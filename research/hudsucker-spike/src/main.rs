// Throwaway evaluation adapter; external access requires --live-chatgpt.
use hudsucker::{*, certificate_authority::RcgenAuthority, rcgen::{CertificateParams, IsCa, BasicConstraints, Issuer, KeyPair}};
use hudsucker::hyper::{Request, Response, Method, StatusCode};
use http_body_util::{BodyExt, Limited};
use std::{sync::{Arc, atomic::{AtomicU64, Ordering}}, time::Duration, path::PathBuf, process::Stdio};
use tokio::{io::{AsyncWriteExt, AsyncBufReadExt, BufReader}, process::{Child, ChildStdin, ChildStdout, Command}, sync::Mutex};
use serde_json::{json, Value};

struct Worker { child: Child, input: ChildStdin, output: BufReader<ChildStdout> }
#[derive(Clone)]
struct Handler { worker: Arc<Mutex<Option<Worker>>>, id: Arc<AtomicU64>, delay: u64, live_chatgpt: bool }
const DECISION_HEADER: &str = "x-aidlp-decision";
const REASON_HEADER: &str = "x-aidlp-reason";
const EVENT_ID_HEADER: &str = "x-aidlp-event-id";

#[derive(Debug, PartialEq, Eq)]
enum InspectionDecision {
    Allow,
    Block { reason: &'static str, event_id: Option<u64> },
}
impl InspectionDecision {
    fn failed() -> Self { Self::Block { reason: "inspection_error", event_id: None } }
}
fn inspection_reply(v: &Value, id: &str) -> std::io::Result<InspectionDecision> {
    if v["id"].as_str()!=Some(id) { return Err(std::io::ErrorKind::InvalidData.into()) }
    let event_id = v["event_id"].as_u64().filter(|id| *id>0);
    match (v["action"].as_str(), v["reason"].as_str(), event_id) {
        (Some("allow"), Some("clean"), Some(_)) => Ok(InspectionDecision::Allow),
        (Some("block"), Some("sensitive_data"), Some(id)) =>
            Ok(InspectionDecision::Block { reason: "sensitive_data", event_id: Some(id) }),
        (Some("block"), Some("uninspectable_request"), Some(id)) =>
            Ok(InspectionDecision::Block { reason: "uninspectable_request", event_id: Some(id) }),
        // Failure metadata never asserts that a database event was committed.
        (Some("block"), Some("inspection_error"), _) => Ok(InspectionDecision::failed()),
        _ => Err(std::io::ErrorKind::InvalidData.into()),
    }
}
fn deny(reason: &'static str, event_id: Option<u64>) -> RequestOrResponse {
    let mut response = Response::builder().status(StatusCode::FORBIDDEN)
        .header(DECISION_HEADER, "block").header(REASON_HEADER, reason);
    if let Some(id) = event_id.filter(|id| *id>0) {
        response = response.header(EVENT_ID_HEADER, id.to_string());
    }
    response.body(Body::from("blocked")).unwrap().into()
}
fn strip_origin_diagnostics(response: &mut Response<Body>) {
    // These names identify local decisions only. Never trust origin-provided
    // values or add them to an upstream request; leave its body streaming.
    for name in [DECISION_HEADER, REASON_HEADER, EVENT_ID_HEADER] {
        response.headers_mut().remove(name);
    }
}
fn duplicate_content_type(request: &Request<Body>) -> bool {
    request.headers().get_all("content-type").iter().nth(1).is_some()
}
fn selected(host: &str) -> bool { host.trim_end_matches('.').eq_ignore_ascii_case("localhost") }
fn live_target(host: &str, port: Option<u16>, enabled: bool) -> bool {
    enabled && host.eq_ignore_ascii_case("chatgpt.com") && port.unwrap_or(443)==443
}
impl Handler {
    async fn inspect(&self, req: &Request<Body>, text: &str) -> InspectionDecision {
        let id = self.id.fetch_add(1, Ordering::Relaxed).to_string();
        let msg = json!({"id":id,"host":req.uri().host().unwrap_or(""),"method":req.method().as_str(),
            "path":req.uri().path_and_query().map(|p|p.as_str()).unwrap_or("/"),
            "content_type":req.headers().get("content-type").and_then(|v|v.to_str().ok()).unwrap_or(""), "body":text});
        let deadline = tokio::time::Instant::now() + Duration::from_secs(5);
        let Ok(mut slot) = tokio::time::timeout_at(deadline, self.worker.lock()).await else { return InspectionDecision::failed() };
        let Some(worker) = slot.as_mut() else { return InspectionDecision::failed() };
        let result = tokio::time::timeout_at(deadline, async {
            tokio::time::sleep(Duration::from_millis(self.delay)).await;
            worker.input.write_all((msg.to_string()+"\n").as_bytes()).await?;
            worker.input.flush().await?;
            let mut line = Vec::new();
            loop {
                let buffer = worker.output.fill_buf().await?;
                if buffer.is_empty() { return Err(std::io::ErrorKind::UnexpectedEof.into()) }
                let n = buffer.iter().position(|b|*b==b'\n').map(|i|i+1).unwrap_or(buffer.len());
                if line.len()+n>8192 { return Err(std::io::ErrorKind::InvalidData.into()) }
                let done = buffer[n-1]==b'\n'; line.extend_from_slice(&buffer[..n]); worker.output.consume(n);
                if done { break }
            }
            let v: Value = serde_json::from_slice(&line).map_err(|_|std::io::Error::from(std::io::ErrorKind::InvalidData))?;
            inspection_reply(&v, &id)
        }).await;
        match result { Ok(Ok(decision))=>decision, _=>{ if let Some(mut w)=slot.take(){let _=w.child.kill().await;} InspectionDecision::failed() } }
    }
}
impl HttpHandler for Handler {
    async fn should_intercept_connect(&mut self, _: &HttpContext, req: &Request<Body>) -> bool {
        req.uri().host().is_some_and(|h| selected(h) || live_target(h, req.uri().port_u16(), self.live_chatgpt))
    }
    async fn handle_request(&mut self, _: &HttpContext, mut req: Request<Body>) -> RequestOrResponse {
        let host = req.uri().host().unwrap_or("");
        // Only the explicit live mode permits the exact ChatGPT HTTPS authority.
        let inspected = selected(host) || live_target(host, req.uri().port_u16(), self.live_chatgpt);
        if !(inspected || host=="127.0.0.1") { return deny("destination_not_allowed", None) }
        if req.method()==Method::CONNECT { return req.into() }
        if !inspected || req.uri().scheme_str()!=Some("https") || req.headers().contains_key("upgrade")
           || req.headers().contains_key("content-encoding") || duplicate_content_type(&req) { return deny("uninspectable_request", None) }
        if req.headers().get("content-length").is_some_and(|v|v.to_str().ok().and_then(|s|s.parse::<u64>().ok()).unwrap_or(u64::MAX)>1048576) { return deny("uninspectable_request", None) }
        let incoming = std::mem::replace(req.body_mut(), Body::empty());
        let Ok(Ok(collected)) = tokio::time::timeout(Duration::from_secs(10), Limited::new(incoming,1048576).collect()).await else { return deny("uninspectable_request", None) };
        let bytes=collected.to_bytes();
        let Ok(text)=std::str::from_utf8(&bytes) else { return deny("uninspectable_request", None) };
        if let InspectionDecision::Block { reason, event_id } = self.inspect(&req,text).await { return deny(reason, event_id) }
        *req.body_mut()=Body::from(bytes); req.into()
    }
    async fn handle_response(&mut self, _: &HttpContext, mut response: Response<Body>) -> Response<Body> {
        strip_origin_diagnostics(&mut response);
        response
    }
}
fn arg(args: &[String], key: &str) -> String { args.iter().position(|s|s==key).and_then(|i|args.get(i+1)).expect("missing argument").clone() }
fn main() -> Result<(), Box<dyn std::error::Error>> {
    tokio::runtime::Builder::new_current_thread().enable_all().build()?.block_on(run())
}
async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args:Vec<String>=std::env::args().collect();
    let state=PathBuf::from(arg(&args,"--state")); std::fs::create_dir_all(&state)?;
    let mut cmd=Command::new(arg(&args,"--python"));
    cmd.args(["-u","-m","aidlp.worker","--db"]).arg(state.join("events.db"))
       .current_dir(arg(&args,"--agent")).env("PYTHONPATH",arg(&args,"--agent"))
       .env("PYTHONIOENCODING","utf-8").env("PYTHONDONTWRITEBYTECODE","1")
       .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
    let mut child=cmd.spawn()?;
    let worker=Worker{input:child.stdin.take().unwrap(),output:BufReader::new(child.stdout.take().unwrap()),child};
    let shared=Arc::new(Mutex::new(Some(worker)));
    let handler=Handler{worker:shared.clone(), id:Arc::new(AtomicU64::new(1)),delay:arg(&args,"--delay-ms").parse()?,live_chatgpt:args.iter().any(|a|a=="--live-chatgpt")};
    let key=KeyPair::generate()?; let mut params=CertificateParams::default();
    params.is_ca=IsCa::Ca(BasicConstraints::Unconstrained);
    params.key_usages=vec![rcgen::KeyUsagePurpose::KeyCertSign,rcgen::KeyUsagePurpose::CrlSign];
    let cert=params.self_signed(&key)?; std::fs::write(state.join("proxy-ca.pem"),cert.pem())?;
    let issuer=Issuer::from_ca_cert_pem(&cert.pem(),key)?;
    let ca=RcgenAuthority::new(issuer,32,rustls::crypto::ring::default_provider());
    let mut roots=rustls::RootCertStore::empty();
    let data=std::fs::read(arg(&args,"--origin-ca"))?;
    for cert in rustls_pemfile::certs(&mut &data[..]) { roots.add(cert?)?; }
    let config=rustls::ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()?.with_root_certificates(roots).with_no_client_auth();
    let connector=hyper_rustls::HttpsConnectorBuilder::new().with_tls_config(config).https_only().enable_http1().enable_http2().build();
    let listener=tokio::net::TcpListener::bind("127.0.0.1:0").await?; let port=listener.local_addr()?.port();
    let proxy=Proxy::builder().with_listener(listener).with_ca(ca).with_http_connector(connector)
        .with_http_handler(handler).with_graceful_shutdown(async { let mut s=String::new(); let _=BufReader::new(tokio::io::stdin()).read_line(&mut s).await; })
        .build()?;
    println!("{}",json!({"ready":true,"port":port}));
    let result=proxy.start().await;
    if let Some(mut w)=shared.lock().await.take(){let _=w.child.kill().await;}
    result?; Ok(())
}

