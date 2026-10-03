use crate::intercept_conf::InterceptConf;
use crate::ipc::PacketWithMeta;
use crate::messages::{
    NetworkCommand, NetworkEvent, SmolPacket, TransportCommand, TransportEvent, TunnelInfo,
};
use crate::network::add_network_layer;
use crate::{MAX_PACKET_SIZE, ipc, shutdown};
use anyhow::{Context, Result, anyhow};
use prost::Message;
use prost::bytes::Bytes;
use std::future::Future;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::sync::mpsc;
use tokio::sync::mpsc::{Sender, UnboundedReceiver};

#[cfg(target_os = "linux")]
pub mod linux;
#[cfg(target_os = "macos")]
pub mod macos;
#[cfg(target_os = "linux")]
pub mod tun;
pub mod udp;
#[cfg(windows)]
pub mod windows;
pub mod wireguard;

pub trait PacketSourceConf {
    type Task: PacketSourceTask + Send + 'static;
    type Data: Send + 'static;

    fn name(&self) -> &'static str;

    fn build(
        self,
        transport_events_tx: mpsc::Sender<TransportEvent>,
        transport_commands_rx: mpsc::UnboundedReceiver<TransportCommand>,
        shutdown: shutdown::Receiver,
    ) -> impl Future<Output = Result<(Self::Task, Self::Data)>> + Send;
}

pub trait PacketSourceTask: Send {
    fn run(self) -> impl Future<Output = Result<()>> + Send;
}

pub const IPC_BUF_SIZE: usize = MAX_PACKET_SIZE + 1024;

/// Feed packets from a socket into smol, and the other way around.
#[allow(dead_code)]
async fn forward_packets<T: AsyncRead + AsyncWrite + Unpin>(
    channel: T,
    transport_events_tx: Sender<TransportEvent>,
    transport_commands_rx: UnboundedReceiver<TransportCommand>,
    mut conf_rx: UnboundedReceiver<InterceptConf>,
    shutdown: shutdown::Receiver,
    mut ready: Option<tokio::sync::oneshot::Sender<()>>,
) -> Result<()> {
    let (mut reader, mut writer) = tokio::io::split(channel);
    let (mut network_task_handle, net_tx, mut net_rx) =
        add_network_layer(transport_events_tx, transport_commands_rx, shutdown);

    // Read and write must make progress independently. A blocked IPC write must
    // never stop draining the reverse direction and form a bounded-queue cycle.
    let receive = async {
        let mut buf = Vec::with_capacity(IPC_BUF_SIZE);
        loop {
            buf.clear();
            reader.read_buf(&mut buf).await.context("redirector IPC read failed")?;
            if buf.is_empty() { return Err::<(), anyhow::Error>(anyhow!("redirect daemon exited prematurely")); }
            let PacketWithMeta { data, tunnel_info, configuration_applied } = PacketWithMeta::decode(buf.as_slice())
                .context("invalid redirector IPC message")?;
            if configuration_applied {
                anyhow::ensure!(data.is_empty(), "invalid configuration ACK");
                if let Some(ready) = ready.take() { let _ = ready.send(()); }
                continue;
            }
            let Ok(mut packet) = SmolPacket::try_from(data.to_vec()) else {
                log::warn!("Skipping invalid packet");
                continue;
            };
            // This vendored runtime is TCP-only. Drop captured UDP/QUIC before
            // allocating transport state or commands, including under saturation.
            if packet.transport_protocol() != smoltcp::wire::IpProtocol::Tcp {
                continue;
            }
            packet.fill_ip_checksum();
            let event = NetworkEvent::ReceivePacket {
                packet,
                tunnel_info: TunnelInfo::LocalRedirector {
                    pid: tunnel_info.as_ref().and_then(|t| t.pid),
                    process_name: tunnel_info.and_then(|t| t.process_name),
                    remote_endpoint: None,
                },
            };
            if net_tx.try_send(event).is_err() {
                log::warn!("Dropping incoming packet, TCP channel is full");
            }
        }
    };
    let send = async {
        let mut buf = Vec::with_capacity(IPC_BUF_SIZE);
        loop {
            buf.clear();
            let message = tokio::select! {
                Some(conf) = conf_rx.recv() => ipc::from_proxy::Message::InterceptConf(conf.into()),
                Some(NetworkCommand::SendPacket(packet)) = net_rx.recv() =>
                    ipc::from_proxy::Message::Packet(ipc::Packet { data: Bytes::from(packet.into_inner()) }),
                else => return Ok::<(), anyhow::Error>(()),
            };
            ipc::FromProxy { message: Some(message) }.encode(&mut buf)?;
            writer.write_all(&buf).await.context("redirector IPC write failed")?;
        }
    };
    let result = tokio::select! {
        exit = &mut network_task_handle => exit.context("network task panic")?.context("network task error"),
        result = receive => result,
        result = send => result,
    };
    network_task_handle.abort();
    log::info!("Redirector shutting down");
    result
}
