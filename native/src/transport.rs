//! Bounded adapter for mitmproxy's transport command protocol.
use bytes::{Buf, Bytes};
use mitmproxy::messages::{ConnectionId, TransportCommand};
use std::{
    io,
    pin::Pin,
    task::{Context, Poll},
};
use tokio::{
    io::{AsyncRead, AsyncWrite, ReadBuf},
    sync::{mpsc, oneshot},
};
const CHUNK: usize = 16 * 1024;
pub struct TransportStream {
    id: ConnectionId,
    tx: mpsc::UnboundedSender<TransportCommand>,
    read: Option<oneshot::Receiver<Vec<u8>>>,
    buffer: Bytes,
    drain: Option<oneshot::Receiver<()>>,
    eof: bool,
    write_closed: bool,
}
fn closed() -> io::Error {
    io::Error::new(io::ErrorKind::BrokenPipe, "capture transport closed")
}
impl TransportStream {
    pub fn new(id: ConnectionId, tx: mpsc::UnboundedSender<TransportCommand>) -> Self {
        Self {
            id,
            tx,
            read: None,
            buffer: Bytes::new(),
            drain: None,
            eof: false,
            write_closed: false,
        }
    }
    fn poll_drain(&mut self, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        if let Some(rx) = self.drain.as_mut() {
            match std::future::Future::poll(Pin::new(rx), cx) {
                Poll::Pending => return Poll::Pending,
                Poll::Ready(result) => {
                    self.drain = None;
                    if result.is_err() {
                        return Poll::Ready(Err(closed()));
                    }
                }
            }
        }
        Poll::Ready(Ok(()))
    }
}
impl AsyncRead for TransportStream {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        out: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        if out.remaining() == 0 {
            return Poll::Ready(Ok(()));
        }
        loop {
            if !self.buffer.is_empty() {
                let n = out.remaining().min(self.buffer.len());
                out.put_slice(&self.buffer[..n]);
                self.buffer.advance(n);
                return Poll::Ready(Ok(()));
            }
            if self.eof {
                return Poll::Ready(Ok(()));
            }
            if self.read.is_none() {
                let (tx, rx) = oneshot::channel();
                self.tx
                    .send(TransportCommand::ReadData(self.id, CHUNK as u32, tx))
                    .map_err(|_| closed())?;
                self.read = Some(rx);
            }
            match std::future::Future::poll(Pin::new(self.read.as_mut().unwrap()), cx) {
                Poll::Pending => return Poll::Pending,
                Poll::Ready(result) => {
                    self.read = None;
                    let data = result.map_err(|_| closed())?;
                    if data.len() > CHUNK {
                        return Poll::Ready(Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            "oversized transport read",
                        )));
                    }
                    self.eof = data.is_empty();
                    self.buffer = Bytes::from(data);
                }
            }
        }
    }
}
impl AsyncWrite for TransportStream {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        data: &[u8],
    ) -> Poll<io::Result<usize>> {
        if self.write_closed {
            return Poll::Ready(Err(closed()));
        }
        std::task::ready!(self.poll_drain(cx))?;
        let n = data.len().min(CHUNK);
        if n == 0 {
            return Poll::Ready(Ok(0));
        }
        self.tx
            .send(TransportCommand::WriteData(self.id, data[..n].to_vec()))
            .map_err(|_| closed())?;
        let (tx, rx) = oneshot::channel();
        self.tx
            .send(TransportCommand::DrainWriter(self.id, tx))
            .map_err(|_| closed())?;
        self.drain = Some(rx);
        Poll::Ready(Ok(n))
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        self.poll_drain(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        std::task::ready!(self.poll_drain(cx))?;
        if !self.write_closed {
            self.tx
                .send(TransportCommand::CloseConnection(self.id, true))
                .map_err(|_| closed())?;
            self.write_closed = true;
        }
        Poll::Ready(Ok(()))
    }
}
impl Drop for TransportStream {
    fn drop(&mut self) {
        // A completed shutdown already queued FIN after the response. Aborting
        // here would discard queued TLS records before the peer receives them.
        if !self.write_closed {
            let _ = self
                .tx
                .send(TransportCommand::CloseConnection(self.id, false));
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    fn pair() -> (TransportStream, mpsc::UnboundedReceiver<TransportCommand>) {
        let (tx, rx) = mpsc::unbounded_channel();
        (
            TransportStream::new(
                mitmproxy::messages::ConnectionIdGenerator::tcp().next_id(),
                tx,
            ),
            rx,
        )
    }
    #[tokio::test]
    async fn graceful_shutdown_is_not_overridden_by_drop() {
        let (mut stream, mut rx) = pair();
        stream.shutdown().await.unwrap();
        drop(stream);
        assert!(matches!(
            rx.recv().await,
            Some(TransportCommand::CloseConnection(_, true))
        ));
        assert!(rx.recv().await.is_none());
    }
    #[tokio::test]
    async fn writes_wait_for_drain_and_are_bounded() {
        let (mut stream, mut rx) = pair();
        let task = tokio::spawn(async move {
            stream.write_all(&vec![42; CHUNK * 2]).await.unwrap();
            stream.flush().await.unwrap();
        });
        let Some(TransportCommand::WriteData(_, data)) = rx.recv().await else {
            panic!()
        };
        assert_eq!(data.len(), CHUNK);
        let Some(TransportCommand::DrainWriter(_, ack)) = rx.recv().await else {
            panic!()
        };
        assert!(
            tokio::time::timeout(std::time::Duration::from_millis(20), rx.recv())
                .await
                .is_err()
        );
        ack.send(()).unwrap();
        assert!(
            matches!(rx.recv().await,Some(TransportCommand::WriteData(_,data)) if data.len()==CHUNK)
        );
        let Some(TransportCommand::DrainWriter(_, ack)) = rx.recv().await else {
            panic!()
        };
        ack.send(()).unwrap();
        task.await.unwrap();
        assert!(matches!(
            rx.recv().await,
            Some(TransportCommand::CloseConnection(_, false))
        ));
    }
    #[tokio::test]
    async fn small_reads_preserve_data_and_eof() {
        let (mut stream, mut rx) = pair();
        let task = tokio::spawn(async move {
            let mut buf = [0; 2];
            stream.read_exact(&mut buf).await.unwrap();
            assert_eq!(&buf, b"ab");
            let mut rest = Vec::new();
            stream.read_to_end(&mut rest).await.unwrap();
            assert_eq!(&rest, b"c");
        });
        let Some(TransportCommand::ReadData(_, _, tx)) = rx.recv().await else {
            panic!()
        };
        tx.send(b"abc".to_vec()).unwrap();
        let Some(TransportCommand::ReadData(_, _, tx)) = rx.recv().await else {
            panic!()
        };
        tx.send(vec![]).unwrap();
        task.await.unwrap();
    }
}

/// Replays a sniffed TLS prefix while forwarding writes to the original stream.
pub struct ReplayStream<S> {
    prefix: Bytes,
    inner: S,
}
impl<S> ReplayStream<S> {
    pub fn new(prefix: Vec<u8>, inner: S) -> Self {
        Self {
            prefix: Bytes::from(prefix),
            inner,
        }
    }
}
impl<S: AsyncRead + Unpin> AsyncRead for ReplayStream<S> {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        out: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        if !self.prefix.is_empty() {
            let n = out.remaining().min(self.prefix.len());
            out.put_slice(&self.prefix[..n]);
            self.prefix.advance(n);
            Poll::Ready(Ok(()))
        } else {
            Pin::new(&mut self.inner).poll_read(cx, out)
        }
    }
}
impl<S: AsyncWrite + Unpin> AsyncWrite for ReplayStream<S> {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.inner).poll_write(cx, buf)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_shutdown(cx)
    }
}
