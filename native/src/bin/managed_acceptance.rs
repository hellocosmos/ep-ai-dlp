//! Real managed TLS acceptance, optionally through Windows selected-process capture.
#[path = "acceptance/client.rs"]
mod client;
use anyhow::{ensure, Context, Result};
use client::{full, Client};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    net::SocketAddr,
    path::{Path, PathBuf},
    process::Stdio,
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::Command,
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    proxy: PathBuf,
    redirector: Option<PathBuf>,
    origin_ca: PathBuf,
    destination: SocketAddr,
    management: PathBuf,
    output: PathBuf,
    monitor_revision: u64,
    block_revision: u64,
}
#[derive(Serialize, Deserialize)]
struct ChildConfig {
    destination: SocketAddr,
    authority: String,
    ca: PathBuf,
    status: PathBuf,
    monitor_revision: u64,
    block_revision: u64,
}
async fn wait_policy(path: &Path, revision: u64) -> Result<()> {
    let start = Instant::now();
    loop {
        if let Ok(raw) = std::fs::read(path) {
            if let Ok(value) = serde_json::from_slice::<Value>(&raw) {
                if value["applied_revision"] == revision && value["policy_valid"] == true {
                    return Ok(());
                }
            }
        }
        ensure!(
            start.elapsed() < Duration::from_secs(240),
            "policy apply timed out"
        );
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
}
async fn stats(c: &Client) -> Result<u64> {
    let mut r = client::timeout_request(c.request(
        false,
        false,
        "GET",
        "/__aidlp_stats",
        &[],
        full(vec![]),
    ))
    .await?;
    ensure!(r.response.status() == 200, "stats failed");
    let (data, _) = r.collect().await?;
    serde_json::from_slice::<Value>(&data)?["total_requests"]
        .as_u64()
        .context("origin counter missing")
}
async fn case(
    c: &Client,
    id: &str,
    body: &[u8],
    kind: &str,
    expected: u16,
    h2: bool,
) -> Result<Value> {
    let before = stats(c).await?;
    let mut r = client::timeout_request(c.request(
        h2,
        false,
        "POST",
        "/managed-acceptance",
        &[("content-type", kind)],
        full(body.to_vec()),
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
    r.collect().await?;
    let after = stats(c).await?;
    ensure!(status == expected, "{id}: unexpected status {status}");
    ensure!(
        after - before == u64::from(expected == 200),
        "{id}: unexpected origin delivery"
    );
    Ok(json!({"case":id,"status":status,"origin_delta":after-before,"reason":reason,"pass":true}))
}
async fn child(config: ChildConfig) -> Result<()> {
    // Fresh child does not open any network socket until capture and policy are ready.
    wait_policy(&config.status, config.monitor_revision).await?;
    let ready_at = Instant::now();
    while !config.ca.exists() {
        ensure!(
            ready_at.elapsed() < Duration::from_secs(120),
            "proxy CA not ready"
        );
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    let c = Client::new(config.destination, config.authority, &config.ca)?;
    let start = Instant::now();
    loop {
        if stats(&c).await.is_ok() {
            break;
        }
        ensure!(
            start.elapsed() < Duration::from_secs(30),
            "capture did not become ready"
        );
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    let safe = case(
        &c,
        "monitor-safe",
        b"safe request",
        "text/plain",
        200,
        false,
    )
    .await?;
    let monitor = case(
        &c,
        "monitor-email",
        b"person@example.test",
        "text/plain",
        200,
        false,
    )
    .await?;
    println!(
        "{}",
        json!({"phase":"monitor","revision":config.monitor_revision,"cases":[safe,monitor]})
    );
    wait_policy(&config.status, config.block_revision).await?;
    let blocked = case(
        &c,
        "block-email-h1",
        b"person@example.test",
        "text/plain",
        403,
        false,
    )
    .await?;
    let blocked_h2 = case(
        &c,
        "block-email-h2",
        b"person@example.test",
        "text/plain",
        403,
        true,
    )
    .await?;
    let malformed = case(
        &c,
        "unsupported-always-blocked",
        b"safe",
        "multipart/form-data",
        403,
        false,
    )
    .await?;
    let safe = case(&c, "block-safe", b"safe request", "text/plain", 200, true).await?;
    println!(
        "{}",
        json!({"phase":"block","revision":config.block_revision,"cases":[blocked,blocked_h2,malformed,safe]})
    );
    Ok(())
}
async fn run(config: Config) -> Result<()> {
    ensure!(!config.output.exists(), "acceptance output must be fresh");
    std::fs::create_dir_all(&config.output)?;
    let management: Value = serde_json::from_slice(&std::fs::read(&config.management)?)?;
    let cache = PathBuf::from(
        management["cache_dir"]
            .as_str()
            .context("cache directory missing")?,
    );
    let state = config.output.join("engine");
    std::fs::create_dir_all(&state)?;
    let capture = config.redirector.is_some();
    if capture {
        ensure!(cfg!(windows), "capture requires Windows");
    }
    let mut client_config = ChildConfig {
        destination: config.destination,
        authority: format!("aidlp.test:{}", config.destination.port()),
        ca: state.join("proxy-ca.pem"),
        status: cache.join("status.json"),
        monitor_revision: config.monitor_revision,
        block_revision: config.block_revision,
    };
    let child_file = config.output.join("client.json");
    std::fs::write(&child_file, serde_json::to_vec(&client_config)?)?;
    let start_child = || {
        Command::new(std::env::current_exe().unwrap())
            .arg("--child")
            .arg(&child_file)
            .stdout(Stdio::piped())
            .stderr(std::fs::File::create(config.output.join("client-stderr.log")).unwrap())
            .kill_on_drop(true)
            .spawn()
    };
    let mut captured = if capture { Some(start_child()?) } else { None };
    let mode = if let Some(redirector) = &config.redirector {
        json!({"kind":"capture","redirector":redirector,"pid":captured.as_ref().unwrap().id()})
    } else {
        json!({"kind":"fixture","listen":"127.0.0.1:0","destination":config.destination})
    };
    let engine_config = json!({"state_dir":state,"origin_ca":config.origin_ca,"targets":[client_config.authority],"protected_endpoints":[config.destination],"management":config.management,"mode":mode});
    let engine_file = config.output.join("engine.json");
    std::fs::write(&engine_file, serde_json::to_vec(&engine_config)?)?;
    let log = std::fs::File::create(config.output.join("engine-stderr.log"))?;
    let mut engine = Command::new(&config.proxy)
        .arg("--config")
        .arg(&engine_file)
        .stdout(Stdio::piped())
        .stderr(log)
        .kill_on_drop(true)
        .spawn()?;
    let engine_id = engine.id();
    let mut lines = BufReader::new(engine.stdout.take().unwrap()).lines();
    let line = tokio::time::timeout(Duration::from_secs(120), lines.next_line())
        .await??
        .context("engine exited before ready")?;
    let ready: Value = serde_json::from_str(&line)?;
    ensure!(
        ready["ready"] == true || ready["capture_started"] == true,
        "engine did not report ready"
    );
    tokio::spawn(async move { while let Ok(Some(_)) = lines.next_line().await {} });
    if !capture {
        client_config.destination = ready["listen"]
            .as_str()
            .context("fixture address missing")?
            .parse()?;
        std::fs::write(&child_file, serde_json::to_vec(&client_config)?)?;
        captured = Some(start_child()?);
    }
    let mut test_client = captured.unwrap();
    let protected_pid = test_client.id();
    let stdout = test_client.stdout.take().unwrap();
    let mut reader = BufReader::new(stdout).lines();
    let mut phases = Vec::new();
    while let Some(line) =
        tokio::time::timeout(Duration::from_secs(300), reader.next_line()).await??
    {
        let value: Value = serde_json::from_str(&line)?;
        std::fs::write(
            config.output.join(format!(
                "{}.json",
                value["phase"].as_str().context("missing phase")?
            )),
            serde_json::to_vec_pretty(&value)?,
        )?;
        println!("{}", value);
        phases.push(value);
    }
    let status = test_client.wait().await?;
    ensure!(
        status.success() && phases.len() == 2,
        "managed client did not complete both phases"
    );
    let start = Instant::now();
    loop {
        let empty = std::fs::metadata(cache.join("events.jsonl")).is_ok_and(|v| v.len() == 0);
        if empty {
            break;
        }
        ensure!(
            start.elapsed() < Duration::from_secs(30),
            "events not acknowledged"
        );
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    engine.kill().await?;
    engine.wait().await?;
    let report = json!({"pass":true,"platform":std::env::consts::OS,"capture":capture,"engine_pid":engine_id,"client_pid":protected_pid,"phases":phases,"events_acknowledged":true,"limitations":["Controlled synthetic TLS input only","Selected-process capture does not establish machine-wide protection","No real AI provider or M365 file compatibility claim"]});
    std::fs::write(
        config.output.join("report.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    println!(
        "{}",
        json!({"pass":true,"report":config.output.join("report.json")})
    );
    Ok(())
}
#[tokio::main]
async fn main() -> Result<()> {
    let args = std::env::args().collect::<Vec<_>>();
    ensure!(args.len() == 3, "usage: managed_acceptance --config FILE");
    if args[1] == "--child" {
        child(serde_json::from_slice(&std::fs::read(&args[2])?)?).await
    } else {
        ensure!(args[1] == "--config", "unknown command");
        run(serde_json::from_slice(&std::fs::read(&args[2])?)?).await
    }
}
