//! Selected-process Windows capture acceptance. All input data is synthetic.
//! The parent is an unrelated process; only the waiting child is captured.
#[path = "acceptance/client.rs"]
mod client;
use anyhow::{Context, Result, ensure};
use client::{Client, full};
use http_body_util::BodyExt;
use hyper::body::Body;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    net::SocketAddr,
    path::{Path, PathBuf},
    process::Stdio,
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    process::Command,
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    proxy: PathBuf,
    redirector: PathBuf,
    origin_ca: PathBuf,
    destination: SocketAddr,
    output: PathBuf,
}
#[derive(Serialize, Deserialize)]
struct ChildConfig {
    destination: SocketAddr,
    ca: PathBuf,
    origin_ca: PathBuf,
    output: PathBuf,
}
async fn stats_value(c: &Client) -> Result<Value> {
    let mut r = client::timeout_request(c.request(
        false,
        false,
        "GET",
        "/__aidlp_stats",
        &[],
        full(vec![]),
    ))
    .await?;
    ensure!(r.response.status() == 200, "stats status");
    let (data, _) = r.collect().await?;
    Ok(serde_json::from_slice::<Value>(&data)?)
}
async fn stats(c: &Client) -> Result<u64> {
    stats_value(c).await?["total_requests"]
        .as_u64()
        .context("missing origin count")
}
async fn udp_count(c: &Client) -> Result<u64> {
    let value = stats_value(c).await?;
    ensure!(
        value["udp_receiver_running"] == true,
        "UDP origin receiver is not running"
    );
    ensure!(
        value["udp_receive_errors"] == 0,
        "UDP origin receiver reported errors"
    );
    value["total_udp_datagrams"]
        .as_u64()
        .context("missing UDP count")
}
async fn udp_probes(destination: SocketAddr, blocked: bool) -> Result<Vec<socket2::Socket>> {
    let mut sockets = Vec::new();
    for n in 0..8 {
        // Windows send_to requires a bound socket. Use the same explicit bind
        // in both processes so WSAEINVAL cannot masquerade as containment.
        let udp = socket2::Socket::new(
            socket2::Domain::IPV4,
            socket2::Type::DGRAM,
            Some(socket2::Protocol::UDP),
        )?;
        let address = socket2::SockAddr::from(destination);
        let binding = udp.bind(&socket2::SockAddr::from("0.0.0.0:0".parse::<SocketAddr>()?));
        if blocked {
            ensure!(
                binding.as_ref().err().and_then(|e| e.raw_os_error()) == Some(10013),
                "UDP guard bind must return WSAEACCES, got {binding:?}"
            );
            sockets.push(udp);
            continue;
        }
        binding?;
        let result = if n % 2 == 0 {
            udp.send_to(b"safe-udp", &address)
        } else {
            udp.connect(&address).and_then(|_| udp.send(b"safe-udp"))
        };
        ensure!(result? == 8, "UDP probe truncated");
        sockets.push(udp);
    }
    Ok(sockets)
}
async fn case(
    c: &Client,
    id: &str,
    h2: bool,
    fragment: bool,
    data: Vec<u8>,
    expected: u16,
) -> Result<Value> {
    let before = stats(c).await?;
    let start = Instant::now();
    let mut r = client::timeout_request(c.request(
        h2,
        fragment,
        "POST",
        &format!("/windows-{id}"),
        &[("content-type", "text/plain")],
        full(data),
    ))
    .await?;
    let status = r.response.status().as_u16();
    let reason = r
        .response
        .headers()
        .get("x-aidlp-reason")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_owned();
    let _ = r.collect().await?;
    let after = stats(c).await?;
    ensure!(
        status == expected,
        "{id}: expected {expected}, got {status}"
    );
    ensure!(
        after - before == u64::from(expected == 200),
        "{id}: unexpected origin count"
    );
    if expected == 403 {
        ensure!(reason == "sensitive_data", "wrong block reason");
    }
    Ok(
        json!({"case":id,"pass":true,"status":status,"reason":reason,"origin_delta":after-before,"elapsed_ms":start.elapsed().as_millis()}),
    )
}
async fn child(config: ChildConfig) -> Result<()> {
    let start = Instant::now();
    while !config.ca.exists() {
        ensure!(
            start.elapsed() < Duration::from_secs(60),
            "proxy CA not ready"
        );
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    let authority = format!("aidlp.test:{}", config.destination.port());
    let c = Client::new(config.destination, authority.clone(), &config.ca)?;
    // Trust only the ephemeral proxy CA until capture has actually been proven.
    loop {
        if tokio::time::timeout(Duration::from_secs(2), stats(&c))
            .await
            .is_ok_and(|r| r.is_ok())
        {
            break;
        }
        ensure!(
            start.elapsed() < Duration::from_secs(60),
            "selected-process capture not ready"
        );
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    println!(
        "{}",
        json!({"capture_verified":true,"pid":std::process::id(),"python":false})
    );
    let mut cases = Vec::new();
    let udp_before = udp_count(&c).await?;
    // Keep sockets alive so an early local socket close cannot masquerade as denial.
    let _udp_sockets = udp_probes(config.destination, true).await?;
    tokio::time::sleep(Duration::from_millis(300)).await;
    ensure!(
        udp_count(&c).await? == udp_before,
        "selected process UDP bypassed capture"
    );
    cases.push(
        json!({"case":"selected-udp-denied","pass":true,"bind_attempts":8,"socket_denials":8,"winsock_error":10013,"denial_stage":"bind","origin_delta":0}),
    );
    for h2 in [false, true] {
        let p = if h2 { "h2" } else { "h1" };
        cases.push(case(&c, &format!("{p}-normal"), h2, false, b"safe".to_vec(), 200).await?);
        cases.push(
            case(
                &c,
                &format!("{p}-blocked"),
                h2,
                false,
                b"person@example.test".to_vec(),
                403,
            )
            .await?,
        );
    }
    cases.push(
        case(
            &c,
            "fragmented-clienthello",
            false,
            true,
            b"safe".to_vec(),
            200,
        )
        .await?,
    );
    cases.push(case(&c, "one-mib", false, false, vec![b'a'; 1024 * 1024], 200).await?);
    let before = stats(&c).await?;
    let (status,headers)=client::raw_headers(&c,&format!("POST /windows-oversize HTTP/1.1\r\nHost: {authority}\r\nContent-Type: text/plain\r\nContent-Length: 1048577\r\n\r\n")).await?;
    ensure!(
        status == 403 && headers.contains("uninspectable_request") && stats(&c).await? == before,
        "oversize forwarded"
    );
    cases.push(json!({"case":"oversize","pass":true,"status":403,"origin_delta":0}));
    for h2 in [false, true] {
        let mut r = c
            .request(h2, false, "GET", "/sse/windows", &[], full(vec![]))
            .await?;
        ensure!(r.response.status() == 200, "SSE status");
        let (_, times) = r.collect().await?;
        ensure!(
            times.len() >= 4 && times[0] < 500 && times.last().unwrap() - times[0] >= 550,
            "SSE not progressive"
        );
        cases.push(
            json!({"case":if h2 {"h2-sse"}else{"h1-sse"},"pass":true,"frame_arrival_ms":times}),
        );
    }
    let before = stats(&c).await?;
    let start = Instant::now();
    let mut tasks = tokio::task::JoinSet::new();
    for worker in 0..16 {
        let c = c.clone();
        tasks.spawn(async move {
            let mut times = Vec::new();
            for n in 0..10 {
                let start = Instant::now();
                let mut r = c
                    .request(
                        worker % 2 == 0,
                        false,
                        "POST",
                        &format!("/windows-concurrent-{worker}-{n}"),
                        &[("content-type", "text/plain")],
                        full(b"safe".to_vec()),
                    )
                    .await?;
                ensure!(r.response.status() == 200, "concurrency status");
                let _ = r.collect().await?;
                times.push(start.elapsed().as_millis());
            }
            Ok::<_, anyhow::Error>(times)
        });
    }
    let mut times = Vec::new();
    while let Some(r) = tasks.join_next().await {
        times.extend(r??);
    }
    times.sort();
    ensure!(stats(&c).await? == before + 160, "concurrency origin count");
    cases.push(json!({"case":"concurrency","pass":true,"workers":16,"requests":160,"origin_delta":160,"elapsed_ms":start.elapsed().as_millis(),"latency_ms":{"p50":times[80],"p95":times[152],"max":times[159]}}));
    let before = stats(&c).await?;
    let start = Instant::now();
    let (status,_) = client::raw_headers(&c,&format!("POST /windows-slow HTTP/1.1\r\nHost: {authority}\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\na")).await?;
    ensure!(
        status == 403 && stats(&c).await? == before && start.elapsed() >= Duration::from_secs(9),
        "slow body deadline"
    );
    cases.push(json!({"case":"slow-body","pass":true,"status":403,"origin_delta":0,"elapsed_ms":start.elapsed().as_millis()}));
    // For crash validation also trust the origin. A surviving child would be able
    // to connect directly after redirector teardown; CA rejection cannot mask it.
    let combined = config.output.join("crash-trust.pem");
    let mut roots = std::fs::read(&config.ca)?;
    roots.extend(std::fs::read(&config.origin_ca)?);
    std::fs::write(&combined, roots)?;
    let crash = Client::new(config.destination, authority, &combined)?;
    let mut _held = crash
        .request(
            true,
            false,
            "GET",
            "/hold/windows-crash-stream",
            &[],
            full(vec![]),
        )
        .await?;
    ensure!(_held.response.status() == 200, "crash stream not allowed");
    ensure!(
        _held
            .response
            .headers()
            .get("content-type")
            .is_some_and(|v| v == "text/event-stream"),
        "crash stream content type"
    );
    let first = tokio::time::timeout(Duration::from_secs(3), _held.response.body_mut().frame())
        .await?
        .context("crash stream ended")??;
    ensure!(
        first.data_ref().is_some_and(|b| b.starts_with(b"data:")),
        "crash stream has no SSE event"
    );
    ensure!(
        !_held.response.body().is_end_stream(),
        "crash SSE already ended"
    );
    ensure!(
        udp_count(&c).await? == udp_before,
        "delayed selected UDP reached origin"
    );
    std::fs::write(
        config.output.join("client-report.json"),
        serde_json::to_vec_pretty(&json!({"pass":true,"pid":std::process::id(),"cases":cases}))?,
    )?;
    println!(
        "{}",
        json!({"crash_ready":true,"pid":std::process::id(),"active_sse":true,"case_count":cases.len()})
    );
    let mut command = [0];
    tokio::io::stdin().read_exact(&mut command).await?;
    std::fs::write(
        config.output.join("child-resumed.json"),
        b"{\"resumed\":true}",
    )?;
    // Reaching this after parent terminates the engine is a failed lifetime guard.
    let attempted = crash
        .request(
            false,
            false,
            "POST",
            "/windows-after-crash",
            &[("content-type", "text/plain")],
            full(b"safe".to_vec()),
        )
        .await;
    std::fs::write(
        config.output.join("child-survived.json"),
        serde_json::to_vec(&json!({"survived":true,"request_succeeded":attempted.is_ok()}))?,
    )?;
    anyhow::bail!("protected process survived engine termination")
}
#[cfg(windows)]
fn memory(pid: u32) -> Result<Value> {
    use windows::Win32::{
        Foundation::CloseHandle,
        System::{
            ProcessStatus::{K32GetProcessMemoryInfo, PROCESS_MEMORY_COUNTERS},
            Threading::{OpenProcess, PROCESS_QUERY_INFORMATION, PROCESS_VM_READ},
        },
    };
    unsafe {
        let h = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, false, pid)?;
        let mut counters = PROCESS_MEMORY_COUNTERS::default();
        counters.cb = std::mem::size_of_val(&counters) as u32;
        let cb = counters.cb;
        let ok = K32GetProcessMemoryInfo(h, &mut counters, cb);
        let _ = CloseHandle(h);
        ok.ok()?;
        Ok(
            json!({"working_set_bytes":counters.WorkingSetSize,"peak_working_set_bytes":counters.PeakWorkingSetSize}),
        )
    }
}
#[cfg(not(windows))]
fn memory(_pid: u32) -> Result<Value> {
    Ok(json!({"unavailable":true}))
}
async fn preexisting_udp_gate(config: &Config) -> Result<Value> {
    let out = config.output.join("preexisting-udp");
    std::fs::create_dir(&out)?;
    let placeholder = out.join("probe.json");
    std::fs::write(&placeholder, b"{}")?;
    let mut child = Command::new(std::env::current_exe()?)
        .arg("--prebound")
        .arg(&placeholder)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()?;
    let mut ready = BufReader::new(child.stdout.take().context("prebound stdout")?).lines();
    ensure!(
        tokio::time::timeout(Duration::from_secs(5), ready.next_line())
            .await??
            .as_deref()
            == Some("prebound-ready"),
        "prebound child readiness"
    );
    let pid = child.id().context("prebound PID")?;
    let cfg = json!({"state_dir":out.join("state"),"origin_ca":config.origin_ca,"targets":[format!("aidlp.test:{}",config.destination.port())],"protected_endpoints":[config.destination],"mode":{"kind":"capture","pid":pid,"redirector":config.redirector}});
    let path = out.join("capture-config.json");
    std::fs::write(&path, serde_json::to_vec_pretty(&cfg)?)?;
    let log = out.join("engine-stderr.log");
    let mut engine = Command::new(&config.proxy)
        .arg("--config")
        .arg(&path)
        .stdout(Stdio::null())
        .stderr(std::fs::File::create(&log)?)
        .kill_on_drop(true)
        .spawn()?;
    let status = tokio::time::timeout(Duration::from_secs(20), engine.wait()).await??;
    ensure!(
        !status.success()
            && std::fs::read_to_string(&log)?
                .contains("protected process has preexisting UDP sockets"),
        "preexisting UDP must refuse startup for the stated reason"
    );
    let _ = tokio::time::timeout(Duration::from_secs(3), child.wait()).await??;
    let result = json!({"pass":true,"startup_rejected":true,"reason":"preexisting_udp","protected_process_terminated":true});
    std::fs::write(out.join("report.json"), serde_json::to_vec_pretty(&result)?)?;
    Ok(result)
}

async fn parent(config: Config) -> Result<()> {
    ensure!(cfg!(windows), "real capture acceptance requires Windows");
    ensure!(!config.output.exists(), "output must be fresh");
    std::fs::create_dir_all(&config.output)?;
    let control = Client::new(
        config.destination,
        format!("aidlp.test:{}", config.destination.port()),
        &config.origin_ca,
    )?;
    // Prove the UDP origin is reachable before interception is enabled.
    let udp_before = udp_count(&control).await?;
    let _baseline_sockets = udp_probes(config.destination, false).await?;
    let started = Instant::now();
    let udp_after = loop {
        let value = udp_count(&control).await?;
        if value >= udp_before + 8 || started.elapsed() >= Duration::from_secs(2) {
            break value;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    };
    std::fs::write(
        config.output.join("udp-baseline.json"),
        serde_json::to_vec_pretty(
            &json!({"before":udp_before,"after":udp_after,"sent":8,"capture_active":false}),
        )?,
    )?;
    ensure!(
        udp_after == udp_before + 8,
        "UDP origin unavailable before capture"
    );
    let preexisting_udp = preexisting_udp_gate(&config).await?;
    let baseline = stats(&control).await?;
    let child_config = ChildConfig {
        destination: config.destination,
        ca: config.output.join("state/proxy-ca.pem"),
        origin_ca: config.origin_ca.clone(),
        output: config.output.clone(),
    };
    let cp = config.output.join("child-config.json");
    std::fs::write(&cp, serde_json::to_vec_pretty(&child_config)?)?;
    let err = std::fs::File::create(config.output.join("client-stderr.log"))?;
    let mut protected = Command::new(std::env::current_exe()?)
        .arg("--client")
        .arg(&cp)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(err)
        .kill_on_drop(true)
        .spawn()?;
    let pid = protected.id().context("child pid")?;
    let cfg = json!({"state_dir":config.output.join("state"),"origin_ca":config.origin_ca,"targets":[format!("aidlp.test:{}",config.destination.port())],"protected_endpoints":[config.destination],"mode":{"kind":"capture","pid":pid,"redirector":config.redirector}});
    let path = config.output.join("capture-config.json");
    std::fs::write(&path, serde_json::to_vec_pretty(&cfg)?)?;
    let mut engine = Command::new(&config.proxy)
        .arg("--config")
        .arg(&path)
        .stdout(std::fs::File::create(
            config.output.join("engine-stdout.jsonl"),
        )?)
        .stderr(std::fs::File::create(
            config.output.join("engine-stderr.log"),
        )?)
        .kill_on_drop(true)
        .spawn()?;
    let mut lines = BufReader::new(protected.stdout.take().context("child stdout")?).lines();
    let ready = tokio::time::timeout(Duration::from_secs(180), async {
        loop {
            let line = lines
                .next_line()
                .await?
                .context("protected client exited before crash test")?;
            let value: Value = serde_json::from_str(&line)?;
            println!("{value}");
            if value["crash_ready"] == true {
                return Ok::<_, anyhow::Error>(value);
            }
        }
    })
    .await??;
    let engine_memory = memory(engine.id().context("engine pid")?)?;
    // This process is not in the capture configuration and still trusts only origin CA.
    let before = stats(&control).await?;
    let mut unrelated = control
        .request(
            false,
            false,
            "POST",
            "/windows-unrelated-process",
            &[("content-type", "text/plain")],
            full(b"safe".to_vec()),
        )
        .await?;
    ensure!(
        unrelated.response.status() == 200,
        "unrelated process affected"
    );
    let _ = unrelated.collect().await?;
    ensure!(
        stats(&control).await? == before + 1,
        "unrelated origin count"
    );
    let udp_before = udp_count(&control).await?;
    let _udp_sockets = udp_probes(config.destination, false).await?;
    let udp_started = Instant::now();
    let udp_after = loop {
        let value = udp_count(&control).await?;
        if value >= udp_before + 8 || udp_started.elapsed() >= Duration::from_secs(2) {
            break value;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    };
    std::fs::write(
        config.output.join("unrelated-udp.json"),
        serde_json::to_vec_pretty(
            &json!({"sent":8,"connected":4,"unconnected":4,"before":udp_before,"after":udp_after}),
        )?,
    )?;
    ensure!(udp_after == udp_before + 8, "unrelated UDP affected");
    let before_kill = stats(&control).await?;
    ensure!(
        protected.try_wait()?.is_none(),
        "protected process died before engine kill"
    );
    let started = Instant::now();
    engine.kill().await?;
    engine.wait().await?;
    if let Some(stdin) = protected.stdin.as_mut() {
        let _ = stdin.write_all(b"G").await;
    }
    let exit = tokio::time::timeout(Duration::from_secs(3), protected.wait()).await??;
    let death_ms = started.elapsed().as_millis();
    tokio::time::sleep(Duration::from_millis(500)).await;
    let after_kill = stats(&control).await?;
    let resumed = config.output.join("child-resumed.json").exists();
    let survived = config.output.join("child-survived.json").exists();
    let crash_evidence = json!({"protected_process_alive_before_kill":true,"protected_process_wait_completed":true,"child_exit_code":exit.code(),"termination_ms":death_ms,"origin_before":before_kill,"origin_after":after_kill,"resumed_marker":resumed,"survived_marker":survived});
    std::fs::write(
        config.output.join("crash-evidence.json"),
        serde_json::to_vec_pretty(&crash_evidence)?,
    )?;
    // Job-close termination is not a normal child return. Its exit code need
    // not be nonzero; prove the actual lifecycle and absence of resumed work.
    ensure!(
        after_kill == before_kill && !resumed && !survived,
        "crash fail-closed proof failed"
    );
    let report = json!({"pass":true,"platform":"Windows","python":false,"protected_pid":pid,"baseline_origin_requests":baseline,"preexisting_udp":preexisting_udp,"client":serde_json::from_slice::<Value>(&std::fs::read(config.output.join("client-report.json"))?)?,"unrelated_process":{"status":200,"origin_delta":1,"udp_origin_delta":8},"engine_memory":engine_memory,"forced_kill":{"active_sse":ready["active_sse"],"protected_process_terminated":true,"termination_ms":death_ms,"origin_before":before_kill,"origin_after":after_kill,"origin_delta":after_kill-before_kill,"child_trusted_both_proxy_and_origin":true,"evidence":crash_evidence}});
    std::fs::write(
        config.output.join("report.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    println!("{report}");
    Ok(())
}
#[tokio::main]
async fn main() -> Result<()> {
    rustls::crypto::ring::default_provider()
        .install_default()
        .ok();
    let a: Vec<_> = std::env::args().collect();
    ensure!(
        a.len() == 3,
        "usage: windows_acceptance --config FILE | --client FILE"
    );
    let data = std::fs::read(Path::new(&a[2]))?;
    if a[1] == "--prebound" {
        let _udp = tokio::net::UdpSocket::bind("0.0.0.0:0").await?;
        println!("prebound-ready");
        std::future::pending::<()>().await;
        Ok(())
    } else if a[1] == "--client" {
        child(serde_json::from_slice(&data)?).await
    } else {
        ensure!(a[1] == "--config", "unknown mode");
        parent(serde_json::from_slice(&data)?).await
    }
}
