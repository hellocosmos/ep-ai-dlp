use crate::engine::Engine;
use anyhow::Result;
use std::path::PathBuf;
use tokio::sync::watch;

#[derive(Clone)]
pub enum Selection {
    Pid(u32),
    Applications { executables: Vec<String>, ports: Vec<u16> },
}

#[cfg(not(windows))]
pub async fn run(_engine: Engine, _redirector: PathBuf, _selection: Selection, _stop: watch::Receiver<bool>) -> Result<()> {
    anyhow::bail!("capture requires Windows");
}

#[cfg(windows)]
pub async fn run(engine: Engine, redirector: PathBuf, selection: Selection, mut stop: watch::Receiver<bool>) -> Result<()> {
    use mitmproxy::{
        intercept_conf::{InterceptConf, ProcessInfo},
        messages::{TransportCommand, TransportEvent, TunnelInfo},
        packet_sources::{PacketSourceConf, PacketSourceTask, windows::WindowsConf},
        shutdown,
    };
    use std::{sync::Arc, time::Duration};
    use tokio::{sync::{Semaphore, mpsc}, task::JoinSet};

    let mut owned_guard = None;
    let spec = match &selection {
        Selection::Pid(pid) => {
            anyhow::ensure!(*pid > 4 && *pid != std::process::id(), "invalid protected PID");
            owned_guard = Some(crate::process_guard::ProcessGuard::attach(*pid)?);
            anyhow::ensure!(!mitmproxy::windows::network::network_table()?.iter().any(|e| e.protocol == 17 && e.pid == *pid), "protected process has preexisting UDP sockets");
            InterceptConf::try_from(pid.to_string().as_str())?
        }
        Selection::Applications { executables, ports } => {
            anyhow::ensure!(mitmproxy::packet_sources::windows::elevated()?, "application mode requires the elevated Windows service");
            crate::firewall::verify(executables.clone(), ports.clone()).await?;
            let spec = InterceptConf::try_from(executables.iter().map(|p| format!("={p}")).collect::<Vec<_>>())?;
            anyhow::ensure!(spec.is_application_scope(), "exact application paths required");
            spec
        }
    };
    let (events_tx, mut events_rx) = mpsc::channel(128);
    let (commands_tx, commands_rx) = mpsc::unbounded_channel();
    let (shutdown_tx, stopped) = shutdown::channel();
    let (mut task, conf) = WindowsConf { executable_path: redirector }.build(events_tx, commands_rx, stopped).await?;
    let ready = task.readiness();
    let mut capture = tokio::spawn(task.run());
    conf.send(spec.clone())?;
    // A queued configuration is not evidence of a loaded driver or applied selector.
    let ready_result: Result<()> = tokio::select! {
        result = tokio::time::timeout(Duration::from_secs(120), ready) => result.map_err(anyhow::Error::from).and_then(|r| r.map_err(anyhow::Error::from)),
        result = &mut capture => { let _ = result; Err(anyhow::anyhow!("redirector exited before configuration acknowledgment")) },
        _ = stop.changed() => Err(anyhow::anyhow!("capture stopped during startup")),
    };
    if let Err(error) = ready_result { let _ = shutdown_tx.send(()); capture.abort(); let _ = capture.await; return Err(error); }
    engine.mark_running();
    println!("{}", serde_json::json!({"capture_started":true,"scope":if matches!(selection,Selection::Pid(_)){"selected_process"}else{"configured_applications"},"pid":std::process::id(),"python":false}));
    let slots = Arc::new(Semaphore::new(128));
    let mut tasks = JoinSet::new();
    let mut guard_check = tokio::time::interval(Duration::from_secs(30));
    guard_check.tick().await;
    let mut capture_finished = false;
    let result = loop {
        tokio::select! {
            result = &mut capture => { capture_finished = true; break result.map_err(anyhow::Error::from).and_then(|x| x); },
            _ = stop.changed() => break Ok(()),
            _ = guard_check.tick(), if matches!(selection,Selection::Applications{..}) => {
                if let Selection::Applications { executables, ports } = &selection {
                    if let Err(error) = crate::firewall::verify(executables.clone(),ports.clone()).await { break Err(error); }
                }
            },
            event = events_rx.recv() => {
                let Some(TransportEvent::ConnectionEstablished {connection_id,dst_addr,tunnel_info,command_tx,..}) = event else { break Err(anyhow::anyhow!("capture channel closed")); };
                let tx = command_tx.unwrap_or_else(|| commands_tx.clone());
                let process = match tunnel_info { TunnelInfo::LocalRedirector { pid:Some(pid),process_name,.. } => Some(ProcessInfo{pid,process_name}), _ => None };
                if !connection_id.is_tcp() || !process.as_ref().is_some_and(|p| spec.should_intercept(p)) { let _ = tx.send(TransportCommand::CloseConnection(connection_id,false)); continue; }
                let stream = crate::transport::TransportStream::new(connection_id,tx);
                let Ok(permit) = slots.clone().try_acquire_owned() else { drop(stream); continue; };
                let engine = engine.clone();
                tasks.spawn(async move { let _permit = permit; let _ = tokio::time::timeout(Duration::from_secs(300),engine.serve(stream,dst_addr)).await; });
            },
            _ = tasks.join_next(), if !tasks.is_empty() => {},
        }
    };
    let _ = shutdown_tx.send(());
    tasks.shutdown().await;
    if !capture_finished {
        if tokio::time::timeout(Duration::from_secs(5), &mut capture).await.is_err() { capture.abort(); let _ = capture.await; }
    }
    drop(owned_guard); // Application mode never owns or kills a user's process.
    result
}
