[CmdletBinding()]param()
$ErrorActionPreference='Stop'
$root=Join-Path $env:ProgramData 'Fastpace\AI DLP'
$manifest=Get-Content -Raw -LiteralPath (Join-Path $root 'installation.json')|ConvertFrom-Json
$service=Get-Service $manifest.service -ErrorAction SilentlyContinue
$statusPath=Join-Path $root 'credentials\state\status.json'
$agent=if(Test-Path $statusPath){Get-Content -Raw -LiteralPath $statusPath|ConvertFrom-Json}else{$null}
$age=if($agent){[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()-[long]$agent.timestamp}else{$null}
[pscustomobject]@{service=if($service){[string]$service.Status}else{'Absent'};capture_report=$agent;report_age_seconds=$age;fresh=($null -ne $age -and $age -lt 30);certificate_present=(Test-Path "Cert:\LocalMachine\Root\$($manifest.certificate_thumbprint)");firewall_rule_count=@($manifest.rules|ForEach-Object{Get-NetFirewallRule -Name $_ -PolicyStore ActiveStore -ErrorAction SilentlyContinue}).Count;applications=$manifest.applications;targets=$manifest.targets;limitation='Service failure may release TCP capture during recovery; status is not tamper attestation.'}|ConvertTo-Json -Depth 8
