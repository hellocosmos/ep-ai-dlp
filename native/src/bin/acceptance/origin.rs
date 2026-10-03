use anyhow::{Result, ensure};
use bytes::Bytes;
use http_body_util::{BodyExt, Full, combinators::BoxBody};
use hyper::{
    Request, Response,
    body::{Body, Frame, Incoming},
    service::service_fn,
};
use hyper_util::{
    rt::{TokioExecutor, TokioIo},
    server::conn::auto::Builder,
};
use serde_json::{Value, json};
use std::{
    convert::Infallible,
    io::Write,
    net::SocketAddr,
    path::{Path, PathBuf},
    pin::Pin,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicI32, AtomicUsize, Ordering},
    },
    task::{Context, Poll},
    time::Duration,
};
use tokio::{
    net::{TcpListener, UdpSocket},
    sync::mpsc,
    task::JoinHandle,
};
use tokio_rustls::TlsAcceptor;

pub struct ChannelBody(pub mpsc::Receiver<Bytes>);
impl Body for ChannelBody {
    type Data = Bytes;
    type Error = Infallible;
    fn poll_frame(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<Frame<Bytes>, Infallible>>> {
        self.0.poll_recv(cx).map(|v| v.map(|b| Ok(Frame::data(b))))
    }
}
pub fn digest(data: &[u8]) -> String {
    let h = data.iter().fold(0xcbf29ce484222325u64, |h, b| {
        (h ^ (*b as u64)).wrapping_mul(0x100000001b3)
    });
    format!("{h:016x}")
}
#[derive(Clone)]
pub struct Evidence {
    events: Arc<Mutex<Vec<Value>>>,
    tcp_connections: Arc<AtomicUsize>,
    udp_datagrams: Arc<AtomicUsize>,
    udp_receiver_running: Arc<AtomicBool>,
    udp_receive_errors: Arc<AtomicUsize>,
    udp_last_os_error: Arc<AtomicI32>,
    udp_last_error_kind: Arc<Mutex<Option<String>>>,
    file: Arc<Mutex<std::fs::File>>,
}
struct UdpReceiverHealth(Arc<AtomicBool>);
impl Drop for UdpReceiverHealth {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}
impl Evidence {
    pub fn count(&self) -> usize {
        self.events.lock().unwrap().len()
    }
    pub fn tcp_count(&self) -> usize {
        self.tcp_connections.load(Ordering::SeqCst)
    }
    pub fn udp_count(&self) -> usize {
        self.udp_datagrams.load(Ordering::SeqCst)
    }
    pub fn events(&self) -> Vec<Value> {
        self.events.lock().unwrap().clone()
    }
    fn record(&self, event: Value) -> Result<()> {
        let mut f = self.file.lock().unwrap();
        serde_json::to_writer(&mut *f, &event)?;
        f.write_all(b"\n")?;
        f.flush()?;
        self.events.lock().unwrap().push(event);
        Ok(())
    }
}
pub struct Origin {
    pub address: SocketAddr,
    pub ca_path: PathBuf,
    pub evidence: Evidence,
    task: JoinHandle<()>,
    udp_task: JoinHandle<()>,
}
impl Drop for Origin {
    fn drop(&mut self) {
        self.task.abort();
        self.udp_task.abort();
    }
}
impl Origin {
    pub async fn start(listen: SocketAddr, state: &Path) -> Result<Self> {
        Self::start_protocol(listen, state, true).await
    }
    pub async fn start_protocol(listen: SocketAddr, state: &Path, h2: bool) -> Result<Self> {
        std::fs::create_dir_all(state)?;
        let key = rcgen::KeyPair::generate()?;
        let mut params =
            rcgen::CertificateParams::new(vec!["localhost".to_owned(), "aidlp.test".to_owned()])?;
        params.distinguished_name.push(
            rcgen::DnType::CommonName,
            "Controlled AI DLP acceptance origin",
        );
        let cert = params.self_signed(&key)?;
        let ca_path = state.join("origin-ca.pem");
        std::fs::write(&ca_path, cert.pem())?;
        std::fs::write(state.join("origin-key.pem"), key.serialize_pem())?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(
                state.join("origin-key.pem"),
                std::fs::Permissions::from_mode(0o600),
            )?;
        }
        let key = rustls::pki_types::PrivateKeyDer::Pkcs8(
            rustls::pki_types::PrivatePkcs8KeyDer::from(key.serialize_der()),
        );
        let mut config = rustls::ServerConfig::builder()
            .with_no_client_auth()
            .with_single_cert(vec![cert.der().clone()], key)?;
        config.alpn_protocols = if h2 { vec![b"h2".to_vec(), b"http/1.1".to_vec()] } else { vec![b"http/1.1".to_vec()] };
        let acceptor = TlsAcceptor::from(Arc::new(config));
        let listener = TcpListener::bind(listen).await?;
        let address = listener.local_addr()?;
        let udp = UdpSocket::bind(address).await?;
        let file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(state.join("origin-events.jsonl"))?;
        let evidence = Evidence {
            events: Arc::new(Mutex::new(Vec::new())),
            tcp_connections: Arc::new(AtomicUsize::new(0)),
            udp_datagrams: Arc::new(AtomicUsize::new(0)),
            udp_receiver_running: Arc::new(AtomicBool::new(false)),
            udp_receive_errors: Arc::new(AtomicUsize::new(0)),
            udp_last_os_error: Arc::new(AtomicI32::new(0)),
            udp_last_error_kind: Arc::new(Mutex::new(None)),
            file: Arc::new(Mutex::new(file)),
        };
        let events = evidence.clone();
        let udp_events = evidence.clone();
        let udp_task = tokio::spawn(async move {
            let _health = UdpReceiverHealth(udp_events.udp_receiver_running.clone());
            udp_events
                .udp_receiver_running
                .store(true, Ordering::SeqCst);
            // Receive one complete bounded datagram, then immediately erase its
            // bytes. Retain only a count; never log payload, address or length.
            let mut discard = vec![0u8; 65536];
            loop {
                match udp.recv_from(&mut discard).await {
                    Ok((length, _)) => {
                        discard[..length].fill(0);
                        udp_events.udp_datagrams.fetch_add(1, Ordering::SeqCst);
                    }
                    Err(error) => {
                        discard.fill(0);
                        udp_events.udp_receive_errors.fetch_add(1, Ordering::SeqCst);
                        udp_events
                            .udp_last_os_error
                            .store(error.raw_os_error().unwrap_or(0), Ordering::SeqCst);
                        *udp_events.udp_last_error_kind.lock().unwrap() =
                            Some(format!("{:?}", error.kind()));
                        // Keep transient socket errors visible without silently
                        // dropping the receiver or spinning on a permanent one.
                        tokio::time::sleep(Duration::from_millis(100)).await;
                    }
                }
            }
        });
        let task = tokio::spawn(async move {
            let mut tasks = tokio::task::JoinSet::new();
            loop {
                tokio::select! {
                    accepted = listener.accept() => {
                        let Ok((io, _)) = accepted else { break };
                        events.tcp_connections.fetch_add(1, Ordering::SeqCst);
                        let acceptor = acceptor.clone(); let events = events.clone();
                        tasks.spawn(async move {
                            let Ok(Ok(tls)) = tokio::time::timeout(Duration::from_secs(10), acceptor.accept(io)).await else { return };
                            let service = service_fn(move |req| handler(req, events.clone()));
                            let builder = Builder::new(TokioExecutor::new());
                            let builder = if h2 { builder } else { builder.http1_only() };
                            let _ = builder.serve_connection(TokioIo::new(tls), service).await;
                        });
                    },
                    _ = tasks.join_next(), if !tasks.is_empty() => {}
                }
            }
        });
        std::fs::write(
            state.join("ready.json"),
            serde_json::to_vec_pretty(
                &json!({"ready":true,"listen":address,"ca":ca_path,"sni":"localhost","supports":["http/1.1","h2","udp-counter"],"metadata_only":true}),
            )?,
        )?;
        ensure!(address.port() != 0, "origin address missing");
        Ok(Self {
            address,
            ca_path,
            evidence,
            task,
            udp_task,
        })
    }
}
async fn handler(
    req: Request<Incoming>,
    evidence: Evidence,
) -> Result<Response<BoxBody<Bytes, Infallible>>, Infallible> {
    let path = req.uri().path().to_owned();
    let version = format!("{:?}", req.version());
    if path == "/__aidlp_stats" {
        let os_error = evidence.udp_last_os_error.load(Ordering::SeqCst);
        let body = serde_json::to_vec(&json!({
            "total_requests":evidence.count(),
            "total_tcp_connections":evidence.tcp_count(),
            "total_udp_datagrams":evidence.udp_count(),
            "udp_receiver_running":evidence.udp_receiver_running.load(Ordering::SeqCst),
            "udp_receive_errors":evidence.udp_receive_errors.load(Ordering::SeqCst),
            "udp_last_os_error":if os_error == 0 {None} else {Some(os_error)},
            "udp_last_error_kind":evidence.udp_last_error_kind.lock().unwrap().clone()
        }))
        .unwrap();
        return Ok(Response::builder()
            .header("content-type", "application/json")
            .body(Full::new(Bytes::from(body)).boxed())
            .unwrap());
    }
    let method = req.method().to_string();
    let body = match req.into_body().collect().await {
        Ok(b) => b.to_bytes(),
        Err(_) => {
            return Ok(Response::builder()
                .status(400)
                .body(Full::new(Bytes::new()).boxed())
                .unwrap());
        }
    };
    // Never store the raw path/query: keep the controlled case id only.
    let case_id = path
        .strip_prefix('/')
        .unwrap_or("")
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '/'))
        .take(96)
        .collect::<String>();
    if evidence.record(json!({"case_id":case_id,"method":method,"protocol":version,"body_len":body.len(),"body_fnv1a64":digest(&body)})).is_err() {
        return Ok(Response::builder().status(500).body(Full::new(Bytes::new()).boxed()).unwrap());
    }
    if path.starts_with("/sse") || path.starts_with("/hold") {
        let (tx, rx) = mpsc::channel(1);
        let hold = path.starts_with("/hold");
        tokio::spawn(async move {
            for i in 0..if hold { 60 } else { 4 } {
                if i > 0 {
                    tokio::time::sleep(Duration::from_millis(if hold { 1000 } else { 250 })).await;
                }
                if tx
                    .send(Bytes::from(format!("data: safe-event-{i}\n\n")))
                    .await
                    .is_err()
                {
                    break;
                }
            }
        });
        return Ok(Response::builder()
            .header("content-type", "text/event-stream")
            .body(ChannelBody(rx).boxed())
            .unwrap());
    }
    Ok(Response::builder()
        .header("content-type", "text/plain")
        .header("x-origin-protocol", version)
        .body(Full::new(Bytes::from_static(b"safe-origin-response")).boxed())
        .unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn udp_control_smoke_metadata_and_raii_cleanup() -> Result<()> {
        rustls::crypto::ring::default_provider()
            .install_default()
            .ok();
        let state = tempfile::tempdir()?;
        let ip = std::env::var("AIDLP_ACCEPTANCE_ORIGIN_TEST_IP")
            .unwrap_or_else(|_| "127.0.0.1".to_owned());
        let origin = Origin::start(format!("{ip}:0").parse()?, state.path()).await?;
        let address = origin.address;
        let sender = UdpSocket::bind(format!("{ip}:0")).await?;
        for payload in [
            Vec::new(),
            b"synthetic-udp-marker".to_vec(),
            vec![0x5a; 2048],
        ] {
            sender.send_to(&payload, address).await?;
        }
        tokio::time::timeout(Duration::from_secs(2), async {
            while origin.evidence.udp_count() < 3 {
                tokio::task::yield_now().await;
            }
        })
        .await?;
        let client = crate::client::Client::new(
            address,
            format!("aidlp.test:{}", address.port()),
            &origin.ca_path,
        )?;
        let mut reply = client
            .request(
                false,
                false,
                "GET",
                "/__aidlp_stats",
                &[],
                crate::client::full(Vec::new()),
            )
            .await?;
        ensure!(reply.response.status() == 200, "stats status");
        let (bytes, _) = reply.collect().await?;
        let stats: Value = serde_json::from_slice(&bytes)?;
        ensure!(stats["total_udp_datagrams"] == 3, "UDP stats mismatch");
        ensure!(
            stats["udp_receiver_running"] == true && stats["udp_receive_errors"] == 0,
            "UDP receiver unhealthy"
        );
        ensure!(
            stats["total_requests"] == 0 && stats["total_tcp_connections"] == 1,
            "stats metadata mismatch"
        );
        ensure!(
            std::fs::metadata(state.path().join("origin-events.jsonl"))?.len() == 0,
            "UDP or stats retained content"
        );
        drop(reply);
        drop(origin);
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if let Ok(rebound) = UdpSocket::bind(address).await {
                    drop(rebound);
                    break;
                }
                tokio::task::yield_now().await;
            }
        })
        .await?;
        Ok(())
    }

    #[tokio::test]
    async fn udp_receiver_survives_bursts_idle_and_spaced_datagrams() -> Result<()> {
        rustls::crypto::ring::default_provider()
            .install_default()
            .ok();
        let state = tempfile::tempdir()?;
        let ip = std::env::var("AIDLP_ACCEPTANCE_ORIGIN_TEST_IP")
            .unwrap_or_else(|_| "127.0.0.1".to_owned());
        let origin = Origin::start(format!("{ip}:0").parse()?, state.path()).await?;
        let address = origin.address;
        let sender = UdpSocket::bind(format!("{ip}:0")).await?;
        let client = crate::client::Client::new(
            address,
            format!("aidlp.test:{}", address.port()),
            &origin.ca_path,
        )?;
        let mut expected = 0;
        for round in 0..4 {
            // Each burst includes empty, ordinary and 8KiB datagrams. Darwin's
            // default UDP send-space rejects a 65507-byte test payload.
            for n in 0..8 {
                let payload = match n {
                    0 => Vec::new(),
                    1 => vec![0x5a; 8192],
                    _ => b"discard-synthetic-udp-payload".to_vec(),
                };
                sender.send_to(&payload, address).await?;
                expected += 1;
            }
            tokio::time::timeout(Duration::from_secs(2), async {
                while origin.evidence.udp_count() < expected {
                    tokio::task::yield_now().await;
                }
            })
            .await?;
            // A later datagram must still work after an idle period. This catches
            // a receiver that accepted an initial packet then silently exited.
            for _ in 0..4 {
                tokio::time::sleep(Duration::from_millis(80)).await;
                sender
                    .send_to(b"spaced-synthetic-datagram", address)
                    .await?;
                expected += 1;
                tokio::time::timeout(Duration::from_secs(2), async {
                    while origin.evidence.udp_count() < expected {
                        tokio::task::yield_now().await;
                    }
                })
                .await?;
            }
            let mut response = client
                .request(
                    false,
                    false,
                    "GET",
                    "/__aidlp_stats",
                    &[],
                    crate::client::full(Vec::new()),
                )
                .await?;
            let (body, _) = response.collect().await?;
            let stats: Value = serde_json::from_slice(&body)?;
            ensure!(
                stats["total_udp_datagrams"] == expected,
                "round {round} datagram count mismatch"
            );
            ensure!(
                stats["udp_receiver_running"] == true && stats["udp_receive_errors"] == 0,
                "round {round} receiver unhealthy"
            );
            ensure!(
                stats["udp_last_os_error"].is_null() && stats["udp_last_error_kind"].is_null(),
                "unexpected UDP diagnostics"
            );
            ensure!(
                UdpSocket::bind(address).await.is_err(),
                "live origin lost UDP socket"
            );
        }
        ensure!(
            expected == 48 && origin.evidence.count() == 0,
            "UDP regression accounting"
        );
        ensure!(
            std::fs::metadata(state.path().join("origin-events.jsonl"))?.len() == 0,
            "UDP content logged"
        );
        Ok(())
    }
}
