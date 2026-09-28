<#
  Builds DentalIntakeSetup.exe on Windows (GitHub Actions runs this; you can too).
  Needs: Python 3.11, Node 22, Inno Setup 6 (iscc on PATH or default install folder).
    powershell -ExecutionPolicy Bypass -File desktop\build_windows.ps1
#>
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Check { if ($LASTEXITCODE -ne 0) { throw "step failed ($LASTEXITCODE)" } }

Step "frontend"
Push-Location frontend; npm ci; Check; npm run build; Check; Pop-Location

Step "python deps"
python -m pip install --upgrade pip; Check
python -m pip install -r desktop\requirements.txt; Check

Step "assets"
python desktop\make_assets.py; Check

$dist = "desktop\dist\DentalIntake"
Remove-Item -Recurse -Force desktop\dist, desktop\build -ErrorAction SilentlyContinue

Step "DentalIntakeServer.exe"
$common = @("--noconfirm", "--clean", "--onedir", "--distpath", "desktop\dist\DentalIntake", "--workpath", "desktop\build",
  "--specpath", "desktop\build", "--paths", "$root\backend", "--paths", "$root\desktop", "--icon", "$root\desktop\build-assets\icon.ico")
python -m PyInstaller @common --console --name DentalIntakeServer --contents-directory server-lib `
  --add-data "$root\frontend\dist;static" --add-data "$root\backend\alembic;alembic" `
  --add-data "$root\backend\app\content;app\content" `
  --collect-submodules app --collect-submodules dentalintake --collect-submodules uvicorn `
  --collect-submodules alembic --collect-data tzdata --collect-data reportlab --collect-data segno `
  --hidden-import sqlalchemy.dialects.sqlite --hidden-import tzlocal `
  --exclude-module boto3 --exclude-module botocore --exclude-module pystray `
  desktop\server_main.py; Check
# PyInstaller nests each app in its own folder; flatten both into one program folder.
Move-Item "$dist\DentalIntakeServer\*" $dist; Remove-Item "$dist\DentalIntakeServer"

Step "Dental Intake.exe"
python -m PyInstaller @common --windowed --name "Dental Intake" --contents-directory app-lib `
  --collect-submodules pystray --hidden-import PIL.ImageDraw `
  desktop\app_main.py; Check
Move-Item "$dist\Dental Intake\*" $dist; Remove-Item "$dist\Dental Intake"

Step "service wrapper (WinSW)"
$winsw = "desktop\build\WinSW-x64.exe"
if (-not (Test-Path $winsw)) {
  Invoke-WebRequest "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe" -OutFile $winsw
}
Copy-Item $winsw "$dist\DentalIntakeService.exe"
Copy-Item desktop\installer\DentalIntakeService.xml $dist
Copy-Item desktop\installer\grant-service-logon.ps1 $dist
Copy-Item desktop\installer\remove-office-ca.ps1 $dist
Copy-Item desktop\build-assets\OFFICE_GUIDE.html $dist
Copy-Item desktop\build-assets\icon.ico $dist

Step "smoke test the built server"
& "$dist\DentalIntakeServer.exe" --help | Out-Null; Check

Step "installer (Inno Setup)"
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
$version = (Select-String -Path desktop\dentalintake\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
& $iscc "/DAppVersion=$version" "/DSourceDir=$root\$dist" "/O$root\desktop\dist" desktop\installer\DentalIntake.iss; Check
Write-Host "Built desktop\dist\DentalIntakeSetup.exe" -ForegroundColor Green
