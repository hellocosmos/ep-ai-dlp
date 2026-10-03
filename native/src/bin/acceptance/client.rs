use anyhow::{Result, ensure};
use bytes::Bytes;
use http_body_util::{BodyExt, Full, combinators::BoxBody};
use hyper::{Request, Response, body::Incoming};
use hyper_util::rt::{TokioExecutor, TokioIo};
use std::{
    convert::Infallible,
    io,
    net::SocketAddr,
    path::Path,
    pin::Pin,
    sync::Arc,
    task::{Context, Poll},
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncRead, AsyncWrite, ReadBuf},
    net::TcpStream,
    task::JoinHandle,
};
use tokio_rustls::{TlsConnector, client::TlsStream};

pub type RequestBody = BoxBody<Bytes, Infallible>;
pub fn full(data: Vec<u8>) -> RequestBody {
    Full::new(Bytes::from(data)).boxed()
}
#[derive(Clone)]
pub struct Client {
    pub address: SocketAddr,
    pub authority: String,
    pub config: Arc<rustls::ClientConfig>,
}
impl Client {
    pub fn new(address: SocketAddr, authority: String, ca: &Path) -> Result<Self> {
        let pem = std::fs::read(ca)?;
        let mut roots = rustls::RootCertStore::empty();
        for cert in rustls_pemfile::certs(&mut &pem[..]) {
            roots.add(cert?)?;
        }
        let config = rustls::ClientConfig::builder()
            .with_root_certificates(roots)
            .with_no_client_auth();
        Ok(Self {
            address,
            authority,
            config: Arc::new(config),
        })
    }
    pub async fn tls(&self, sni: &str, h2: bool, fragment: bool) -> Result<TlsStream<FragmentIo>> {
        let mut config = (*self.config).clone();
        config.alpn_protocols = vec![if h2 {
            b"h2".to_vec()
        } else {
            b"http/1.1".to_vec()
        }];
        let tcp = TcpStream::connect(self.address).await?;
        tcp.set_nodelay(true)?;
        let tls = TlsConnector::from(Arc::new(config))
            .connect(
                rustls::pki_types::ServerName::try_from(sni.to_owned())?,
                FragmentIo::new(tcp, fragment),
            )
            .await?;
        ensure!(
            tls.get_ref().1.alpn_protocol() == Some(if h2 { &b"h2"[..] } else { &b"http/1.1"[..] }),
            "unexpected ALPN"
        );
        Ok(tls)
    }
    pub async fn request(
        &self,
        h2: bool,
        fragment: bool,
        method: &str,
        path: &str,
        headers: &[(&str, &str)],
        body: RequestBody,
    ) -> Result<Reply> {
        let authority: hyper::http::uri::Authority = self.authority.parse()?;
        let tls = self.tls(authority.host(), h2, fragment).await?;
        let uri = if h2 {
            format!(
                "https://{}/{}",
                self.authority,
                path.trim_start_matches('/')
            )
        } else {
            path.to_owned()
        };
        let mut req = Request::builder()
            .method(method)
            .uri(uri)
            .header("host", &self.authority)
            .body(body)?;
        for (key, value) in headers {
            req.headers_mut().append(
                hyper::header::HeaderName::from_bytes(key.as_bytes())?,
                value.parse()?,
            );
        }
        let start = Instant::now();
        if h2 {
            let (mut sender, connection) =
                hyper::client::conn::http2::handshake(TokioExecutor::new(), TokioIo::new(tls))
                    .await?;
            let task = tokio::spawn(async move {
                let _ = connection.await;
            });
            let res = sender.send_request(req).await;
            match res {
                Ok(response) => Ok(Reply {
                    response,
                    task,
                    start,
                }),
                Err(e) => {
                    task.abort();
                    Err(e.into())
                }
            }
        } else {
            let (mut sender, connection) =
                hyper::client::conn::http1::handshake(TokioIo::new(tls)).await?;
            let task = tokio::spawn(async move {
                let _ = connection.await;
            });
            let res = sender.send_request(req).await;
            match res {
                Ok(response) => Ok(Reply {
                    response,
                    task,
                    start,
                }),
                Err(e) => {
                    task.abort();
                    Err(e.into())
                }
            }
        }
    }
}
pub struct Reply {
    pub response: Response<Incoming>,
    task: JoinHandle<()>,
    pub start: Instant,
}
impl Reply {
    pub async fn collect(&mut self) -> Result<(Vec<u8>, Vec<u128>)> {
        let mut bytes = Vec::new();
        let mut times = Vec::new();
        while let Some(frame) = self.response.body_mut().frame().await {
            if let Ok(data) = frame?.into_data() {
                bytes.extend_from_slice(&data);
                times.push(self.start.elapsed().as_millis());
            }
        }
        Ok((bytes, times))
    }
}
impl Drop for Reply {
    fn drop(&mut self) {
        self.task.abort();
    }
}
// Split the first ClientHello into separate TLS handshake records, then write
// the transformed wire buffer in <= 7-byte TCP writes. Preserve handshake bytes.
pub struct FragmentIo {
    io: TcpStream,
    first: bool,
    buffered: Vec<u8>,
    pos: usize,
    original: usize,
}
impl FragmentIo {
    fn new(io: TcpStream, fragment: bool) -> Self {
        Self {
            io,
            first: fragment,
            buffered: Vec::new(),
            pos: 0,
            original: 0,
        }
    }
}
impl AsyncRead for FragmentIo {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        Pin::new(&mut self.io).poll_read(cx, buf)
    }
}
impl AsyncWrite for FragmentIo {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        if !self.first && self.buffered.is_empty() {
            return Pin::new(&mut self.io).poll_write(cx, buf);
        }
        if self.buffered.is_empty() {
            if buf.len() < 5 || buf[0] != 22 {
                return Poll::Ready(Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "expected ClientHello record",
                )));
            }
            let n = u16::from_be_bytes([buf[3], buf[4]]) as usize;
            if buf.len() < n + 5 {
                return Poll::Ready(Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "incomplete outgoing TLS record",
                )));
            }
            for chunk in buf[5..5 + n].chunks(47) {
                self.buffered.extend_from_slice(&[
                    22,
                    buf[1],
                    buf[2],
                    (chunk.len() >> 8) as u8,
                    chunk.len() as u8,
                ]);
                self.buffered.extend_from_slice(chunk);
            }
            self.buffered.extend_from_slice(&buf[n + 5..]);
            self.original = buf.len();
            self.first = false;
        }
        while self.pos < self.buffered.len() {
            let end = (self.pos + 7).min(self.buffered.len());
            let piece = self.buffered[self.pos..end].to_vec();
            match Pin::new(&mut self.io).poll_write(cx, &piece) {
                Poll::Pending => return Poll::Pending,
                Poll::Ready(Err(e)) => return Poll::Ready(Err(e)),
                Poll::Ready(Ok(0)) => return Poll::Ready(Err(io::ErrorKind::WriteZero.into())),
                Poll::Ready(Ok(n)) => self.pos += n,
            }
        }
        let n = self.original;
        self.buffered.clear();
        self.pos = 0;
        Poll::Ready(Ok(n))
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.io).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.io).poll_shutdown(cx)
    }
}

pub async fn raw_headers(client: &Client, header: &str) -> Result<(u16, String)> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let authority: hyper::http::uri::Authority = client.authority.parse()?;
    let mut tls = client.tls(authority.host(), false, false).await?;
    tls.write_all(header.as_bytes()).await?;
    let mut buf = Vec::new();
    let mut byte = [0];
    loop {
        if tls.read(&mut byte).await? == 0 {
            break;
        }
        buf.push(byte[0]);
        if buf.ends_with(b"\r\n\r\n") || buf.len() > 32768 {
            break;
        }
    }
    let text = String::from_utf8(buf)?;
    let code = text.split_whitespace().nth(1).unwrap_or("0").parse()?;
    Ok((code, text))
}
pub async fn timeout_request(f: impl std::future::Future<Output = Result<Reply>>) -> Result<Reply> {
    tokio::time::timeout(Duration::from_secs(20), f).await?
}
