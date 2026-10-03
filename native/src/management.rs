//! Device enrollment, pinned policy trust, and bounded authenticated telemetry.
use crate::{
    managed_policy::{self, Envelope, Policy},
    outbox::{atomic_write, ManagedEvent, Outbox},
};
use anyhow::{ensure, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU8, Ordering},
        Arc, Mutex, RwLock,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ManagementConfig {
    pub server_url: String,
    pub tenant_id: Uuid,
    pub device_id: Uuid,
    pub device_token: String,
    pub public_key_base64: String,
    pub key_id: String,
    pub ca_pem: Option<String>,
    pub cache_dir: PathBuf,
    #[serde(default)]
    pub allow_insecure_loopback: bool,
    #[serde(default = "poll_default")]
    pub poll_seconds: u64,
}
fn poll_default() -> u64 {
    3
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EnrollmentConfig {
    pub server_url: String,
    pub enrollment_token: String,
    pub tenant_id: Uuid,
    pub public_key_base64: String,
    pub key_id: String,
    pub ca_pem: Option<String>,
    pub device_name: String,
    #[serde(default)]
    pub allow_insecure_loopback: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Enrolled {
    device_id: Uuid,
    device_token: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Ack {
    accepted_event_ids: Vec<Uuid>,
}
fn client(server: &str, ca: Option<&str>, insecure: bool) -> Result<reqwest::Client> {
    let url = reqwest::Url::parse(server)?;
    ensure!(
        url.username().is_empty()
            && url.password().is_none()
            && url.query().is_none()
            && url.fragment().is_none()
            && url.path() == "/",
        "management_url_invalid"
    );
    let local = matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "[::1]"));
    ensure!(
        url.scheme() == "https" || (url.scheme() == "http" && local && insecure),
        "management_requires_https"
    );
    let mut builder = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .connect_timeout(Duration::from_secs(5));
    if let Some(ca) = ca {
        ensure!(ca.len() <= 16384, "management_ca_too_large");
        builder = builder.add_root_certificate(reqwest::Certificate::from_pem(ca.as_bytes())?);
    }
    Ok(builder.build()?)
}
async fn response<T: DeserializeOwned>(mut response: reqwest::Response) -> Result<T> {
    ensure!(response.status().is_success(), "management_http_error");
    ensure!(
        response.content_length().unwrap_or(0) <= 65536,
        "management_response_too_large"
    );
    let mut body = Vec::new();
    while let Some(chunk) = response.chunk().await? {
        ensure!(
            body.len() + chunk.len() <= 65536,
            "management_response_too_large"
        );
        body.extend_from_slice(&chunk);
    }
    Ok(serde_json::from_slice(&body)?)
}
fn read_config<T: DeserializeOwned>(path: &Path) -> Result<T> {
    ensure!(
        fs::metadata(path)?.len() <= 65536,
        "management_config_too_large"
    );
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}
fn secure_directory(path: &Path) -> Result<()> {
    fs::create_dir_all(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    #[cfg(windows)]
    {
        use std::process::{Command, Stdio};
        // Service accounts do not have a reliable USERDOMAIN\USERNAME pair.
        // Resolve the process token identity through whoami and use its numeric SID.
        let identity = Command::new("whoami.exe")
            .args(["/user", "/fo", "csv", "/nh"])
            .output()?;
        ensure!(identity.status.success(), "credential_owner_unknown");
        let identity = String::from_utf8(identity.stdout)?;
        let sid = identity.trim().rsplit(',').next().unwrap_or("").trim_matches('"');
        ensure!(regex::Regex::new(r"^S-1-[0-9]+(?:-[0-9]+)+$")?.is_match(sid), "credential_owner_unknown");
        let mut command = Command::new("icacls.exe");
        command.arg(path)
            .arg("/inheritance:r")
            .arg("/grant:r")
            .arg(format!("*{sid}:(OI)(CI)F"));
        if sid == "S-1-5-18" {
            // The administrator installer/status tools must read service readiness.
            command.arg("*S-1-5-32-544:(OI)(CI)F");
        }
        let status = command.stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()?;
        ensure!(status.success(), "credential_directory_acl_failed");
    }
    Ok(())
}
fn validate_key(public: &str, id: &str) -> Result<()> {
    let key = STANDARD.decode(public)?;
    ensure!(
        key.len() == 32 && managed_policy::key_id(&key) == id,
        "enrollment_key_invalid"
    );
    Ok(())
}
pub async fn enroll(input: &Path, output: &Path) -> Result<()> {
    ensure!(!output.exists(), "management_config_already_exists");
    let config: EnrollmentConfig = read_config(input)?;
    validate_key(&config.public_key_base64, &config.key_id)?;
    let http = client(
        &config.server_url,
        config.ca_pem.as_deref(),
        config.allow_insecure_loopback,
    )?;
    let parent = output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    // Enrollment output must live in its own credential directory: do not change a user's workspace ACL.
    ensure!(
        parent != Path::new("."),
        "use_dedicated_management_directory"
    );
    ensure!(
        !parent.exists() || fs::read_dir(parent)?.next().is_none(),
        "enrollment_directory_must_be_empty"
    );
    secure_directory(parent)?;
    let enrolled:Enrolled=response(http.post(format!("{}/v1/enroll",config.server_url.trim_end_matches('/'))).json(&serde_json::json!({"token":config.enrollment_token,"name":config.device_name,"platform":std::env::consts::OS})).send().await?).await?;
    ensure!(
        enrolled.device_token.starts_with("dev_") && enrolled.device_token.len() == 47,
        "device_credential_invalid"
    );
    let management = ManagementConfig {
        server_url: config.server_url,
        tenant_id: config.tenant_id,
        device_id: enrolled.device_id,
        device_token: enrolled.device_token,
        public_key_base64: config.public_key_base64,
        key_id: config.key_id,
        ca_pem: config.ca_pem,
        cache_dir: parent.join("state"),
        allow_insecure_loopback: config.allow_insecure_loopback,
        poll_seconds: 3,
    };
    atomic_write(output, &serde_json::to_vec_pretty(&management)?)?;
    println!(
        "{}",
        serde_json::json!({"enrolled":true,"device_id":management.device_id})
    );
    Ok(())
}
pub struct Management {
    config: ManagementConfig,
    http: reqwest::Client,
    policy: RwLock<Option<Arc<Policy>>>,
    outbox: Mutex<Outbox>,
    scope: &'static str,
    targets: Vec<String>,
    state: AtomicU8,
}
impl Management {
    pub fn load(path: &Path, scope: &'static str, targets: Vec<String>) -> Result<Arc<Self>> {
        let config: ManagementConfig = read_config(path)?;
        validate_key(&config.public_key_base64, &config.key_id)?;
        ensure!(
            (1..=60).contains(&config.poll_seconds),
            "poll_interval_invalid"
        );
        ensure!(
            config.device_token.starts_with("dev_") && config.device_token.len() == 47,
            "device_credential_invalid"
        );
        let http = client(
            &config.server_url,
            config.ca_pem.as_deref(),
            config.allow_insecure_loopback,
        )?;
        secure_directory(&config.cache_dir)?;
        let path = config.cache_dir.join("policy.json");
        let cached = if path.exists() {
            // An expired authentic cache still supplies the persisted revision floor. It cannot allow traffic.
            let envelope: Envelope = read_config(&path)?;
            ensure!(envelope.payload_base64.len() <= 16384, "policy_too_large");
            let unsigned: Policy =
                serde_json::from_slice(&STANDARD.decode(&envelope.payload_base64)?)?;
            Some(Arc::new(managed_policy::verify(
                &envelope,
                &config.public_key_base64,
                config.device_id,
                config.tenant_id,
                unsigned.issued_at,
            )?))
        } else {
            None
        };
        let outbox = Outbox::open(config.cache_dir.join("events.jsonl"))?;
        Ok(Arc::new(Self {
            config,
            http,
            policy: RwLock::new(cached),
            outbox: Mutex::new(outbox),
            scope,
            targets,
            state: AtomicU8::new(0),
        }))
    }
    pub fn snapshot(&self) -> Option<Arc<Policy>> {
        self.policy.read().ok().and_then(|v| v.clone())
    }
    pub fn running(&self) {
        self.state.store(1, Ordering::Release);
    }
    pub fn stopped(&self) {
        self.state.store(2, Ordering::Release);
    }
    fn install(&self, envelope: Envelope) -> Result<()> {
        let candidate = managed_policy::verify(
            &envelope,
            &self.config.public_key_base64,
            self.config.device_id,
            self.config.tenant_id,
            managed_policy::now(),
        )?;
        let mut current = self
            .policy
            .write()
            .map_err(|_| anyhow::anyhow!("policy_lock_failed"))?;
        managed_policy::check_revision(&candidate, current.as_deref())?;
        if current.as_deref() == Some(&candidate) {
            return Ok(());
        }
        atomic_write(
            &self.config.cache_dir.join("policy.json"),
            &serde_json::to_vec(&envelope)?,
        )?;
        *current = Some(Arc::new(candidate));
        Ok(())
    }
    pub fn record(
        &self,
        host: &str,
        method: &str,
        body_len: usize,
        decision: &crate::inspection::Decision,
        action: &str,
        revision: u64,
    ) -> Result<()> {
        let event = ManagedEvent {
            event_id: Uuid::new_v4(),
            timestamp_ms: SystemTime::now()
                .duration_since(UNIX_EPOCH)?
                .as_millis()
                .try_into()?,
            policy_revision: revision,
            host: host.into(),
            method: method.into(),
            body_len,
            reason: decision.reason.into(),
            rules: decision.rules.iter().map(|v| v.to_string()).collect(),
            action: action.into(),
        };
        self.outbox
            .lock()
            .map_err(|_| anyhow::anyhow!("outbox_lock_failed"))?
            .append(event)
    }
    pub async fn synchronize(&self) -> Result<()> {
        let url = self.config.server_url.trim_end_matches('/');
        let policy_result = async {
            let envelope: Envelope = response(
                self.http
                    .get(format!("{url}/v1/device/policy"))
                    .bearer_auth(&self.config.device_token)
                    .send()
                    .await?,
            )
            .await?;
            self.install(envelope)
        }
        .await;
        // Bad policy never prevents retrying already-committed evidence.
        let snapshot = self.snapshot();
        let revision = snapshot.as_ref().map_or(0, |v| v.revision);
        let state = match self.state.load(Ordering::Acquire) {
            0 => "starting",
            2 => "stopped",
            _ => {
                if snapshot
                    .as_ref()
                    .is_some_and(|v| v.expires_at > managed_policy::now())
                {
                    "running"
                } else {
                    "policy_unavailable"
                }
            }
        };
        let events = self
            .outbox
            .lock()
            .map_err(|_| anyhow::anyhow!("outbox_lock_failed"))?
            .batch();
        let sent_ids = events.iter().map(|e| e.event_id).collect::<Vec<_>>();
        let report = serde_json::json!({"applied_revision":revision,"protection_scope":self.scope,"engine_state":state,"protected_targets":self.targets,"events":events});
        let ack: Ack = response(
            self.http
                .post(format!("{url}/v1/device/report"))
                .bearer_auth(&self.config.device_token)
                .json(&report)
                .send()
                .await?,
        )
        .await?;
        ensure!(
            ack.accepted_event_ids.len() <= 100
                && ack
                    .accepted_event_ids
                    .iter()
                    .all(|id| sent_ids.contains(id)),
            "event_ack_invalid"
        );
        self.outbox
            .lock()
            .map_err(|_| anyhow::anyhow!("outbox_lock_failed"))?
            .acknowledge(&ack.accepted_event_ids)?;
        policy_result
    }
    pub async fn run(self: Arc<Self>) {
        loop {
            let ok = self.synchronize().await.is_ok();
            let p = self.snapshot();
            let status = serde_json::json!({"device_id":self.config.device_id,"applied_revision":p.as_ref().map_or(0,|p|p.revision),"policy_valid":p.as_ref().is_some_and(|p|p.expires_at>managed_policy::now()),"last_sync_ok":ok,"engine_state":match self.state.load(Ordering::Acquire){0=>"starting",1=>"running",_=>"stopped"},"scope":self.scope,"timestamp":managed_policy::now()});
            let _ = atomic_write(
                &self.config.cache_dir.join("status.json"),
                &serde_json::to_vec(&status).unwrap_or_default(),
            );
            tokio::time::sleep(Duration::from_secs(self.config.poll_seconds)).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};
    fn fixture(dir: &Path, expired: bool) -> (PathBuf, Envelope) {
        let key = SigningKey::from_bytes(&[19; 32]);
        let public = STANDARD.encode(key.verifying_key().as_bytes());
        let clock = managed_policy::now();
        let policy = Policy {
            schema_version: 1,
            tenant_id: Uuid::nil(),
            device_id: Uuid::nil(),
            revision: 7,
            issued_at: clock - 120,
            expires_at: if expired { clock - 1 } else { clock + 300 },
            rules: crate::detectors::RULE_IDS
                .iter()
                .map(|id| (id.to_string(), managed_policy::RuleAction::Monitor))
                .collect(),
        };
        let bytes = serde_json::to_vec(&policy).unwrap();
        let envelope = Envelope {
            payload_base64: STANDARD.encode(&bytes),
            signature_base64: STANDARD.encode(key.sign(&bytes).to_bytes()),
            key_id: managed_policy::key_id(key.verifying_key().as_bytes()),
        };
        let config = ManagementConfig {
            server_url: "http://127.0.0.1:9".into(),
            tenant_id: Uuid::nil(),
            device_id: Uuid::nil(),
            device_token: format!("dev_{}", "x".repeat(43)),
            public_key_base64: public,
            key_id: envelope.key_id.clone(),
            ca_pem: None,
            cache_dir: dir.join("state"),
            allow_insecure_loopback: true,
            poll_seconds: 3,
        };
        fs::create_dir_all(&config.cache_dir).unwrap();
        fs::write(
            config.cache_dir.join("policy.json"),
            serde_json::to_vec(&envelope).unwrap(),
        )
        .unwrap();
        let path = dir.join("management.json");
        fs::write(&path, serde_json::to_vec(&config).unwrap()).unwrap();
        (path, envelope)
    }
    #[tokio::test]
    async fn offline_restart_preserves_policy_and_pending_evidence() {
        let dir = tempfile::tempdir().unwrap();
        let (path, _) = fixture(dir.path(), false);
        let manager = Management::load(&path, "fixture", vec!["aidlp.test:443".into()]).unwrap();
        let decision = crate::inspection::Decision {
            reason: "sensitive_data",
            rules: vec!["email"],
        };
        manager
            .record("aidlp.test", "POST", 19, &decision, "monitor", 7)
            .unwrap();
        assert!(manager.synchronize().await.is_err());
        assert_eq!(manager.snapshot().unwrap().revision, 7);
        assert_eq!(
            managed_policy::evaluate(
                decision,
                manager.snapshot().as_deref(),
                managed_policy::now()
            )
            .1,
            "monitor"
        );
        let original = manager.outbox.lock().unwrap().batch()[0].event_id;
        drop(manager);
        let restarted = Management::load(&path, "fixture", vec![]).unwrap();
        assert_eq!(
            restarted.outbox.lock().unwrap().batch()[0].event_id,
            original
        );
        assert_eq!(restarted.snapshot().unwrap().revision, 7);
    }
    #[test]
    fn expired_cache_blocks_and_retains_revision_floor() {
        let dir = tempfile::tempdir().unwrap();
        let (path, _) = fixture(dir.path(), true);
        let manager = Management::load(&path, "fixture", vec![]).unwrap();
        let old = manager.snapshot().unwrap();
        assert_eq!(
            managed_policy::evaluate(
                crate::inspection::Decision {
                    reason: "clean",
                    rules: vec![]
                },
                Some(&old),
                managed_policy::now()
            )
            .1,
            "block"
        );
        let mut candidate = old.as_ref().clone();
        candidate.revision = 6;
        candidate.expires_at = managed_policy::now() + 60;
        let key = SigningKey::from_bytes(&[19; 32]);
        let bytes = serde_json::to_vec(&candidate).unwrap();
        let envelope = Envelope {
            payload_base64: STANDARD.encode(&bytes),
            signature_base64: STANDARD.encode(key.sign(&bytes).to_bytes()),
            key_id: managed_policy::key_id(key.verifying_key().as_bytes()),
        };
        assert!(manager.install(envelope).is_err());
        assert_eq!(manager.snapshot().unwrap().revision, 7);
    }
    #[test]
    fn transport_requires_tls_and_authentic_cache() {
        assert!(client("http://192.0.2.1", None, true).is_err());
        assert!(client("http://localhost", None, false).is_err());
        assert!(client("https://user:password@example.test", None, false).is_err());
        let dir = tempfile::tempdir().unwrap();
        let (path, mut envelope) = fixture(dir.path(), false);
        envelope.payload_base64 = STANDARD.encode(b"{}");
        fs::write(
            dir.path().join("state/policy.json"),
            serde_json::to_vec(&envelope).unwrap(),
        )
        .unwrap();
        assert!(Management::load(&path, "fixture", vec![]).is_err());
    }
}
