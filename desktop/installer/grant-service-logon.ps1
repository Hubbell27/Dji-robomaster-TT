<#
  Lets the Dental Intake service run as a specific Windows account, so nightly
  backups can be written to a shared network drive that account can access.
  Called by the installer:  grant-service-logon.ps1 -Account "OFFICE\backupuser" -DataDir "C:\ProgramData\Dental Intake"
#>
param([Parameter(Mandatory)][string]$Account, [Parameter(Mandatory)][string]$DataDir)
$ErrorActionPreference = "Stop"
$sid = (New-Object System.Security.Principal.NTAccount($Account)).Translate([System.Security.Principal.SecurityIdentifier]).Value

# 1. Grant "Log on as a service".
$cfg = Join-Path $env:TEMP "di-rights.inf"
$db = Join-Path $env:TEMP "di-rights.sdb"
secedit /export /cfg $cfg /areas USER_RIGHTS | Out-Null
$lines = Get-Content $cfg
$existing = $lines | Where-Object { $_ -like "SeServiceLogonRight*" }
if ($existing) {
  if ($existing -notmatch [regex]::Escape("*$sid")) {
    $lines = $lines | ForEach-Object { if ($_ -like "SeServiceLogonRight*") { "$_,*$sid" } else { $_ } }
  }
} else {
  $lines = $lines | ForEach-Object { if ($_ -eq "[Privilege Rights]") { $_; "SeServiceLogonRight = *$sid" } else { $_ } }
}
$lines | Set-Content $cfg -Encoding Unicode
secedit /configure /db $db /cfg $cfg /areas USER_RIGHTS | Out-Null
Remove-Item $cfg, $db -ErrorAction SilentlyContinue

# 2. Give the account access to the data folder (it is otherwise SYSTEM/Administrators only).
# Folder-level, inheritable grant: files and subfolders inherit it (no /T, which would add per-file entries).
icacls $DataDir /grant "*${sid}:(OI)(CI)M" /Q | Out-Null
Write-Host "Granted service logon and data access to $Account"
