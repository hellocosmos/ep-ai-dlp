use std::{iter, os::windows::{ffi::OsStrExt, io::AsRawHandle}, path::PathBuf, process::Stdio};
use anyhow::{Result, anyhow, ensure};
use tokio::{net::windows::named_pipe::{NamedPipeServer, PipeMode, ServerOptions}, process::{Command,Child}, sync::{mpsc::{Sender,UnboundedReceiver,UnboundedSender,unbounded_channel},oneshot}};
use windows::{core::{PCWSTR,w}, Win32::{Foundation::{CloseHandle,HANDLE,HLOCAL,LocalFree}, Security::{GetTokenInformation,TOKEN_ELEVATION,TOKEN_QUERY,TokenElevation,SECURITY_ATTRIBUTES,PSECURITY_DESCRIPTOR,Authorization::ConvertStringSecurityDescriptorToSecurityDescriptorW}, System::{Threading::{GetCurrentProcess,OpenProcessToken},Pipes::GetNamedPipeClientProcessId}, UI::{Shell::ShellExecuteW,WindowsAndMessaging::SW_HIDE}}};
use crate::{intercept_conf::InterceptConf,messages::{TransportCommand,TransportEvent},packet_sources::{IPC_BUF_SIZE,PacketSourceConf,PacketSourceTask,forward_packets},shutdown};

pub struct WindowsConf { pub executable_path: PathBuf }

pub fn elevated() -> Result<bool> {
    unsafe {
        let mut handle = HANDLE::default();
        OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&mut handle)?;
        let mut elevation = TOKEN_ELEVATION::default(); let mut length = 0;
        let result = GetTokenInformation(handle,TokenElevation,Some(&mut elevation as *mut _ as *mut _),std::mem::size_of::<TOKEN_ELEVATION>() as u32,&mut length);
        let _ = CloseHandle(handle); result?;
        Ok(elevation.TokenIsElevated != 0)
    }
}

impl PacketSourceConf for WindowsConf {
    type Task = WindowsTask;
    type Data = UnboundedSender<InterceptConf>;
    fn name(&self) -> &'static str { "Windows proxy" }
    async fn build(self, transport_events_tx: Sender<TransportEvent>, transport_commands_rx: UnboundedReceiver<TransportCommand>, shutdown: shutdown::Receiver) -> Result<(Self::Task,Self::Data)> {
        let pipe_name = format!(r"\\.\pipe\mitmproxy-transparent-proxy-{}",std::process::id());
        let direct = elevated()?;
        let mut options = ServerOptions::new();
        options.pipe_mode(PipeMode::Message).first_pipe_instance(true).max_instances(1).in_buffer_size(IPC_BUF_SIZE as u32).out_buffer_size(IPC_BUF_SIZE as u32).reject_remote_clients(true);
        let ipc_server = if direct {
            unsafe {
                let mut descriptor = PSECURITY_DESCRIPTOR::default();
                ConvertStringSecurityDescriptorToSecurityDescriptorW(w!("D:P(A;;GA;;;SY)(A;;GA;;;BA)"),1,&mut descriptor,None)?;
                let mut attributes = SECURITY_ATTRIBUTES { nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32, lpSecurityDescriptor: descriptor.0, bInheritHandle: false.into() };
                let result = options.create_with_security_attributes_raw(&pipe_name,&mut attributes as *mut _ as *mut _);
                let _ = LocalFree(Some(HLOCAL(descriptor.0))); result?
            }
        } else { options.create(&pipe_name)? };
        // Services run in session 0: never invoke an interactive runas dialog there.
        let child = if direct {
            Some(Command::new(&self.executable_path).arg(&pipe_name).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).creation_flags(0x08000000).kill_on_drop(true).spawn()?)
        } else {
            let pipe = pipe_name.encode_utf16().chain(iter::once(0)).collect::<Vec<_>>();
            let executable = self.executable_path.as_os_str().encode_wide().chain(iter::once(0)).collect::<Vec<_>>();
            let result = unsafe { ShellExecuteW(None,w!("runas"),PCWSTR::from_raw(executable.as_ptr()),PCWSTR::from_raw(pipe.as_ptr()),None,SW_HIDE) };
            ensure!(result.0 as usize > 32, "Failed to start the interception process as administrator");
            None
        };
        let (conf_tx,conf_rx) = unbounded_channel();
        Ok((WindowsTask{ipc_server,transport_events_tx,transport_commands_rx,conf_rx,shutdown,child,ready:None},conf_tx))
    }
}

pub struct WindowsTask {
    ipc_server: NamedPipeServer,
    transport_events_tx: Sender<TransportEvent>,
    transport_commands_rx: UnboundedReceiver<TransportCommand>,
    conf_rx: UnboundedReceiver<InterceptConf>,
    shutdown: shutdown::Receiver,
    child: Option<Child>,
    ready: Option<oneshot::Sender<()>>,
}
impl WindowsTask {
    pub fn readiness(&mut self) -> oneshot::Receiver<()> { let (tx,rx)=oneshot::channel(); self.ready=Some(tx); rx }
}
impl PacketSourceTask for WindowsTask {
    async fn run(mut self) -> Result<()> {
        tokio::select! {
            result = self.ipc_server.connect() => result?,
            _ = self.shutdown.recv() => return Ok(()),
        }
        if let Some(child) = &self.child {
            let mut peer = 0;
            unsafe { GetNamedPipeClientProcessId(HANDLE(self.ipc_server.as_raw_handle()), &mut peer)?; }
            ensure!(Some(peer) == child.id(), "unexpected redirector IPC client");
        }
        let result = forward_packets(self.ipc_server,self.transport_events_tx,self.transport_commands_rx,self.conf_rx,self.shutdown,self.ready).await;
        if let Some(mut child) = self.child { let _=child.kill().await; let _=child.wait().await; }
        result.map_err(|e|anyhow!("redirector transport stopped: {e}"))
    }
}
