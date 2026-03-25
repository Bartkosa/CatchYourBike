# Prints RUNNING (PID=...) or STOPPED.
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $scriptRoot 'telegram-bot.pid'

if (-not (Test-Path -LiteralPath $pidFile)) {
  Write-Output 'STOPPED (no pid file)'
  exit 0
}

$pidText = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
if (-not $pidText -or -not ($pidText -match '^\d+$')) {
  Write-Output 'STOPPED (empty/invalid pid file)'
  exit 0
}

$procId = [int]$pidText
if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
  Write-Output "RUNNING (PID=$procId)"
} else {
  Write-Output 'STOPPED (pid not running)'
}

