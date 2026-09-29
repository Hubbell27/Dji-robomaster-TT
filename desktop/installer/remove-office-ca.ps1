# Removes the Dental Intake office certificate(s) from the Windows trust store (run by the uninstaller).
# Finds them two ways (PowerShell's certificate drive and certutil's own listing, the same view the
# installer used to add them), deletes by thumbprint with certutil, and writes a small log.
$ErrorActionPreference = "Continue"
$logDir = Join-Path $env:ProgramData "Dental Intake\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "uninstall-ca.log"
function Log($m) { "$(Get-Date -Format s) $m" | Out-File -FilePath $log -Append -Encoding utf8 }
$certutil = Join-Path $env:SystemRoot "System32\certutil.exe"
if (-not [Environment]::Is64BitProcess -and (Test-Path (Join-Path $env:SystemRoot "Sysnative\certutil.exe"))) {
  $certutil = Join-Path $env:SystemRoot "Sysnative\certutil.exe"
}
Log "removing Dental Intake office certificates (64-bit process: $([Environment]::Is64BitProcess))"

function Find-OfficeCerts {
  $found = @{}
  Get-ChildItem Cert:\LocalMachine\Root -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -like "*Dental Intake CA*" } | ForEach-Object { $found[$_.Thumbprint.ToUpper()] = $true }
  $subject = ""
  foreach ($line in (& $certutil -store Root 2>$null)) {
    if ($line -match '^\s*Subject:\s*(.*)$') { $subject = $Matches[1] }
    elseif ($line -match '^\s*Cert Hash\(sha1\):\s*(.*)$' -and $subject -like "*Dental Intake CA*") {
      $found[($Matches[1] -replace '\s', '').ToUpper()] = $true
    }
  }
  return @($found.Keys)
}

$thumbs = Find-OfficeCerts
Log "found $($thumbs.Count)"
foreach ($t in $thumbs) {
  $out = & $certutil -delstore Root $t 2>&1
  Log "  $t certutil exit $LASTEXITCODE"
  if ($LASTEXITCODE -ne 0) { $out | Out-File -FilePath $log -Append -Encoding utf8 }
}
Log "remaining $((Find-OfficeCerts).Count)"
exit 0
