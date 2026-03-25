@echo off
setlocal

rem Stop telegram-bot using stored PID.
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-telegram-bot.ps1"

endlocal

