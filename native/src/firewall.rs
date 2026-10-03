//! Effective Windows Firewall verification; the installer owns rule creation/removal.
use anyhow::Result;

#[cfg(not(windows))]
pub async fn verify(_executables: Vec<String>, _ports: Vec<u16>) -> Result<()> { anyhow::bail!("application firewall verification requires Windows"); }

#[cfg(windows)]
pub async fn verify(executables: Vec<String>, ports: Vec<u16>) -> Result<()> {
    let request = serde_json::to_string(&serde_json::json!({"executables":executables,"ports":ports}))?;
    let status = tokio::time::timeout(std::time::Duration::from_secs(20), async {
        let mut command = tokio::process::Command::new("powershell.exe");
        command.args(["-NoProfile", "-NonInteractive", "-Command", VERIFY_SCRIPT])
            .env("AIDLP_FIREWALL_EXPECTED", request).kill_on_drop(true)
            .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
        command.status().await
    }).await??;
    anyhow::ensure!(status.success(), "effective application QUIC firewall guard is unavailable");
    Ok(())
}

#[cfg(windows)]
const VERIFY_SCRIPT: &str = r#"
$ErrorActionPreference = 'Stop'
try {
  $expected = $env:AIDLP_FIREWALL_EXPECTED | ConvertFrom-Json
  $profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
  if ($profiles.Count -ne 3 -or @($profiles | Where-Object { -not $_.Enabled -or $_.AllowLocalFirewallRules -eq 'False' }).Count) { exit 2 }
  $rules = @(Get-NetFirewallRule -PolicyStore ActiveStore -Group 'Fastpace AI DLP QUIC' -ErrorAction SilentlyContinue)
  foreach ($program in $expected.executables) {
    $matched = $false
    foreach ($rule in $rules) {
      if ($rule.Enabled -ne 'True' -or $rule.Direction -ne 'Outbound' -or $rule.Action -ne 'Block' -or $rule.Profile -ne 'Any') { continue }
      $app = $rule | Get-NetFirewallApplicationFilter
      $port = $rule | Get-NetFirewallPortFilter
      if ($app.Program -ine $program -or $port.Protocol -notin @('UDP','17')) { continue }
      $actual = @($port.RemotePort | ForEach-Object { [string]$_ })
      if (@($expected.ports | Where-Object { [string]$_ -notin $actual }).Count -eq 0) { $matched = $true; break }
    }
    if (-not $matched) { exit 3 }
  }
  exit 0
} catch { exit 4 }
"#;
