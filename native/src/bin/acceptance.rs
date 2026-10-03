//! Real Rust TLS/HTTP subprocess acceptance. Never stores raw request content.
#[path = "acceptance/client.rs"]
mod client;
#[path = "acceptance/origin.rs"]
mod origin;
use anyhow::{Context, Result, ensure};
use client::{Client, full};
use http_body_util::BodyExt;
use origin::Origin;
use serde_json::{Value, json};
use std::{
    future::Future,
    net::SocketAddr,
    path::{Path, PathBuf},
    pin::Pin,
    process::Stdio,
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::{Child, Command},
};

struct Runtime {
    child: Child,
    client: Client,
    state: PathBuf,
}
impl Runtime {
    async fn start(
        proxy: &Path,
        origin: &Origin,
        dir: &Path,
        trusted: bool,
        quota: Option<u64>,
    ) -> Result<Self> {
        std::fs::create_dir_all(dir)?;
        let state = dir.join("state");
        let ca = if trusted {
            origin.ca_path.clone()
        } else {
            let key = rcgen::KeyPair::generate()?;
            let cert =
                rcgen::CertificateParams::new(vec!["localhost".to_owned()])?.self_signed(&key)?;
            let path = dir.join("unrelated-origin-ca.pem");
            std::fs::write(&path, cert.pem())?;
            path
        };
        let mut config = json!({"state_dir":state,"origin_ca":ca,"targets":[format!("localhost:{}",origin.address.port())],"protected_endpoints":[origin.address],"mode":{"kind":"fixture","listen":"127.0.0.1:0","destination":origin.address}});
        if let Some(n) = quota {
            config["audit_max_bytes"] = json!(n);
        }
        let path = dir.join("config.json");
        std::fs::write(&path, serde_json::to_vec_pretty(&config)?)?;
        let stderr = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(dir.join("runtime-stderr.log"))?;
        let mut child = Command::new(proxy)
            .arg("--config")
            .arg(&path)
            .stdout(Stdio::piped())
            .stderr(stderr)
            .kill_on_drop(true)
            .spawn()?;
        let mut reader = BufReader::new(child.stdout.take().context("missing stdout")?).lines();
        let line = tokio::time::timeout(Duration::from_secs(15), reader.next_line())
            .await??
            .context("runtime exited before ready")?;
        let ready: Value = serde_json::from_str(&line)?;
        ensure!(ready["ready"] == true, "runtime not ready");
        std::fs::write(
            dir.join("runtime-ready.json"),
            serde_json::to_vec_pretty(&ready)?,
        )?;
        // Drain stdout so a verbose future runtime cannot block on its pipe.
        tokio::spawn(async move { while let Ok(Some(_)) = reader.next_line().await {} });
        let address = ready["listen"]
            .as_str()
            .context("missing listen")?
            .parse()?;
        let client = Client::new(
            address,
            format!("localhost:{}", origin.address.port()),
            &state.join("proxy-ca.pem"),
        )?;
        Ok(Self {
            child,
            client,
            state,
        })
    }
    async fn kill(&mut self) -> Result<()> {
        self.child.kill().await?;
        self.child.wait().await?;
        Ok(())
    }
}
struct Suite {
    cases: Vec<Value>,
}
impl Suite {
    fn new() -> Self {
        Self { cases: Vec::new() }
    }
    fn run<'a, F>(&'a mut self, id: &'a str, f: F) -> Pin<Box<dyn Future<Output = ()> + 'a>>
    where
        F: Future<Output = Result<Value>> + 'a,
    {
        // Erase each case future before awaiting it, keeping the suite's outer
        // state machine and debug stack temporaries independent of case size.
        let f = Box::pin(f);
        Box::pin(async move {
            let start = Instant::now();
            let value = match tokio::time::timeout(Duration::from_secs(45), f).await {
                Ok(Ok(evidence)) => {
                    json!({"case":id,"pass":true,"elapsed_ms":start.elapsed().as_millis(),"evidence":evidence})
                }
                Ok(Err(e)) => {
                    json!({"case":id,"pass":false,"elapsed_ms":start.elapsed().as_millis(),"error":format!("{e:#}")})
                }
                Err(_) => {
                    json!({"case":id,"pass":false,"elapsed_ms":start.elapsed().as_millis(),"error":"case deadline exceeded"})
                }
            };
            println!(
                "{}",
                json!({"case":id,"pass":value["pass"],"elapsed_ms":value["elapsed_ms"]})
            );
            self.cases.push(value);
        })
    }
}
async fn roundtrip(
    client: &Client,
    origin: &Origin,
    id: &str,
    h2: bool,
    fragment: bool,
    method: &str,
    headers: &[(&str, &str)],
    data: Vec<u8>,
    expected: u16,
    reason: Option<&str>,
) -> Result<Value> {
    let before = origin.evidence.count();
    let expected_len = data.len();
    let expected_digest = origin::digest(&data);
    let mut reply = client::timeout_request(client.request(
        h2,
        fragment,
        method,
        &format!("/{id}"),
        headers,
        full(data),
    ))
    .await?;
    let status = reply.response.status().as_u16();
    let actual_reason = reply
        .response
        .headers()
        .get("x-aidlp-reason")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let version = format!("{:?}", reply.response.version());
    let (body, _) = reply.collect().await?;
    let after = origin.evidence.count();
    ensure!(
        status == expected,
        "expected HTTP {expected}, received {status}"
    );
    ensure!(
        version == if h2 { "HTTP/2.0" } else { "HTTP/1.1" },
        "protocol mismatch: {version}"
    );
    if let Some(reason) = reason {
        ensure!(
            actual_reason == reason,
            "unexpected block reason {actual_reason}"
        );
    }
    ensure!(
        after - before == usize::from(expected == 200),
        "origin count changed unexpectedly: {before} -> {after}"
    );
    if expected == 200 {
        ensure!(body == b"safe-origin-response", "response body mismatch");
        let events = origin.evidence.events();
        let last = events.last().context("missing origin event")?;
        ensure!(
            last["body_len"] == expected_len && last["body_fnv1a64"] == expected_digest,
            "origin body evidence mismatch"
        );
        ensure!(last["case_id"] == id, "wrong origin case id");
    }
    Ok(
        json!({"status":status,"protocol":version,"block_reason":actual_reason,"origin_before":before,"origin_after":after,"body_len":expected_len,"body_fnv1a64":expected_digest,"response_len":body.len()}),
    )
}
#[tokio::main]
async fn main() -> Result<()> {
    rustls::crypto::ring::default_provider()
        .install_default()
        .ok();
    let args: Vec<String> = std::env::args().skip(1).collect();
    Box::pin(run_suite(args)).await
}
async fn run_suite(args: Vec<String>) -> Result<()> {
    if args.first().is_some_and(|v| v == "origin") {
        let listen: SocketAddr = option(&args, "--listen")
            .context("origin requires --listen IP:port")?
            .parse()?;
        let state = PathBuf::from(option(&args, "--state").context("origin requires --state DIR")?);
        ensure!(!state.exists(), "origin state directory must be fresh");
        let origin = Origin::start(listen, &state).await?;
        println!(
            "{}",
            json!({"ready":true,"listen":origin.address,"ca":origin.ca_path,"sni":"localhost","state":state})
        );
        tokio::signal::ctrl_c().await?;
        return Ok(());
    }
    let proxy = PathBuf::from(option(&args,"--proxy").context("usage: acceptance --proxy PATH --output FRESH_DIR | acceptance origin --listen IP:port --state FRESH_DIR")?);
    let output = PathBuf::from(option(&args, "--output").context("missing --output")?);
    ensure!(!output.exists(), "output directory must be fresh");
    std::fs::create_dir_all(&output)?;
    let origin = Origin::start("127.0.0.1:0".parse()?, &output.join("origin")).await?;
    let mut runtime = Runtime::start(&proxy, &origin, &output.join("primary"), true, None).await?;
    let c = &runtime.client;
    let mut suite = Suite::new();
    let h1_origin = Origin::start_protocol("127.0.0.1:0".parse()?, &output.join("h1-origin"), false).await?;
    let mut h1_runtime = Runtime::start(&proxy, &h1_origin, &output.join("h1-upstream"), true, None).await?;
    suite.run("h2-client-h1-upstream", roundtrip(&h1_runtime.client, &h1_origin, "h2-client-h1-upstream", true, false, "POST", &[("content-type", "application/json")], br#"{"message":"safe request"}"#.to_vec(), 200, None)).await;
    h1_runtime.kill().await?;

    for h2 in [false, true] {
        let p = if h2 { "h2" } else { "h1" };
        let id = format!("{p}-normal");
        suite
            .run(
                &id,
                roundtrip(
                    c,
                    &origin,
                    &id,
                    h2,
                    false,
                    "POST",
                    &[("content-type", "application/json")],
                    br#"{"message":"safe request"}"#.to_vec(),
                    200,
                    None,
                ),
            )
            .await;
        let id = format!("{p}-sensitive");
        suite
            .run(
                &id,
                roundtrip(
                    c,
                    &origin,
                    &id,
                    h2,
                    false,
                    "POST",
                    &[("content-type", "text/plain")],
                    b"person@example.test".to_vec(),
                    403,
                    Some("sensitive_data"),
                ),
            )
            .await;
    }
    let table: Vec<(&str, &str, Vec<u8>, &str)> = vec![
        (
            "json-escaped-email",
            "application/json",
            br#"{"email":"person\u0040example.test"}"#.to_vec(),
            "sensitive_data",
        ),
        (
            "json-numeric-card",
            "application/json",
            br#"{"card":4111111111111111}"#.to_vec(),
            "sensitive_data",
        ),
        (
            "form-repeated-sensitive-first",
            "application/x-www-form-urlencoded",
            b"x=person%40example.test&x=safe".to_vec(),
            "sensitive_data",
        ),
        (
            "form-repeated-sensitive-last",
            "application/x-www-form-urlencoded",
            b"x=safe&x=person%40example.test".to_vec(),
            "sensitive_data",
        ),
        (
            "form-embedded-json",
            "application/x-www-form-urlencoded",
            b"x=%7B%22email%22%3A%22person%5Cu0040example.test%22%7D".to_vec(),
            "sensitive_data",
        ),
        (
            "unsupported-content",
            "application/octet-stream",
            b"safe".to_vec(),
            "uninspectable_request",
        ),
        (
            "malformed-json",
            "application/json",
            b"{broken}".to_vec(),
            "uninspectable_request",
        ),
        (
            "duplicate-json-key",
            "application/json",
            br#"{"x":"safe","x":"other"}"#.to_vec(),
            "uninspectable_request",
        ),
        (
            "non-utf8",
            "text/plain",
            vec![0xff, 0xfe],
            "uninspectable_request",
        ),
        (
            "malformed-form",
            "application/x-www-form-urlencoded",
            b"x=%GG".to_vec(),
            "uninspectable_request",
        ),
        (
            "malformed-content-type",
            "text/plain garbage",
            b"safe".to_vec(),
            "uninspectable_request",
        ),
        (
            "encoded-content",
            "text/plain",
            b"safe".to_vec(),
            "uninspectable_request",
        ),
        (
            "fullwidth-card",
            "text/plain",
            "４１１１１１１１１１１１１１１１".as_bytes().to_vec(),
            "sensitive_data",
        ),
        (
            "later-phone-candidate",
            "text/plain",
            b"010 01012345678".to_vec(),
            "sensitive_data",
        ),
        ("sensitive-url", "text/plain", Vec::new(), "sensitive_data"),
    ];
    for (id, kind, data, reason) in table {
        let path = if id == "sensitive-url" {
            "sensitive-url?q=person%40example.test"
        } else {
            id
        };
        let headers = if id == "encoded-content" {
            vec![("content-type", kind), ("content-encoding", "gzip")]
        } else {
            vec![("content-type", kind)]
        };
        suite
            .run(
                id,
                roundtrip(
                    c,
                    &origin,
                    path,
                    false,
                    false,
                    "POST",
                    &headers,
                    data,
                    403,
                    Some(reason),
                ),
            )
            .await;
    }
    suite
        .run(
            "duplicate-content-type",
            roundtrip(
                c,
                &origin,
                "duplicate-content-type",
                false,
                false,
                "POST",
                &[
                    ("content-type", "text/plain"),
                    ("content-type", "application/json"),
                ],
                b"safe".to_vec(),
                403,
                Some("uninspectable_request"),
            ),
        )
        .await;
    suite
        .run(
            "unknown-method",
            roundtrip(
                c,
                &origin,
                "unknown-method",
                false,
                false,
                "CUSTOM",
                &[("content-type", "text/plain")],
                b"safe".to_vec(),
                403,
                Some("inspection_error"),
            ),
        )
        .await;
    suite
        .run(
            "exactly-1MiB",
            roundtrip(
                c,
                &origin,
                "exactly-1MiB",
                false,
                false,
                "POST",
                &[("content-type", "text/plain")],
                vec![b'a'; 1024 * 1024],
                200,
                None,
            ),
        )
        .await;
    suite
        .run(
            "fragmented-clienthello",
            roundtrip(
                c,
                &origin,
                "fragmented-clienthello",
                false,
                true,
                "POST",
                &[("content-type", "text/plain")],
                b"safe".to_vec(),
                200,
                None,
            ),
        )
        .await;
    suite.run("oversize-declared-body",async {
        let before = origin.evidence.count();
        let (status,headers) = client::raw_headers(c,&format!("POST /oversize HTTP/1.1\r\nHost: {}\r\nContent-Type: text/plain\r\nContent-Length: 1048577\r\n\r\n",c.authority)).await?;
        ensure!(status==403 && headers.contains("uninspectable_request"), "oversize headers must block");
        ensure!(origin.evidence.count()==before,"oversize forwarded");
        Ok(json!({"status":status,"declared_body_len":1048577,"origin_delta":0}))
    }).await;
    suite
        .run("oversize-chunked-body", async {
            // A real oversized body without Content-Length exercises Limited, not header validation.
            let (tx, rx) = tokio::sync::mpsc::channel(2);
            tokio::spawn(async move {
                let _ = tx.send(bytes::Bytes::from(vec![b'a'; 512 * 1024])).await;
                let _ = tx
                    .send(bytes::Bytes::from(vec![b'a'; 512 * 1024 + 1]))
                    .await;
            });
            let before = origin.evidence.count();
            let mut r = c
                .request(
                    false,
                    false,
                    "POST",
                    "/oversize-chunked",
                    &[("content-type", "text/plain")],
                    origin::ChannelBody(rx).boxed(),
                )
                .await?;
            ensure!(
                r.response.status() == 403,
                "oversize streaming body must block"
            );
            let _ = r.collect().await?;
            ensure!(origin.evidence.count() == before, "oversize forwarded");
            Ok(json!({"status":403,"body_len":1048577,"origin_delta":0}))
        })
        .await;
    suite
        .run("authority-mismatch", async {
            let before = origin.evidence.count();
            let (status, headers) = client::raw_headers(
                c,
                "GET https://other.test:443/mismatch HTTP/1.1\r\nHost: other.test:443\r\n\r\n",
            )
            .await?;
            ensure!(
                status == 403 && headers.contains("uninspectable_request"),
                "authority mismatch must block"
            );
            ensure!(
                origin.evidence.count() == before,
                "authority mismatch forwarded"
            );
            Ok(json!({"status":status,"origin_delta":0}))
        })
        .await;
    suite
        .run("nonselected-sni-protected-endpoint", async {
            let before = origin.evidence.count();
            let tcp_before = origin.evidence.tcp_count();
            let tls = c.tls("other.test", false, false).await;
            ensure!(tls.is_err(), "nonselected SNI accepted");
            ensure!(origin.evidence.count() == before, "SNI mismatch forwarded");
            ensure!(
                origin.evidence.tcp_count() == tcp_before,
                "SNI mismatch opened an upstream TCP connection"
            );
            Ok(json!({"tls_rejected":true,"origin_delta":0,"upstream_tcp_delta":0}))
        })
        .await;
    suite
        .run("missing-sni-protected-endpoint", async {
            let before = origin.evidence.count();
            let tcp_before = origin.evidence.tcp_count();
            let mut client = c.clone();
            let mut config = (*client.config).clone();
            config.enable_sni = false;
            client.config = std::sync::Arc::new(config);
            ensure!(
                client.tls("localhost", false, false).await.is_err(),
                "missing SNI accepted"
            );
            ensure!(
                origin.evidence.count() == before && origin.evidence.tcp_count() == tcp_before,
                "missing SNI forwarded"
            );
            Ok(json!({"tls_rejected":true,"origin_delta":0,"upstream_tcp_delta":0}))
        })
        .await;
    for h2 in [false, true] {
        let id = if h2 {
            "h2-sse-progressive"
        } else {
            "h1-sse-progressive"
        };
        suite
            .run(id, async {
                let before = origin.evidence.count();
                let mut r = c
                    .request(
                        h2,
                        false,
                        "GET",
                        &format!("/sse/{id}"),
                        &[],
                        full(Vec::new()),
                    )
                    .await?;
                ensure!(r.response.status() == 200, "SSE status");
                let (data, times) = r.collect().await?;
                ensure!(times.len() >= 4, "SSE was buffered: {} frames", times.len());
                ensure!(
                    times[0] < 500 && times.last().unwrap() - times[0] >= 550,
                    "SSE timing is not progressive: {times:?}"
                );
                ensure!(
                    data.split(|b| *b == b'\n')
                        .filter(|l| l.starts_with(b"data:"))
                        .count()
                        == 4,
                    "SSE event count mismatch"
                );
                ensure!(origin.evidence.count() == before + 1, "SSE origin count");
                Ok(json!({"status":200,"frame_arrival_ms":times,"event_count":4,"origin_delta":1}))
            })
            .await;
    }
    suite.run("concurrency-16x10",async {
        let before=origin.evidence.count(); let start=Instant::now(); let mut tasks=tokio::task::JoinSet::new();
        for worker in 0..16 { let c=c.clone(); tasks.spawn(async move {
            let mut times=Vec::new();
            for request in 0..10 { let start=Instant::now(); let id=format!("concurrent-{worker}-{request}");
                let mut r=c.request(worker%2==0,false,"POST",&format!("/{id}"),&[("content-type","text/plain")],full(b"safe".to_vec())).await?;
                ensure!(r.response.status()==200,"concurrent request status {}",r.response.status()); let (data,_)=r.collect().await?; ensure!(data==b"safe-origin-response","concurrent response mismatch"); times.push(start.elapsed().as_millis());
            } Ok::<_,anyhow::Error>(times)
        }); }
        let mut times=Vec::new(); while let Some(result)=tasks.join_next().await {times.extend(result??);}
        times.sort(); ensure!(origin.evidence.count()==before+160,"concurrency origin count");
        let events=origin.evidence.events(); let events=&events[before..];
        ensure!(events.iter().all(|e| e["body_len"]==4 && e["body_fnv1a64"]==origin::digest(b"safe")),"concurrency body evidence");
        Ok(json!({"requests":160,"workers":16,"origin_delta":160,"elapsed_ms":start.elapsed().as_millis(),"latency_ms":{"min":times[0],"p50":times[80],"p95":times[152],"max":times[159]}}))
    }).await;
    suite.run("sse-request-slot-bound",async {
        let before=origin.evidence.count(); let mut tasks=tokio::task::JoinSet::new();
        for n in 0..32 {let c=c.clone();tasks.spawn(async move {c.request(true,false,"GET",&format!("/hold/{n}"),&[],full(Vec::new())).await});}
        let mut held=Vec::new(); while let Some(r)=tasks.join_next().await {let r=r??; ensure!(r.response.status()==200,"held stream status");held.push(r);}
        ensure!(origin.evidence.count()==before+32,"held origin count");
        let mut overflow=c.request(false,false,"GET","/overload",&[],full(Vec::new())).await?;
        ensure!(overflow.response.status()==403 && overflow.response.headers().get("x-aidlp-reason").is_some_and(|r|r=="overloaded"),"33rd active SSE request was allowed");
        let _=overflow.collect().await?;ensure!(origin.evidence.count()==before+32,"overflow forwarded");
        drop(held);tokio::time::sleep(Duration::from_millis(250)).await;
        let mut recovery=c.request(false,false,"GET","/slot-recovery",&[],full(Vec::new())).await?;
        ensure!(recovery.response.status()==200,"slots not released on client cancellation");let _=recovery.collect().await?;
        Ok(json!({"held_streams":32,"overflow_status":403,"overflow_origin_delta":0,"recovery_status":200}))
    }).await;
    suite
        .run("slow-body-deadline", async {
            let before = origin.evidence.count();
            let start = Instant::now();
            let (tx, rx) = tokio::sync::mpsc::channel(1);
            let producer = tokio::spawn(async move {
                let _ = tx.send(bytes::Bytes::from_static(b"a")).await;
                tokio::time::sleep(Duration::from_secs(12)).await;
                let _ = tx.send(bytes::Bytes::from_static(b"b")).await;
            });
            let mut r = c
                .request(
                    false,
                    false,
                    "POST",
                    "/slow-body",
                    &[("content-type", "text/plain")],
                    origin::ChannelBody(rx).boxed(),
                )
                .await?;
            ensure!(r.response.status() == 403, "slow body allowed");
            let _ = r.collect().await?;
            producer.abort();
            let elapsed = start.elapsed().as_millis();
            ensure!(
                (9500..15000).contains(&elapsed),
                "unexpected slow body deadline {elapsed}"
            );
            ensure!(origin.evidence.count() == before, "slow body forwarded");
            Ok(json!({"status":403,"elapsed_ms":elapsed,"origin_delta":0}))
        })
        .await;
    suite.run("audit-metadata-only",async {
        let raw=std::fs::read_to_string(runtime.state.join("audit.jsonl"))?;
        for token in ["person","example.test","4111111111111111","safe request","message","path","query"] {ensure!(!raw.contains(token),"audit retained content marker");}
        let events:Vec<Value>=raw.lines().map(serde_json::from_str).collect::<std::result::Result<_,_>>()?;
        ensure!(!events.is_empty(),"audit empty");
        for (i,e) in events.iter().enumerate() {ensure!(e["event_id"]==i+1,"audit sequence discontinuity"); ensure!(e.as_object().unwrap().keys().all(|k|matches!(k.as_str(),"event_id"|"timestamp_ms"|"host"|"method"|"body_len"|"reason"|"rules"|"action")),"unexpected audit field");}
        Ok(json!({"events":events.len(),"bytes":raw.len(),"content_markers_absent":true,"sequence_contiguous":true}))
    }).await;
    suite.run("existing-audit-startup-refused", async {
        let before = std::fs::read(runtime.state.join("audit.jsonl"))?;
        let result = Command::new(&proxy).arg("--config").arg(output.join("primary/config.json")).kill_on_drop(true).output().await?;
        ensure!(!result.status.success(), "existing audit runtime started");
        ensure!(result.stdout.is_empty(), "existing audit emitted readiness");
        ensure!(String::from_utf8_lossy(&result.stderr).contains("cannot create exclusive audit file"), "unexpected startup failure");
        ensure!(std::fs::read(runtime.state.join("audit.jsonl"))? == before, "existing audit modified");
        Ok(json!({"startup_refused":true,"audit_bytes_preserved":before.len(),"exit_code":result.status.code()}))
    }).await;
    let mut untrusted =
        Runtime::start(&proxy, &origin, &output.join("untrusted"), false, None).await?;
    suite
        .run("untrusted-upstream-certificate", async {
            let before = origin.evidence.count();
            let mut r = untrusted
                .client
                .request(
                    false,
                    false,
                    "POST",
                    "/untrusted",
                    &[("content-type", "text/plain")],
                    full(b"safe".to_vec()),
                )
                .await?;
            ensure!(r.response.status() == 502, "untrusted upstream accepted");
            let _ = r.collect().await?;
            ensure!(
                origin.evidence.count() == before,
                "untrusted upstream HTTP reached"
            );
            Ok(json!({"status":502,"origin_delta":0}))
        })
        .await;
    untrusted.kill().await?;
    let mut quota =
        Runtime::start(&proxy, &origin, &output.join("audit-quota"), true, Some(1)).await?;
    suite
        .run("audit-quota-persistent-fail-closed", async {
            let before = origin.evidence.count();
            let mut statuses = Vec::new();
            for n in 0..3 {
                let mut r = quota
                    .client
                    .request(
                        false,
                        false,
                        "POST",
                        &format!("/audit-quota-{n}"),
                        &[("content-type", "text/plain")],
                        full(b"safe".to_vec()),
                    )
                    .await?;
                statuses.push(r.response.status().as_u16());
                ensure!(
                    r.response.status() == 403
                        && r.response
                            .headers()
                            .get("x-aidlp-reason")
                            .is_some_and(|r| r == "inspection_error"),
                    "quota request allowed"
                );
                let _ = r.collect().await?;
            }
            ensure!(origin.evidence.count() == before, "quota forwarded");
            let bytes = std::fs::metadata(quota.state.join("audit.jsonl"))?.len();
            ensure!(bytes == 0, "quota wrote oversized event");
            Ok(json!({"quota_bytes":1,"statuses":statuses,"origin_delta":0,"audit_bytes":bytes}))
        })
        .await;
    quota.kill().await?;
    suite.run("forced-runtime-kill-existing-tls-stream",async {
        // TLS is established before SIGKILL. No HTTP request has been sent yet.
        use tokio::io::{AsyncReadExt,AsyncWriteExt};
        let before=origin.evidence.count();let mut tls=runtime.client.tls("localhost",false,false).await?;
        let mut sse=runtime.client.request(false,false,"GET","/hold/kill",&[],full(Vec::new())).await?;
        ensure!(sse.response.status()==200,"kill SSE setup");
        let _=sse.response.body_mut().frame().await.context("SSE missing frame")??;
        ensure!(origin.evidence.count()==before+1,"kill baseline origin count");
        runtime.kill().await?;
        let write=tls.write_all(format!("POST /after-kill HTTP/1.1\r\nHost: {}\r\nContent-Type: text/plain\r\nContent-Length: 4\r\n\r\nsafe",runtime.client.authority).as_bytes()).await;
        let mut byte=[0];let read=tokio::time::timeout(Duration::from_secs(2),tls.read(&mut byte)).await;
        let closed=matches!(read,Ok(Ok(0))|Ok(Err(_)));ensure!(closed,"existing TLS read did not prove closure after kill");
        let sse_result=tokio::time::timeout(Duration::from_secs(2),sse.response.body_mut().frame()).await;
        ensure!(matches!(sse_result,Ok(None)|Ok(Some(Err(_)))),"existing SSE stayed active after kill");
        ensure!(tokio::net::TcpStream::connect(runtime.client.address).await.is_err(),"fixture listener survived kill");
        tokio::time::sleep(Duration::from_millis(150)).await;ensure!(origin.evidence.count()==before+1,"postkill request reached origin");
        Ok(json!({"existing_tls_closed":closed,"postkill_write_failed":write.is_err(),"existing_sse_terminated":true,"listener_refused":true,"postkill_origin_delta":0,"scope":"fixture subprocess only; does not prove Windows capture crash containment"}))
    }).await;
    let failed = suite.cases.iter().filter(|c| c["pass"] != true).count();
    let report = json!({"schema":"aidlp-native-acceptance-v1","python":false,"proxy":proxy,"origin":origin.address,"passed":suite.cases.len()-failed,"failed":failed,"cases":suite.cases,"origin_http_count":origin.evidence.count(),"origin_events":origin.evidence.events(),"limitations":["Controlled loopback TLS fixtures do not prove production AI sites, Windows interception, or capture crash containment.","Origin FNV-1a64 is a deterministic equality check, not a cryptographic authenticity claim.","OS disk I/O failure injection is not available through exclusive create_new audit CLI; actual bounded audit quota exhaustion is tested."]});
    std::fs::write(
        output.join("report.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    println!(
        "{}",
        json!({"report":output.join("report.json"),"passed":report["passed"],"failed":failed,"origin_http_count":origin.evidence.count()})
    );
    ensure!(failed == 0, "{failed} acceptance case(s) failed");
    Ok(())
}
fn option<'a>(args: &'a [String], key: &str) -> Option<&'a str> {
    args.windows(2).find(|p| p[0] == key).map(|p| p[1].as_str())
}

#[cfg(test)]
mod layout_tests {
    #[test]
    fn suite_future_layout() {
        let bytes = std::thread::Builder::new()
            .stack_size(8 * 1024 * 1024)
            .spawn(|| {
                let future = super::run_suite(Vec::new());
                std::mem::size_of_val(&future)
            })
            .unwrap()
            .join()
            .unwrap();
        println!("acceptance_suite_future_bytes={bytes}");
        assert!(
            bytes <= 64 * 1024,
            "acceptance suite future unexpectedly large"
        );
    }
}