#[cfg(test)]
mod policy_tests {
    use super::*;
    #[test]
    fn external_access_is_opt_in() { assert!(!live_target("chatgpt.com", Some(443), false)); }
    #[test]
    fn live_authority_is_exact() {
        assert!(live_target("chatgpt.com", Some(443), true));
        for host in ["chatgpt.com.evil.test", "evilchatgpt.com", "api.openai.com", "127.0.0.1"] {
            assert!(!live_target(host, Some(443), true));
        }
        assert!(!live_target("chatgpt.com", Some(8443), true));
    }
    #[test]
    fn duplicate_content_type_fields_are_rejected_even_when_equal() {
        for second in ["application/json", "text/plain"] {
            let request = Request::builder().uri("https://localhost/")
                .header("Content-Type", "application/json").header("content-type", second)
                .body(Body::empty()).unwrap();
            assert!(duplicate_content_type(&request));
        }
    }
    #[test]
    fn single_content_type_remains_eligible_for_body_inspection() {
        let request = Request::builder().uri("https://localhost/")
            .header("content-type", "application/json; charset=utf-8")
            .body(Body::empty()).unwrap();
        assert!(!duplicate_content_type(&request));
        assert!(!duplicate_content_type(&Request::new(Body::empty())));
    }
    #[test]
    fn committed_worker_block_preserves_correlation() {
        for reason in ["sensitive_data", "uninspectable_request"] {
            let decision = inspection_reply(&json!({"id":"7","action":"block","reason":reason,"event_id":41}), "7").unwrap();
            assert_eq!(decision, InspectionDecision::Block { reason, event_id: Some(41) });
        }
    }
    #[test]
    fn invalid_worker_metadata_never_authorizes_or_reflects_data() {
        for reply in [
            json!({"id":"wrong","action":"allow","reason":"clean","event_id":41}),
            json!({"id":"7","action":"allow","reason":"clean","event_id":null}),
            json!({"id":"7","action":"allow","reason":"clean","event_id":0}),
            json!({"id":"7","action":"block","reason":"sensitive_data","event_id":null}),
            json!({"id":"7","action":"block","reason":"uninspectable_request","event_id":"41"}),
            json!({"id":"7","action":"block","reason":"SYNTHETIC_PRIVATE_VALUE","event_id":41}),
        ] {
            assert!(inspection_reply(&reply, "7").is_err());
        }
    }
    #[test]
    fn clean_allow_still_requires_committed_event() {
        let decision = inspection_reply(&json!({"id":"7","action":"allow","reason":"clean","event_id":41}), "7").unwrap();
        assert_eq!(decision, InspectionDecision::Allow);
    }
    #[test]
    fn worker_failure_does_not_claim_database_correlation() {
        for event_id in [json!(null), json!(41)] {
            let decision = inspection_reply(&json!({"id":"7","action":"block","reason":"inspection_error","event_id":event_id}), "7").unwrap();
            assert_eq!(decision, InspectionDecision::failed());
        }
    }
    #[tokio::test]
    async fn absent_worker_fails_closed_without_event_id() {
        let handler = Handler { worker: Arc::new(Mutex::new(None)), id: Arc::new(AtomicU64::new(1)), delay: 0, live_chatgpt: false };
        let request = Request::builder().uri("https://localhost/").body(Body::empty()).unwrap();
        assert_eq!(handler.inspect(&request, "safe").await, InspectionDecision::failed());
    }
    #[tokio::test]
    async fn local_deny_exposes_only_fixed_decision_and_event_id() {
        let RequestOrResponse::Response(response) = deny("sensitive_data", Some(41)) else { panic!("denial forwarded request") };
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        assert_eq!(response.headers()[DECISION_HEADER], "block");
        assert_eq!(response.headers()[REASON_HEADER], "sensitive_data");
        assert_eq!(response.headers()[EVENT_ID_HEADER], "41");
        assert_eq!(response.into_body().collect().await.unwrap().to_bytes().as_ref(), b"blocked");
        for event_id in [None, Some(0)] {
            let RequestOrResponse::Response(response) = deny("inspection_error", event_id) else { panic!("denial forwarded request") };
            assert_eq!(response.headers()[REASON_HEADER], "inspection_error");
            assert!(!response.headers().contains_key(EVENT_ID_HEADER));
        }
    }
    #[tokio::test]
    async fn upstream_cannot_spoof_local_decision_headers() {
        let mut response = Response::builder().status(StatusCode::FORBIDDEN)
            .header("X-AIDLP-Decision", "block").header("X-AIDLP-Reason", "sensitive_data")
            .header("X-AIDLP-Event-Id", "41").header("content-type", "text/plain")
            .body(Body::from("origin response")).unwrap();
        response.headers_mut().append(EVENT_ID_HEADER, "42".parse().unwrap());
        strip_origin_diagnostics(&mut response);
        for name in [DECISION_HEADER, REASON_HEADER, EVENT_ID_HEADER] {
            assert!(!response.headers().contains_key(name));
        }
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        assert_eq!(response.headers()["content-type"], "text/plain");
        assert_eq!(response.into_body().collect().await.unwrap().to_bytes().as_ref(), b"origin response");
    }
}
