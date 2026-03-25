# Starts telegram-bot in background (no console window) and writes PID to telegram-bot.pid.
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $scriptRoot 'telegram-bot.pid'
$exe = Join-Path $scriptRoot '.venv\Scripts\pythonw.exe'
$workDir = $scriptRoot

if (-not (Test-Path -LiteralPath $exe)) {
  throw "pythonw.exe not found: $exe"
}

if (Test-Path -LiteralPath $pidFile) {
  $pidText = (Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
  if ($pidText -match '^\d+$') {
    $procId = [int]$pidText
    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
      Write-Output "Telegram bot is already running (PID=$procId)."
      exit 0
    }
  }
  # Stale PID file
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue | Out-Null
}

$args = @(
  '-m', 'bikefinder.cli', 'telegram-bot',
  '--config', 'config/config.yaml',
  '--stats-hours', '1'
)

$p = Start-Process -FilePath $exe `
  -WorkingDirectory $workDir `
  -ArgumentList $args `
  -WindowStyle Hidden `
  -PassThru

$p.Id | Out-File -FilePath $pidFile -Encoding ascii -Force
Write-Output "Telegram bot started (PID=$($p.Id))."

