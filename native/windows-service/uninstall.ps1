[CmdletBinding()]param([switch]$Apply)
$ErrorActionPreference='Stop'
$root=Join-Path $env:ProgramData 'Fastpace\AI DLP'
$path=Join-Path $root 'installation.json';$manifest=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
if($manifest.service -ne 'FastpaceAiDlp' -or $manifest.data_root -ine $root -or $manifest.bin_root -ine (Join-Path $env:ProgramFiles 'Fastpace\AI DLP')){throw 'Installation identity mismatch'}
if(-not $Apply){[pscustomobject]@{remove_service=$manifest.service;remove_rules=$manifest.rules;remove_certificate=$manifest.certificate_thumbprint;preserve='Binaries, encrypted credentials and evidence'}|ConvertTo-Json -Depth 5;return}
$service=Get-CimInstance Win32_Service -Filter "Name='FastpaceAiDlp'"
if($service){
 $expected='"'+(Join-Path $manifest.bin_root 'aidlp-service.exe')+'" --config "'+(Join-Path $root 'engine.json')+'"'
 if($service.PathName -ine $expected){throw 'Service executable differs from installation manifest'}
 Stop-Service 'FastpaceAiDlp'; & sc.exe delete 'FastpaceAiDlp' | Out-Null; if($LASTEXITCODE){throw 'Service removal failed'}
}
foreach($rule in $manifest.rules){if($rule -notlike "FastpaceAiDlp-$($manifest.id)-*"){throw 'Rule identity mismatch'};if(Get-NetFirewallRule -Name $rule -ErrorAction SilentlyContinue){Remove-NetFirewallRule -Name $rule}}
if($manifest.certificate_thumbprint -notmatch '^[A-Fa-f0-9]{40}$'){throw 'Invalid certificate thumbprint'}
$certificate="Cert:\LocalMachine\Root\$($manifest.certificate_thumbprint)";if(Test-Path $certificate){Remove-Item -LiteralPath $certificate}
$manifest.state='uninstalled';[IO.File]::WriteAllText($path,($manifest|ConvertTo-Json -Depth 12),(New-Object Text.UTF8Encoding($false)))
[pscustomobject]@{uninstalled=$true;retained=$root;certificate_removed=$true;firewall_rules_removed=$true}|ConvertTo-Json
