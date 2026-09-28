# Removes the Dental Intake office certificate(s) from the Windows trust store (run by the uninstaller).
$ErrorActionPreference = "Continue"
$removed = 0
foreach ($store in "Cert:\LocalMachine\Root") {
  Get-ChildItem $store | Where-Object { $_.Subject -like "*Dental Intake CA*" } | ForEach-Object {
    Remove-Item -LiteralPath $_.PSPath -Force
    $removed++
  }
}
Write-Host "Removed $removed Dental Intake certificate(s)"
exit 0
