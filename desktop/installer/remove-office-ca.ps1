# Removes the Dental Intake office certificate(s) from the Windows trust store (run by the uninstaller).
# Uses certutil (the same tool the installer used to add them) and writes a small log.
$ErrorActionPreference = "Continue"
$logDir = Join-Path $env:ProgramData "Dental Intake\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "uninstall-ca.log"
"$(Get-Date -Format s) removing Dental Intake office certificates" | Out-File -FilePath $log -Append -Encoding utf8
$certutil = Join-Path $env:SystemRoot "System32\certutil.exe"
if (-not (Test-Path $certutil)) { $certutil = Join-Path $env:SystemRoot "Sysnative\certutil.exe" }
$certs = @(Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -like "*Dental Intake CA*" })
foreach ($c in $certs) {
  $out = & $certutil -delstore Root $c.Thumbprint 2>&1
  "  $($c.Thumbprint) certutil exit $LASTEXITCODE" | Out-File -FilePath $log -Append -Encoding utf8
  if ($LASTEXITCODE -ne 0) {
    $out | Out-File -FilePath $log -Append -Encoding utf8
    Remove-Item -LiteralPath $c.PSPath -Force -ErrorAction Continue
  }
}
$left = @(Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -like "*Dental Intake CA*" }).Count
"  found $($certs.Count), remaining $left" | Out-File -FilePath $log -Append -Encoding utf8
exit 0
