//! Windows SCM host for the transparent inspector. SCM recovery owns restart policy.
#[cfg(not(windows))]
fn main() { eprintln!("aidlp-service requires Windows"); std::process::exit(1); }

#[cfg(windows)]
mod service {
    use anyhow::{Context,Result,ensure};
    use std::{ffi::OsString,path::PathBuf,sync::OnceLock,time::Duration};
    use windows_service::{define_windows_service,service_dispatcher,service::{ServiceControl,ServiceControlAccept,ServiceExitCode,ServiceState,ServiceStatus,ServiceType},service_control_handler::{self,ServiceControlHandlerResult}};
    const NAME: &str = "FastpaceAiDlp";
    static CONFIG: OnceLock<PathBuf> = OnceLock::new();
    define_windows_service!(entry, service_main);

    pub fn start() -> Result<()> {
        let args=std::env::args_os().collect::<Vec<_>>();
        ensure!(args.len()==3 && args[1]=="--config", "usage: aidlp-service --config FILE");
        let config=PathBuf::from(&args[2]); ensure!(config.is_absolute(),"service config must be absolute");
        CONFIG.set(config).map_err(|_|anyhow::anyhow!("service already initialized"))?;
        service_dispatcher::start(NAME,entry)?; Ok(())
    }
    fn status(state:ServiceState, failure:bool) -> ServiceStatus {
        ServiceStatus{service_type:ServiceType::OWN_PROCESS,current_state:state,
            controls_accepted:if state==ServiceState::Running {ServiceControlAccept::STOP|ServiceControlAccept::SHUTDOWN}else{ServiceControlAccept::empty()},
            exit_code:if failure{ServiceExitCode::ServiceSpecific(1)}else{ServiceExitCode::Win32(0)},
            checkpoint:if state==ServiceState::StartPending||state==ServiceState::StopPending{1}else{0},
            wait_hint:Duration::from_secs(20),process_id:None}
    }
    fn service_main(_arguments:Vec<OsString>) {
        if let Err(error)=run() {
            if let Some(parent)=CONFIG.get().and_then(|p|p.parent()) {
                // Only structured errors/paths; configuration contents and secrets are never logged.
                let _=std::fs::write(parent.join("service-error.log"),format!("{error:#}\n"));
            }
        }
    }
    fn run() -> Result<()> {
        let (stop,rx)=tokio::sync::watch::channel(false);
        let handler=service_control_handler::register(NAME,move |event|match event {
            ServiceControl::Stop|ServiceControl::Shutdown=>{let _=stop.send(true);ServiceControlHandlerResult::NoError},
            ServiceControl::Interrogate=>ServiceControlHandlerResult::NoError,
            _=>ServiceControlHandlerResult::NotImplemented,
        })?;
        handler.set_service_status(status(ServiceState::StartPending,false))?;
        let result:Result<()>= (|| {
            let config:aidlp_native::config::Config=serde_json::from_slice(&std::fs::read(CONFIG.get().context("missing service configuration")?)?)?;
            ensure!(matches!(config.mode,aidlp_native::config::Mode::Applications{..}),"service requires applications mode");
            ensure!(config.management.is_some(),"service requires managed enrollment");
            handler.set_service_status(status(ServiceState::Running,false))?;
            // SCM Running means the service exists; agent readiness remains independently reported.
            tokio::runtime::Runtime::new()?.block_on(aidlp_native::runtime::run(config,rx))
        })();
        handler.set_service_status(status(ServiceState::StopPending,false))?;
        handler.set_service_status(status(ServiceState::Stopped,result.is_err()))?;
        result
    }
}
#[cfg(windows)]
fn main() -> anyhow::Result<()> { service::start() }
