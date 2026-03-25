@echo off
setlocal

rem Print RUNNING (PID=...) or STOPPED based on telegram-bot.pid + actual process.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0status-telegram-bot.ps1"

endlocal

