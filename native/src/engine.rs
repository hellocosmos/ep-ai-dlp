use crate::{
    audit::Audit,
    inspection::{self, Decision},
};
use anyhow::{Result, bail};
use bytes::Bytes;
use http_body_util::{BodyExt, Full, Limited, combinators::BoxBody};
use hudsucker::certificate_authority::{CertificateAuthority, RcgenAuthority};
use hyper::{
    Request, Response, StatusCode, body::Incoming, http::uri::Authority, service::service_fn,
};
use hyper_util::{
    rt::{TokioExecutor, TokioIo},
    server::conn::auto::Builder,
};
use std::{
    collections::HashSet,
    convert::Infallible,
    io::Cursor,
    net::SocketAddr,
    pin::Pin,
    sync::{Arc, Mutex},
    task::{Context, Poll},
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite},
    sync::{OwnedSemaphorePermit, Semaphore},
};

type BoxError = Box<dyn std::error::Error + Send + Sync>;
pub type ResponseBody = BoxBody<Bytes, BoxError>;
// A streaming response remains an active request until EOF or client cancellation.
// Keep the permit across inspection, upstream headers, and response streaming.
struct GuardedBody {
    body: Incoming,
    _permit: Arc<OwnedSemaphorePermit>,
}
impl hyper::body::Body for GuardedBody {
    type Data = Bytes;
    type Error = hyper::Error;
    fn poll_frame(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<hyper::body::Frame<Bytes>, Self::Error>>> {
        Pin::new(&mut self.body).poll_frame(cx)
    }
    fn is_end_stream(&self) -> bool {
        self.body.is_end_stream()
    }
    fn size_hint(&self) -> hyper::body::SizeHint {
        self.body.size_hint()
    }
}
use crate::upstream::Upstream;
pub const MAX_BODY: usize = 1024 * 1024;
const READ_TIMEOUT: Duration = Duration::from_secs(10);
#[derive(Clone)]
pub struct Engine {
    ca: Arc<RcgenAuthority>,
    tls_config: rustls::ClientConfig,
    audit: Arc<Mutex<Audit>>,
    targets: Arc<HashSet<Authority>>,
    protected_endpoints: Arc<HashSet<SocketAddr>>,
    requests: Arc<Semaphore>,
    application_mode: bool,
    management: Option<Arc<crate::management::Management>>,
}
impl Engine {
    pub fn new(
        ca: RcgenAuthority,
        roots: rustls::RootCertStore,
        audit: Audit,
        targets: Vec<Authority>,
    ) -> Result<Self> {
        let config = rustls::ClientConfig::builder_with_provider(Arc::new(
            rustls::crypto::ring::default_provider(),
        ))
        .with_safe_default_protocol_versions()?
        .with_root_certificates(roots)
        .with_no_client_auth();
        Ok(Self {
            ca: Arc::new(ca),
            tls_config: config,
            audit: Arc::new(Mutex::new(audit)),
            targets: Arc::new(targets.into_iter().collect()),
            protected_endpoints: Arc::new(HashSet::new()),
            requests: Arc::new(Semaphore::new(32)),
            application_mode: false,
            management: None,
        })
    }
    pub fn with_application_mode(mut self, enabled: bool) -> Self { self.application_mode = enabled; self }
    pub fn with_management(
        mut self,
        management: Option<Arc<crate::management::Management>>,
    ) -> Self {
        self.management = management;
        self
    }
    pub fn mark_running(&self) {
        if let Some(manager) = &self.management {
            manager.running();
        }
    }
    pub fn with_protected_endpoints(mut self, endpoints: Vec<SocketAddr>) -> Self {
        self.protected_endpoints = Arc::new(endpoints.into_iter().collect());
        self
    }
    pub async fn serve<S>(&self, mut io: S, destination: SocketAddr) -> Result<()>
    where
        S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
    {
        if self.application_mode && !self.targets.iter().any(|a| a.port_u16() == Some(destination.port())) {
            let mut upstream = tokio::time::timeout(READ_TIMEOUT, tokio::net::TcpStream::connect(destination)).await??;
            tokio::time::timeout(Duration::from_secs(300), tokio::io::copy_bidirectional(&mut io, &mut upstream)).await??;
            return Ok(());
        }
        let (prefix, sni) = tokio::time::timeout(READ_TIMEOUT, client_hello(&mut io)).await??;
        let authority: Authority = format!("{}:{}", sni, destination.port()).parse()?;
        // No SNI is always rejected; there is no unknown-protocol fallback.
        let selected = self.targets.contains(&authority);
        let replay = crate::transport::ReplayStream::new(prefix, io);
        if !selected && !self.application_mode && self.protected_endpoints.contains(&destination) {
            bail!("SNI mismatch on protected endpoint");
        }
        if !selected {
            let mut replay = replay;
            let mut upstream =
                tokio::time::timeout(READ_TIMEOUT, tokio::net::TcpStream::connect(destination))
                    .await??;
            tokio::time::timeout(
                Duration::from_secs(300),
                tokio::io::copy_bidirectional(&mut replay, &mut upstream),
            )
            .await??;
            return Ok(());
        }
        let config = self.ca.gen_server_config(&authority).await;
        let tls = tokio::time::timeout(
            READ_TIMEOUT,
            tokio_rustls::TlsAcceptor::from(config).accept(replay),
        )
        .await??;
        let upstream =
            crate::upstream::client(self.tls_config.clone(), authority.host(), destination);
        let engine = self.clone();
        let mut server = Builder::new(TokioExecutor::new());
        server
            .http1()
            .max_buf_size(32768)
            .timer(hyper_util::rt::TokioTimer::new())
            .header_read_timeout(READ_TIMEOUT);
        server
            .http2()
            .max_concurrent_streams(32)
            .max_header_list_size(32768)
            .max_send_buf_size(65536);
        server.serve_connection(TokioIo::new(tls),service_fn(move |req| {
            let engine=engine.clone();let authority=authority.clone();let upstream=upstream.clone();
            async move {Ok::<_,Infallible>(engine.request(req,authority,upstream).await)}
        })).await.map_err(|_|anyhow::anyhow!("HTTP connection failed"))?;
        Ok(())
    }
    async fn request(
        &self,
        req: Request<Incoming>,
        authority: Authority,
        upstream: Upstream,
    ) -> Response<ResponseBody> {
        let Ok(permit) = self.requests.clone().try_acquire_owned() else {
            return deny("overloaded", None);
        };
        let permit = Arc::new(permit);
        let management = self.management.clone();
        let policy = management.as_ref().and_then(|manager| manager.snapshot());
        let host = authority.host().to_owned();
        let method = req.method().as_str().to_owned();
        let invalid = validate_headers(&req, &authority).is_err();
        let path = req
            .uri()
            .path_and_query()
            .map(|x| x.as_str())
            .unwrap_or("/")
            .to_owned();
        let content_type = req
            .headers()
            .get("content-type")
            .and_then(|x| x.to_str().ok())
            .unwrap_or("")
            .to_owned();
        let (parts, incoming) = req.into_parts();
        let bytes = if invalid {
            None
        } else {
            match tokio::time::timeout(READ_TIMEOUT, Limited::new(incoming, MAX_BODY).collect())
                .await
            {
                Ok(Ok(body)) if body.trailers().is_none() => Some(body.to_bytes()),
                _ => None,
            }
        };
        let audit = self.audit.clone();
        let body = bytes.clone();
        let inspection_permit = permit.clone();
        let job = tokio::task::spawn_blocking(move || {
            let _permit = inspection_permit;
            let decision = match body.as_ref() {
                Some(data) => inspection::inspect(&method, &path, &content_type, data),
                None => Decision {
                    reason: "uninspectable_request",
                    rules: vec![],
                },
            };
            let (decision, action, revision) = if management.is_some() {
                crate::managed_policy::evaluate(
                    decision,
                    policy.as_deref(),
                    crate::managed_policy::now(),
                )
            } else {
                let action = if decision.reason == "clean" {
                    "allow"
                } else {
                    "block"
                };
                (decision, action, 0)
            };
            let id = audit
                .lock()
                .map_err(|_| anyhow::anyhow!("audit unavailable"))?
                .record_with_policy(
                    &host,
                    &method,
                    body.as_ref().map_or(0, |b| b.len()),
                    &decision,
                    management.as_ref().map(|_| (revision, action)),
                )?;
            if let Some(manager) = management {
                manager.record(
                    &host,
                    &method,
                    body.as_ref().map_or(0, |b| b.len()),
                    &decision,
                    action,
                    revision,
                )?;
            }
            Ok::<_, anyhow::Error>((decision, id, action))
        });
        let (decision, id, action) = match tokio::time::timeout(Duration::from_secs(5), job).await {
            Ok(Ok(Ok(result))) => result,
            _ => return deny("inspection_error", None),
        };
        if action == "block" {
            return deny(decision.reason, Some(id));
        }
        let Some(bytes) = bytes else {
            return deny("inspection_error", None);
        };
        let mut uri = parts.uri.clone().into_parts();
        uri.scheme = Some(hyper::http::uri::Scheme::HTTPS);
        uri.authority = Some(authority.clone());
        let Ok(uri) = hyper::Uri::from_parts(uri) else {
            return deny("uninspectable_request", None);
        };
        let mut parts = parts;
        parts.uri = uri;
        // Downstream and upstream negotiate HTTP independently. Hyper upgrades
        // this request when upstream ALPN selects h2; retaining the client h2
        // version would incorrectly reject an HTTP/1.1-only origin.
        parts.version = hyper::Version::HTTP_11;
        strip_hop_headers(&mut parts.headers);
        parts
            .headers
            .insert("host", authority.as_str().parse().unwrap());
        parts
            .headers
            .insert("content-length", bytes.len().to_string().parse().unwrap());
        let outgoing = Request::from_parts(parts, Full::new(bytes));
        match tokio::time::timeout(Duration::from_secs(30), upstream.request(outgoing)).await {
            Ok(Ok(mut response)) => {
                for name in ["x-aidlp-decision", "x-aidlp-reason", "x-aidlp-event-id"] {
                    response.headers_mut().remove(name);
                }
                strip_hop_headers(response.headers_mut());
                response.map(|body| {
                    GuardedBody {
                        body,
                        _permit: permit,
                    }
                    .map_err(|e| Box::new(e) as BoxError)
                    .boxed()
                })
            }
            _ => response(StatusCode::BAD_GATEWAY, "upstream unavailable"),
        }
    }
}
// Kept separate so all validation precedes any upstream activity.
fn validate_headers(req: &Request<Incoming>, authority: &Authority) -> Result<()> {
    if req.method() == hyper::Method::CONNECT
        || req.headers().contains_key("upgrade")
        || req.headers().contains_key("content-encoding")
    {
        bail!("unsupported request");
    }
    for name in ["content-type", "content-length", "host"] {
        if req.headers().get_all(name).iter().nth(1).is_some() {
            bail!("duplicate header");
        }
    }
    if req.headers().get("content-length").is_some_and(|v| {
        v.to_str()
            .ok()
            .and_then(|s| s.parse::<usize>().ok())
            .is_none_or(|n| n > MAX_BODY)
    }) {
        bail!("body limit");
    }
    if let Some(a) = req.uri().authority() {
        if normalized(a) != normalized(authority) {
            bail!("authority mismatch");
        }
    }
    if let Some(value) = req.headers().get("host") {
        let a: Authority = value.to_str()?.parse()?;
        if normalized(&a) != normalized(authority) {
            bail!("host mismatch");
        }
    } else if req.uri().authority().is_none() {
        bail!("missing host");
    }
    if req.uri().scheme_str().is_some_and(|s| s != "https") {
        bail!("invalid scheme");
    }
    Ok(())
}
fn normalized(a: &Authority) -> (String, u16) {
    (a.host().to_ascii_lowercase(), a.port_u16().unwrap_or(443))
}
fn strip_hop_headers(headers: &mut hyper::HeaderMap) {
    if let Some(connection) = headers
        .get("connection")
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned)
    {
        for name in connection.split(',').map(str::trim) {
            headers.remove(name);
        }
    }
    for name in [
        "connection",
        "proxy-connection",
        "keep-alive",
        "proxy-authorization",
        "proxy-authenticate",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    ] {
        headers.remove(name);
    }
}
fn response(status: StatusCode, text: &'static str) -> Response<ResponseBody> {
    Response::builder()
        .status(status)
        .body(
            Full::new(Bytes::from_static(text.as_bytes()))
                .map_err(|never| match never {})
                .boxed(),
        )
        .unwrap()
}
fn deny(reason: &'static str, id: Option<u64>) -> Response<ResponseBody> {
    let mut res = response(StatusCode::FORBIDDEN, "blocked");
    res.headers_mut()
        .insert("x-aidlp-decision", "block".parse().unwrap());
    res.headers_mut()
        .insert("x-aidlp-reason", reason.parse().unwrap());
    if let Some(id) = id {
        res.headers_mut()
            .insert("x-aidlp-event-id", id.to_string().parse().unwrap());
    }
    res
}
/// Consume complete TLS records until rustls has a complete ClientHello.
/// Retain the exact bounded wire prefix for TLS accept or opaque replay.
async fn client_hello<S: AsyncRead + Unpin>(io: &mut S) -> Result<(Vec<u8>, String)> {
    let mut acceptor = rustls::server::Acceptor::default();
    let mut prefix = Vec::new();
    loop {
        let mut header = [0; 5];
        io.read_exact(&mut header).await?;
        if header[0] != 22 || header[1] != 3 {
            bail!("expected TLS handshake");
        }
        let n = u16::from_be_bytes([header[3], header[4]]) as usize;
        if n == 0 || n > 18432 || prefix.len() + 5 + n > 65536 {
            bail!("ClientHello limit");
        }
        let mut record = Vec::with_capacity(5 + n);
        record.extend_from_slice(&header);
        record.resize(5 + n, 0);
        io.read_exact(&mut record[5..]).await?;
        prefix.extend_from_slice(&record);
        acceptor.read_tls(&mut Cursor::new(record))?;
        match acceptor.accept() {
            Ok(Some(accepted)) => {
                let Some(sni) = accepted
                    .client_hello()
                    .server_name()
                    .map(str::to_ascii_lowercase)
                else {
                    bail!("missing SNI");
                };
                if sni.len() > 253
                    || sni.split('.').any(|part| {
                        part.is_empty()
                            || part.len() > 63
                            || !part.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
                    })
                {
                    bail!("invalid SNI");
                }
                return Ok((prefix, sni));
            }
            Ok(None) => {}
            Err(_) => bail!("invalid ClientHello"),
        }
    }
}
