//! Bounded, durable metadata queue. Upload retries are idempotent by event UUID.
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

pub const MAX_QUEUE_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_EVENTS: usize = 4096;
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedEvent {
    pub event_id: Uuid,
    pub timestamp_ms: u64,
    pub policy_revision: u64,
    pub host: String,
    pub method: String,
    pub body_len: usize,
    pub reason: String,
    pub rules: Vec<String>,
    pub action: String,
}
impl ManagedEvent {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.host.is_empty()
                && self.host.len() <= 253
                && self
                    .host
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b".-:[]".contains(&b)),
            "event_host_invalid"
        );
        ensure!(
            ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"]
                .contains(&self.method.as_str()),
            "event_method_invalid"
        );
        ensure!(
            self.body_len <= crate::inspection::MAX_BODY_BYTES
                && self.rules.len() <= crate::detectors::RULE_IDS.len()
                && self
                    .rules
                    .iter()
                    .all(|r| crate::detectors::RULE_IDS.contains(&r.as_str())),
            "event_invalid"
        );
        let valid = match self.reason.as_str() {
            "clean" => self.rules.is_empty() && self.action == "allow",
            "sensitive_data" => {
                !self.rules.is_empty() && ["block", "monitor"].contains(&self.action.as_str())
            }
            "uninspectable_request" | "policy_unavailable" => {
                self.rules.is_empty() && self.action == "block"
            }
            _ => false,
        };
        ensure!(valid, "event_decision_invalid");
        Ok(())
    }
}
pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let temporary = path.with_extension(format!("{}.tmp", Uuid::new_v4()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let result = (|| {
        let mut file = options.open(&temporary)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)?;
        #[cfg(unix)]
        {
            fs::File::open(path.parent().unwrap())?.sync_all()?;
        }
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}
pub struct Outbox {
    path: PathBuf,
    events: Vec<ManagedEvent>,
    bytes: usize,
    failed: bool,
}
impl Outbox {
    pub fn open(path: PathBuf) -> Result<Self> {
        if !path.exists() {
            atomic_write(&path, b"")?;
        }
        ensure!(
            fs::metadata(&path)?.len() <= MAX_QUEUE_BYTES as u64,
            "outbox_too_large"
        );
        let bytes = fs::read(&path)?;
        ensure!(
            bytes.is_empty() || bytes.last() == Some(&b'\n'),
            "outbox_partial_write"
        );
        let events = bytes
            .split(|b| *b == b'\n')
            .filter(|v| !v.is_empty())
            .map(|line| {
                let event: ManagedEvent = serde_json::from_slice(line)?;
                event.validate()?;
                Ok(event)
            })
            .collect::<Result<Vec<_>>>()?;
        ensure!(events.len() <= MAX_EVENTS, "outbox_full");
        Ok(Self {
            path,
            events,
            bytes: bytes.len(),
            failed: false,
        })
    }
    pub fn append(&mut self, event: ManagedEvent) -> Result<()> {
        ensure!(!self.failed, "outbox_failed");
        event.validate()?;
        let mut bytes = serde_json::to_vec(&event)?;
        bytes.push(b'\n');
        ensure!(
            self.events.len() < MAX_EVENTS && self.bytes + bytes.len() <= MAX_QUEUE_BYTES,
            "outbox_full"
        );
        let result = (|| {
            let mut file = OpenOptions::new().append(true).open(&self.path)?;
            file.write_all(&bytes)?;
            file.sync_data()?;
            Ok::<_, anyhow::Error>(())
        })();
        if let Err(error) = result {
            self.failed = true;
            return Err(error);
        }
        self.bytes += bytes.len();
        self.events.push(event);
        Ok(())
    }
    pub fn batch(&self) -> Vec<ManagedEvent> {
        self.events.iter().take(100).cloned().collect()
    }
    pub fn acknowledge(&mut self, ids: &[Uuid]) -> Result<()> {
        ensure!(!self.failed, "outbox_failed");
        let remaining = self
            .events
            .iter()
            .filter(|e| !ids.contains(&e.event_id))
            .cloned()
            .collect::<Vec<_>>();
        if remaining.len() == self.events.len() {
            return Ok(());
        }
        let mut bytes = Vec::new();
        for event in &remaining {
            serde_json::to_writer(&mut bytes, event)?;
            bytes.push(b'\n');
        }
        atomic_write(&self.path, &bytes)?;
        self.events = remaining;
        self.bytes = bytes.len();
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn event() -> ManagedEvent {
        ManagedEvent {
            event_id: Uuid::new_v4(),
            timestamp_ms: 1,
            policy_revision: 1,
            host: "aidlp.test".into(),
            method: "POST".into(),
            body_len: 4,
            reason: "clean".into(),
            rules: vec![],
            action: "allow".into(),
        }
    }
    #[test]
    fn durable_retry_and_ack() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("events.jsonl");
        let mut out = Outbox::open(path.clone()).unwrap();
        let e = event();
        out.append(e.clone()).unwrap();
        drop(out);
        let mut out = Outbox::open(path.clone()).unwrap();
        assert_eq!(out.batch()[0].event_id, e.event_id);
        out.acknowledge(&[e.event_id]).unwrap();
        assert!(Outbox::open(path).unwrap().batch().is_empty());
    }
    #[test]
    fn rejects_content_and_partial_queue() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("events.jsonl");
        let mut out = Outbox::open(path.clone()).unwrap();
        let mut e = event();
        e.host = "aidlp.test/?secret=abc".into();
        assert!(out.append(e).is_err());
        fs::write(&path, b"{").unwrap();
        assert!(Outbox::open(path).is_err());
    }
    #[test]
    fn capacity_and_io_failure_preserve_unacknowledged_evidence() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("events.jsonl");
        let entries = (0..MAX_EVENTS).map(|_| event()).collect::<Vec<_>>();
        let mut raw = Vec::new();
        for entry in &entries {
            serde_json::to_writer(&mut raw, entry).unwrap();
            raw.push(b'\n');
        }
        fs::write(&path, &raw).unwrap();
        let mut out = Outbox::open(path.clone()).unwrap();
        assert!(out.append(event()).is_err());
        assert_eq!(fs::read(&path).unwrap(), raw);
        out.acknowledge(&[entries[0].event_id]).unwrap();
        out.append(event()).unwrap();
        out.acknowledge(&[entries[1].event_id]).unwrap();
        let preserved = path.with_extension("preserved");
        fs::rename(&path, &preserved).unwrap();
        assert!(out.append(event()).is_err());
        fs::rename(&preserved, &path).unwrap();
        assert!(out.append(event()).is_err());
        assert!(out.acknowledge(&[entries[1].event_id]).is_err());
        assert_eq!(Outbox::open(path).unwrap().events.len(), MAX_EVENTS - 1);
    }
}
