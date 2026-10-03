//! Durable metadata-only audit. Existing paths are refused, never truncated.
use crate::{detectors::RULE_IDS, inspection::Decision};
use anyhow::{Context, Result, bail};
use serde::Serialize;
use std::{
    fs::{File, OpenOptions},
    io::Write,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

pub struct Audit {
    file: File,
    sequence: u64,
    failed: bool,
    bytes_written: u64,
    max_bytes: u64,
}
#[derive(Serialize)]
struct Event<'a> {
    event_id: u64,
    timestamp_ms: u128,
    host: &'a str,
    method: &'a str,
    body_len: usize,
    reason: &'a str,
    rules: &'a [&'static str],
    action: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    policy_revision: Option<u64>,
}
impl Audit {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options
            .open(path.as_ref())
            .context("cannot create exclusive audit file (existing paths are refused)")?;
        Ok(Self {
            file,
            sequence: 0,
            failed: false,
            bytes_written: 0,
            max_bytes: 64 * 1024 * 1024,
        })
    }
    pub fn with_max_bytes(mut self, max_bytes: u64) -> Self {
        self.max_bytes = max_bytes;
        self
    }
    pub fn record(
        &mut self,
        host: &str,
        method: &str,
        body_len: usize,
        decision: &Decision,
    ) -> Result<u64> {
        self.record_with_policy(host, method, body_len, decision, None)
    }
    pub fn record_with_policy(
        &mut self,
        host: &str,
        method: &str,
        body_len: usize,
        decision: &Decision,
        policy: Option<(u64, &str)>,
    ) -> Result<u64> {
        if self.failed {
            bail!("audit writer failed previously");
        }
        // Do not serialize caller-supplied query/content through metadata fields.
        if host.is_empty()
            || host.len() > 253
            || !host
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b".-:[]".contains(&b))
        {
            bail!("invalid audit host");
        }
        if !matches!(
            method,
            "GET" | "HEAD" | "POST" | "PUT" | "PATCH" | "DELETE" | "OPTIONS" | "CONNECT" | "TRACE"
        ) {
            bail!("invalid audit method");
        }
        if !(matches!(
            decision.reason,
            "clean" | "sensitive_data" | "uninspectable_request"
        ) || (policy.is_some() && decision.reason == "policy_unavailable"))
            || decision.rules.iter().any(|id| !RULE_IDS.contains(id))
        {
            bail!("invalid audit decision");
        }
        if decision.reason == "clean" && !decision.rules.is_empty() {
            bail!("inconsistent audit decision");
        }
        if let Some((_, action)) = policy {
            let valid = match decision.reason {
                "clean" => action == "allow",
                "sensitive_data" => {
                    !decision.rules.is_empty() && matches!(action, "monitor" | "block")
                }
                _ => action == "block" && decision.rules.is_empty(),
            };
            if !valid {
                bail!("inconsistent policy action");
            }
        }
        let event_id = self
            .sequence
            .checked_add(1)
            .context("audit sequence exhausted")?;
        let event = Event {
            event_id,
            timestamp_ms: SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis(),
            host,
            method,
            body_len,
            reason: decision.reason,
            rules: &decision.rules,
            policy_revision: policy.map(|p| p.0),
            action: if let Some((_, action)) = policy {
                action
            } else if decision.reason == "clean" {
                "allow"
            } else {
                "block"
            },
        };
        let mut line = serde_json::to_vec(&event)?;
        line.push(b'\n');
        if self.bytes_written.saturating_add(line.len() as u64) > self.max_bytes {
            self.failed = true;
            bail!("audit capacity exhausted");
        }
        // A partial write or failed sync poisons this writer; it cannot later allow.
        if let Err(error) = self
            .file
            .write_all(&line)
            .and_then(|_| self.file.sync_data())
        {
            self.failed = true;
            return Err(error).context("audit durability failed");
        }
        self.sequence = event_id;
        self.bytes_written += line.len() as u64;
        Ok(event_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inspection::inspect;
    #[test]
    fn metadata_only_durable_sequence_and_preservation() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("audit.jsonl");
        let mut audit = Audit::open(&path).unwrap();
        let decision = inspect(
            "POST",
            "/?secret=person%40example.test",
            "text/plain",
            b"4111111111111111",
        );
        assert_eq!(
            audit.record("chatgpt.com", "POST", 16, &decision).unwrap(),
            1
        );
        assert_eq!(
            audit
                .record(
                    "chatgpt.com",
                    "GET",
                    0,
                    &Decision {
                        reason: "clean",
                        rules: vec![]
                    }
                )
                .unwrap(),
            2
        );
        let raw = std::fs::read_to_string(&path).unwrap();
        assert!(
            !raw.contains("person")
                && !raw.contains("4111")
                && !raw.contains("secret")
                && !raw.contains("masked")
                && !raw.contains("path")
        );
        let events: Vec<serde_json::Value> = raw
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(events[0]["action"], "block");
        assert_eq!(events[1]["event_id"], 2);
        assert!(Audit::open(&path).is_err());
        assert_eq!(std::fs::read_to_string(&path).unwrap(), raw);
        assert!(Audit::open(dir.path()).is_err());
        assert!(Audit::open(dir.path().join("missing").join("audit")).is_err());
    }
    #[test]
    fn rejects_content_disguised_as_metadata() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("audit");
        let mut audit = Audit::open(&path).unwrap();
        let clean = Decision {
            reason: "clean",
            rules: vec![],
        };
        assert!(
            audit
                .record("example.test/?secret=a", "GET", 0, &clean)
                .is_err()
        );
        assert!(
            audit
                .record("example.test", "person@example.test", 0, &clean)
                .is_err()
        );
        assert!(
            audit
                .record(
                    "example.test",
                    "GET",
                    0,
                    &Decision {
                        reason: "secret",
                        rules: vec![]
                    }
                )
                .is_err()
        );
        assert!(
            audit
                .record(
                    "example.test",
                    "GET",
                    0,
                    &Decision {
                        reason: "sensitive_data",
                        rules: vec!["person@example.test"]
                    }
                )
                .is_err()
        );
        assert_eq!(std::fs::metadata(&path).unwrap().len(), 0);
    }
    #[cfg(unix)]
    #[test]
    fn io_failure_poisoned_and_readonly_sink_sync_failure() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("readonly");
        std::fs::write(&path, b"").unwrap();
        let file = File::open(&path).unwrap();
        let mut audit = Audit {
            file,
            sequence: 0,
            failed: false,
            bytes_written: 0,
            max_bytes: 64 * 1024 * 1024,
        };
        let clean = Decision {
            reason: "clean",
            rules: vec![],
        };
        assert!(audit.record("example.test", "GET", 0, &clean).is_err());
        assert!(audit.failed);
        assert_eq!(audit.sequence, 0);
        assert!(audit.record("example.test", "GET", 0, &clean).is_err());
        let file = OpenOptions::new().write(true).open("/dev/null").unwrap();
        let mut audit = Audit {
            file,
            sequence: 0,
            failed: false,
            bytes_written: 0,
            max_bytes: 64 * 1024 * 1024,
        };
        assert!(audit.record("example.test", "GET", 0, &clean).is_err());
        assert!(audit.failed);
    }
    #[test]
    fn capacity_exhaustion_remains_fail_closed() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("audit");
        let mut audit = Audit::open(&path).unwrap().with_max_bytes(1);
        let clean = Decision {
            reason: "clean",
            rules: vec![],
        };
        for _ in 0..3 {
            assert!(audit.record("localhost", "GET", 0, &clean).is_err());
        }
        assert!(audit.failed);
        assert_eq!(std::fs::metadata(path).unwrap().len(), 0);
    }
}
