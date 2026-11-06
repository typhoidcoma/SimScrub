# SimScrub launcher: start the backend and UI (if not already running) and open the app.
#   launch.ps1          start + open
#   launch.ps1 -Stop    stop both servers
param([switch]$Stop)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$url = "http://localhost:5173/"

function Listening([int]$port) {
  # Vite may listen on IPv6 (::1) only, uvicorn on IPv4: try both.
  foreach ($addr in "127.0.0.1", "::1") {
    $ip = [Net.IPAddress]::Parse($addr)
    $c = New-Object Net.Sockets.TcpClient($ip.AddressFamily)
    try { if ($c.ConnectAsync($ip, $port).Wait(300) -and $c.Connected) { return $true } } catch {} finally { $c.Dispose() }
  }
  $false
}

function StopMatching([string[]]$needles, [string]$name) {
  # Match by command line: works whether the server came from this launcher or a terminal.
  $procs = Get-CimInstance Win32_Process | Where-Object {
    $cl = $_.CommandLine; $cl -and -not ($needles | Where-Object { $cl -notlike "*$_*" })
  }
  if (-not $procs) { Write-Host "  $name not running"; return }
  # /T kills children too, so later entries may already be gone: ignore those errors.
  foreach ($p in $procs) { cmd /c "taskkill /T /F /PID $($p.ProcessId) >nul 2>&1" }
  Write-Host "  $name stopped"
}

if ($Stop) {
  Write-Host "Stopping SimScrub..."
  StopMatching @("title SimScrub") "launcher windows"
  StopMatching @("uvicorn", "server.app:app") "backend"
  StopMatching @("vite", "--port 5173") "UI"
  Start-Sleep 2
  exit 0
}

Write-Host "SimScrub" -ForegroundColor DarkYellow
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Missing .venv - see README 'Run it'." -ForegroundColor Red; Read-Host "Enter to close"; exit 1 }
if (-not (Test-Path (Join-Path $root "ui\node_modules"))) {
  Write-Host "  installing UI packages (first run)..."
  Push-Location (Join-Path $root "ui"); npm install --no-fund --no-audit | Out-Null; Pop-Location
}

if (Listening 8000) { Write-Host "  backend already running" }
else {
  Start-Process cmd -WindowStyle Minimized -WorkingDirectory $root `
    -ArgumentList "/k title SimScrub backend && `"$py`" -m uvicorn server.app:app --host 127.0.0.1 --port 8000"
  Write-Host "  backend starting"
}
if (Listening 5173) { Write-Host "  UI already running" }
else {
  Start-Process cmd -WindowStyle Minimized -WorkingDirectory (Join-Path $root "ui") `
    -ArgumentList "/k title SimScrub UI && npx vite --port 5173 --strictPort"
  Write-Host "  UI starting"
}

Write-Host -NoNewline "  waiting (a cold start loads PyTorch, ~1-2 min) "
$t0 = Get-Date
while (-not ((Listening 8000) -and (Listening 5173))) {
  if (((Get-Date) - $t0).TotalSeconds -gt 240) {
    Write-Host "`nServers didn't come up in 4 min - check the minimized 'SimScrub' windows." -ForegroundColor Red
    Read-Host "Enter to close"; exit 1
  }
  Write-Host -NoNewline "."
  Start-Sleep 2
}
Write-Host "`n  ready in $([int]((Get-Date) - $t0).TotalSeconds) s: $url" -ForegroundColor Green

# Chrome app window: no tabs, no address bar, no bookmarks. Falls back to the default browser.
$chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
            "$env:LocalAppData\Google\Chrome\Application\chrome.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chrome) { Start-Process $chrome -ArgumentList "--app=$url", "--start-maximized" }
else { Start-Process $url }
