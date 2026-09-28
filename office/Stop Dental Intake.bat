@echo off
REM Double-click to stop Dental Intake.
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "office\office.ps1" stop
pause
