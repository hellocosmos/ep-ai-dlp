//! Resolve upstream connections to the captured destination, preserving TLS SNI.
use bytes::Bytes;
use http_body_util::Full;
use hyper_rustls::{HttpsConnector, HttpsConnectorBuilder};
use hyper_util::{
    client::legacy::{
        Client,
        connect::{HttpConnector, dns::Name},
    },
    rt::TokioExecutor,
};
use std::{
    future::{Ready, ready},
    io,
    net::SocketAddr,
    sync::Arc,
    task::{Context, Poll},
};
use tower_service::Service;
#[derive(Clone)]
pub struct CapturedResolver {
    host: Arc<str>,
    destination: SocketAddr,
}
impl Service<Name> for CapturedResolver {
    type Response = std::vec::IntoIter<SocketAddr>;
    type Error = io::Error;
    type Future = Ready<Result<Self::Response, Self::Error>>;
    fn poll_ready(&mut self, _: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        Poll::Ready(Ok(()))
    }
    fn call(&mut self, name: Name) -> Self::Future {
        ready(if name.as_str().eq_ignore_ascii_case(&self.host) {
            Ok(vec![self.destination].into_iter())
        } else {
            Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "upstream host mismatch",
            ))
        })
    }
}
pub type Upstream = Client<HttpsConnector<HttpConnector<CapturedResolver>>, Full<Bytes>>;
pub fn client(config: rustls::ClientConfig, host: &str, destination: SocketAddr) -> Upstream {
    let mut http = HttpConnector::new_with_resolver(CapturedResolver {
        host: Arc::from(host),
        destination,
    });
    http.enforce_http(false);
    http.set_connect_timeout(Some(std::time::Duration::from_secs(10)));
    let connector = HttpsConnectorBuilder::new()
        .with_tls_config(config)
        .https_only()
        .enable_http1()
        .enable_http2()
        .wrap_connector(http);
    let mut builder = Client::builder(TokioExecutor::new());
    builder
        .pool_max_idle_per_host(1)
        .pool_idle_timeout(std::time::Duration::from_secs(30));
    builder.build(connector)
}
