use anyhow::{Result, ensure};
use hyper::http::uri::Authority;
use serde::Deserialize;
use std::{net::SocketAddr, path::PathBuf};
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    #[serde(default)]
    pub management: Option<PathBuf>,
    pub state_dir: PathBuf,
    #[serde(default)]
    pub origin_ca: Option<PathBuf>,
    #[serde(default)]
    pub ca_dir: Option<PathBuf>,
    pub targets: Vec<String>,
    #[serde(default)]
    pub protected_endpoints: Vec<SocketAddr>,
    #[serde(default = "default_audit_max_bytes")]
    pub audit_max_bytes: u64,
    pub mode: Mode,
}
fn default_audit_max_bytes() -> u64 {
    64 * 1024 * 1024
}
#[derive(Debug, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Mode {
    Fixture {
        listen: SocketAddr,
        destination: SocketAddr,
    },
    Applications {
        redirector: PathBuf,
        executables: Vec<String>,
    },
    Capture {
        redirector: PathBuf,
        pid: u32,
    },
}
impl Config {
    pub fn application_spec(&self) -> Result<Option<mitmproxy::intercept_conf::InterceptConf>> {
        let Mode::Applications { redirector, executables } = &self.mode else { return Ok(None); };
        ensure!((1..=16).contains(&executables.len()), "application count must be 1..16");
        ensure!(self.ca_dir.is_some(), "applications require a persistent inspection CA");
        let specs = executables.iter().map(|p| format!("={p}")).collect::<Vec<_>>();
        let spec = mitmproxy::intercept_conf::InterceptConf::try_from(specs)?;
        ensure!(spec.is_application_scope(), "exact application paths required");
        let names = executables.iter().map(|p| mitmproxy::intercept_conf::normalize_windows_path(p)).collect::<std::collections::HashSet<_>>();
        ensure!(names.len() == executables.len(), "duplicate application paths");
        for path in [std::env::current_exe()?, redirector.clone()] {
            ensure!(!names.contains(&mitmproxy::intercept_conf::normalize_windows_path(&path.to_string_lossy())), "cannot capture agent or redirector");
        }
        Ok(Some(spec))
    }

    pub fn targets(&self) -> Result<Vec<Authority>> {
        ensure!(
            !self.targets.is_empty() && self.targets.len() <= 32,
            "target count must be 1..32"
        );
        self.targets
            .iter()
            .map(|s| {
                let authority: Authority = s.to_ascii_lowercase().parse()?;
                ensure!(
                    authority.port_u16().is_some(),
                    "targets require explicit port"
                );
                ensure!(
                    !s.contains('@') && authority.host().is_ascii(),
                    "invalid target"
                );
                Ok(authority)
            })
            .collect()
    }
    pub async fn endpoints(&self, targets: &[Authority]) -> Result<Vec<SocketAddr>> {
        let mut endpoints = self.protected_endpoints.clone();
        // Explicit endpoints support captured destinations whose DNS differs from
        // this host (and controlled test certificates without editing hosts files).
        if endpoints.is_empty() {
            for authority in targets {
                let resolved = tokio::time::timeout(
                    std::time::Duration::from_secs(10),
                    tokio::net::lookup_host((authority.host(), authority.port_u16().unwrap())),
                )
                .await??;
                endpoints.extend(resolved);
            }
        }
        endpoints.sort();
        endpoints.dedup();
        ensure!(!endpoints.is_empty(), "no protected endpoints resolved");
        if matches!(self.mode, Mode::Capture { .. } | Mode::Applications { .. }) {
            ensure!(
                endpoints
                    .iter()
                    .all(|e| !e.ip().is_loopback() && !e.ip().is_unspecified()),
                "capture does not support loopback or unspecified endpoints"
            );
            ensure!(
                targets.iter().all(|a| {
                    let host = a.host().trim_matches(['[', ']']);
                    host != "localhost"
                        && !host.ends_with(".localhost")
                        && host.parse::<std::net::IpAddr>().is_err()
                }),
                "capture targets must be DNS names other than localhost"
            );
        }
        Ok(endpoints)
    }
}
