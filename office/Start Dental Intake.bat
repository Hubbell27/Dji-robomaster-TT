@echo off
REM Double-click to start Dental Intake (requires Docker Desktop).
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "office\office.ps1" start
pause
