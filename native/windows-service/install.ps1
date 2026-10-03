[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$SourceRoot,
  [Parameter(Mandatory=$true)][string]$EnrollmentPath,
  [string[]]$Applications = @(),
  [string[]]$Targets = @('chatgpt.com:443'),
  [string[]]$ProtectedEndpoints = @(),
  [string]$OriginCa = '',
  [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$ServiceName = 'FastpaceAiDlp'
$BinRoot = Join-Path $env:ProgramFiles 'Fastpace\AI DLP'
$DataRoot = Join-Path $env:ProgramData 'Fastpace\AI DLP'
$ManifestPath = Join-Path $DataRoot 'installation.json'
function Run-Native([string]$File,[string[]]$Arguments) { & $File @Arguments; if ($LASTEXITCODE -ne 0) { throw "Native operation failed: $File ($LASTEXITCODE)" } }
function Protect-Directory([string]$Path) {
  Run-Native 'icacls.exe' @($Path,'/inheritance:r','/grant:r','*S-1-5-18:(OI)(CI)F','*S-1-5-32-544:(OI)(CI)F')
}
function Write-Json([string]$Path,$Value) { [IO.File]::WriteAllText($Path,($Value|ConvertTo-Json -Depth 12),(New-Object Text.UTF8Encoding($false))) }
if (-not $Applications.Count) {
  foreach ($exe in @('chrome.exe','msedge.exe','firefox.exe','brave.exe','whale.exe')) {
    foreach ($hive in @('HKLM:','HKCU:')) {
      $registry = "$hive\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$exe"
      if (Test-Path $registry) { $value = (Get-Item $registry).GetValue(''); if ($value -and (Test-Path -LiteralPath $value)) { $Applications += $value } }
    }
  }
}
$Applications = @($Applications | ForEach-Object {
  $item = Get-Item -LiteralPath $_
  if ($item.PSIsContainer -or $item.Extension -ine '.exe' -or $item.FullName -match '[,\r\n*=?!]' -or $item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Invalid application executable' }
  $item.FullName
} | Sort-Object -Unique)
if ($Applications.Count -lt 1 -or $Applications.Count -gt 16) { throw 'Provide 1..16 installed application executable paths' }
$Ports = @($Targets | ForEach-Object { if ($_ -notmatch '^([a-zA-Z0-9.-]+):([0-9]{1,5})$') { throw 'Targets require DNS name and port' }; $p=[int]$Matches[2]; if ($p -lt 1 -or $p -gt 65535) { throw 'Invalid target port' }; $p } | Sort-Object -Unique)
$plan = [ordered]@{service=$ServiceName; binaries=$BinRoot; data=$DataRoot; applications=$Applications; targets=$Targets; change='Add a device inspection root CA, per-application QUIC firewall rules, and an automatic Windows service. No proxy/profile changes.'}
if (-not $Apply) { $plan|ConvertTo-Json -Depth 6; return }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run installation in an elevated PowerShell session' }
if ((Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) -or (Test-Path -LiteralPath $BinRoot) -or (Test-Path -LiteralPath $DataRoot)) { throw 'Existing installation or conflicting directory; inspect it instead of overwriting' }
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$EnrollmentPath = (Resolve-Path -LiteralPath $EnrollmentPath).Path
$engine = Join-Path $SourceRoot 'native\target\x86_64-pc-windows-gnullvm\debug'
$redirector = Join-Path $SourceRoot 'native\third_party\mitmproxy_rs\target\x86_64-pc-windows-gnullvm\debug'
$files = @(
  @{src=(Join-Path $engine 'aidlp-native.exe');dst='aidlp-native.exe'},
  @{src=(Join-Path $engine 'aidlp-service.exe');dst='aidlp-service.exe'},
  @{src=(Join-Path $engine 'libunwind.dll');dst='libunwind.dll'},
  @{src=(Join-Path $redirector 'windows-redirector.exe');dst='windows-redirector.exe'},
  @{src=(Join-Path $redirector 'WinDivert.dll');dst='WinDivert.dll'},
  @{src=(Join-Path $redirector 'WinDivert64.sys');dst='WinDivert64.sys'}
)
foreach ($file in $files) { if (-not (Test-Path -LiteralPath $file.src -PathType Leaf)) { throw "Missing build output: $($file.dst)" } }
$certificateAdded=$false
$record = [ordered]@{version=1;id=[guid]::NewGuid().ToString();created_at=[DateTime]::UtcNow.ToString('o');service=$ServiceName;bin_root=$BinRoot;data_root=$DataRoot;applications=$Applications;targets=$Targets;rules=@();certificate_thumbprint=$null;service_created=$false;state='installing';binaries=@()}
try {
  New-Item -ItemType Directory -Path $BinRoot -Force | Out-Null
  New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
  Protect-Directory $BinRoot; Protect-Directory $DataRoot
  foreach ($file in $files) { $destination=Join-Path $BinRoot $file.dst; Copy-Item -LiteralPath $file.src -Destination $destination; $record.binaries += @{name=$file.dst;sha256=(Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash} }
  foreach ($name in @('status.ps1','uninstall.ps1')) { Copy-Item -LiteralPath (Join-Path $SourceRoot "native\windows-service\$name") -Destination $BinRoot }
  Write-Json $ManifestPath $record
  Run-Native (Join-Path $BinRoot 'aidlp-native.exe') @('init-ca','--directory',(Join-Path $DataRoot 'ca'))
  $caFile=Join-Path $DataRoot 'ca\ca.pem'
  $pem=[IO.File]::ReadAllText($caFile); $der=[Convert]::FromBase64String(($pem -replace '-----BEGIN CERTIFICATE-----|-----END CERTIFICATE-----|\s',''))
  $cert=New-Object Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList @(,$der)
  $record.certificate_thumbprint=$cert.Thumbprint
  if (Test-Path "Cert:\LocalMachine\Root\$($cert.Thumbprint)") { throw 'Inspection certificate already trusted unexpectedly' }
  Write-Json $ManifestPath $record
  $store=New-Object Security.Cryptography.X509Certificates.X509Store('Root','LocalMachine'); $store.Open('ReadWrite'); try { $store.Add($cert); $certificateAdded=$true } finally { $store.Close() }
  $credentials=Join-Path $DataRoot 'credentials\management.json'
  Run-Native (Join-Path $BinRoot 'aidlp-native.exe') @('enroll','--config',$EnrollmentPath,'--output',$credentials)
  # Enrollment initially limits to the installer identity. Replace it with inherited service ACLs.
  Run-Native 'icacls.exe' @((Join-Path $DataRoot 'credentials'),'/reset','/T')
  Run-Native 'icacls.exe' @($DataRoot,'/setowner','*S-1-5-18','/T')
  foreach ($program in $Applications) {
    $rule="FastpaceAiDlp-$($record.id)-$($record.rules.Count)"
    $record.rules += $rule; Write-Json $ManifestPath $record
    New-NetFirewallRule -Name $rule -DisplayName 'Fastpace AI DLP QUIC guard' -Group 'Fastpace AI DLP QUIC' -Direction Outbound -Program $program -Protocol UDP -RemotePort $Ports -Action Block -Profile Any -Enabled True | Out-Null
  }
  $config=[ordered]@{management=$credentials;state_dir=(Join-Path $DataRoot 'runtime');ca_dir=(Join-Path $DataRoot 'ca');targets=$Targets;mode=@{kind='applications';redirector=(Join-Path $BinRoot 'windows-redirector.exe');executables=$Applications}}
  if ($ProtectedEndpoints.Count) { $config.protected_endpoints=$ProtectedEndpoints }
  if ($OriginCa) { Copy-Item -LiteralPath $OriginCa -Destination (Join-Path $DataRoot 'upstream-ca.pem'); $config.origin_ca=Join-Path $DataRoot 'upstream-ca.pem' }
  $configPath=Join-Path $DataRoot 'engine.json'; Write-Json $configPath $config
  $binary='"'+(Join-Path $BinRoot 'aidlp-service.exe')+'" --config "'+$configPath+'"'
  New-Service -Name $ServiceName -DisplayName 'Fastpace AI DLP' -BinaryPathName $binary -StartupType Automatic -Description 'Transparent inspection for configured applications; local policy enforcement and metadata reporting.' | Out-Null
  $record.service_created=$true; Write-Json $ManifestPath $record
  Run-Native 'sc.exe' @('failure',$ServiceName,'reset=','86400','actions=','restart/5000/restart/15000/restart/60000')
  Run-Native 'sc.exe' @('failureflag',$ServiceName,'1')
  Start-Service $ServiceName
  $deadline=[DateTime]::UtcNow.AddSeconds(90); $ready=$false
  do {
    $statusPath=Join-Path $DataRoot 'credentials\state\status.json'
    if (Test-Path $statusPath) { $status=Get-Content -Raw -LiteralPath $statusPath|ConvertFrom-Json; if ($status.engine_state -eq 'running' -and $status.policy_valid -and $status.last_sync_ok -and $status.scope -eq 'configured_applications') { $ready=$true;break } }
    Start-Sleep -Milliseconds 500
  } while ([DateTime]::UtcNow -lt $deadline)
  if (-not $ready) { throw 'Service did not report capture and valid policy ready within 90 seconds' }
  $record.state='ready';Write-Json $ManifestPath $record
  [pscustomobject]@{installed=$true;service=$ServiceName;capture_ready=$true;policy_revision=$status.applied_revision;certificate_thumbprint=$record.certificate_thumbprint;applications=$Applications}|ConvertTo-Json -Depth 6
} catch {
  $failure=$_
  if ($record.service_created) { Stop-Service $ServiceName -ErrorAction SilentlyContinue; & sc.exe delete $ServiceName | Out-Null }
  foreach ($rule in $record.rules) { Remove-NetFirewallRule -Name $rule -ErrorAction SilentlyContinue }
  if ($certificateAdded) { Remove-Item -LiteralPath "Cert:\LocalMachine\Root\$($record.certificate_thumbprint)" -ErrorAction SilentlyContinue }
  $record.state='failed_rolled_back'; if (Test-Path $DataRoot) { Write-Json $ManifestPath $record }
  throw $failure
}
