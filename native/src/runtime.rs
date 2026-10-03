use crate::{audit::Audit, config::{Config, Mode}, engine::Engine};
use anyhow::{Result, ensure};
use serde_json::json;
use std::{sync::Arc, time::Duration};
use tokio::{net::TcpListener, sync::{Semaphore, watch}, task::JoinSet};

pub async fn run(config: Config, mut stop: watch::Receiver<bool>) -> Result<()> {
    let targets = config.targets()?;
    ensure!(
        (1..=1024 * 1024 * 1024).contains(&config.audit_max_bytes),
        "audit_max_bytes must be 1..=1 GiB"
    );
    config.application_spec()?;
    let applications = matches!(config.mode, Mode::Applications { .. });
    let endpoints = config.endpoints(&targets).await?;
    std::fs::create_dir_all(&config.state_dir)?;
    let management = config
        .management
        .as_ref()
        .map(|path| {
            crate::management::Management::load(
                path,
                if applications {
                    "configured_applications"
                } else if matches!(config.mode, Mode::Capture { .. }) {
                    "selected_process"
                } else {
                    "fixture"
                },
                config.targets.clone(),
            )
        })
        .transpose()?;
    let audit_name = if management.is_some() {
        format!("audit-{}.jsonl", uuid::Uuid::new_v4())
    } else {
        "audit.jsonl".to_string()
    };
    let audit =
        Audit::open(config.state_dir.join(audit_name))?.with_max_bytes(config.audit_max_bytes);
    let (ca, pem) = crate::certificate::load_or_ephemeral(config.ca_dir.as_deref())?;
    std::fs::write(config.state_dir.join("proxy-ca.pem"), pem)?;
    let mut roots = crate::certificate::upstream_roots(if applications { None } else { config.origin_ca.as_deref() })?;
    if applications && config.origin_ca.is_some() { roots.roots.extend(crate::certificate::upstream_roots(config.origin_ca.as_deref())?.roots); }
    let engine = Engine::new(ca, roots, audit, targets)?
        .with_protected_endpoints(endpoints)
        .with_management(management.clone())
        .with_application_mode(applications);
    let management_task = management
        .as_ref()
        .map(|manager| tokio::spawn(manager.clone().run()));
    let result: Result<()> = async { match config.mode {
        Mode::Fixture {
            listen,
            destination,
        } => {
            ensure!(
                listen.ip().is_loopback(),
                "fixture listener must be loopback"
            );
            let listener = TcpListener::bind(listen).await?;
            engine.mark_running();
            println!(
                "{}",
                json!({"ready":true,"mode":"fixture","pid":std::process::id(),"listen":listener.local_addr()?.to_string(),"python":false})
            );
            let slots = Arc::new(Semaphore::new(128));
            let mut tasks = JoinSet::new();
            loop {
                tokio::select! {
                    result=listener.accept()=>{
                        let (io,_)=result?;let Ok(permit)=slots.clone().try_acquire_owned() else {drop(io);continue};let engine=engine.clone();
                        tasks.spawn(async move {let _permit=permit;let _=tokio::time::timeout(Duration::from_secs(300),engine.serve(io,destination)).await;});
                    },
                    _=tasks.join_next(),if !tasks.is_empty()=>{},
                    _=stop.changed()=>break,
                }
            }
            tasks.shutdown().await;
        }
        Mode::Applications { redirector, executables } => {
            let ports = config.targets.iter().map(|s| s.parse::<hyper::http::uri::Authority>().unwrap().port_u16().unwrap()).collect();
            crate::capture::run(engine, redirector, crate::capture::Selection::Applications { executables, ports }, stop.clone()).await?;
        }
        Mode::Capture { redirector, pid } => {
            crate::capture::run(engine, redirector, crate::capture::Selection::Pid(pid), stop.clone()).await?
        }
    }
    Ok(()) }.await;
    if let Some(task) = management_task { task.abort(); let _ = task.await; }
    if let Some(manager) = management {
        manager.stopped();
        let _ = tokio::time::timeout(Duration::from_secs(5), manager.synchronize()).await;
    }
    result
}
