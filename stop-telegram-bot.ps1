# Stops telegram-bot using telegram-bot.pid.
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $scriptRoot 'telegram-bot.pid'

if (-not (Test-Path -LiteralPath $pidFile)) {
  Write-Output "Telegram bot is not running (no pid file)."
  exit 0
}

$pidText = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
if ($pidText -match '^\d+$') {
  $procId = [int]$pidText
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue | Out-Null
}

Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue | Out-Null
Write-Output "Telegram bot stopped."

