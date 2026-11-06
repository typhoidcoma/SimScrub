# Bring a window to the front and maximize it, by (partial) title.
#   powershell -File window.ps1 -Title "Isaac Sim" [-Action max|restore]
param([string]$Title = "", [int]$ProcessId = 0, [string]$CmdMatch = "", [string]$Action = "max")

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win {
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
}
"@

if ($CmdMatch) {
  # Processes whose command line contains a unique marker (e.g. our browser's profile folder).
  $ids = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($CmdMatch) } | ForEach-Object { $_.ProcessId }
  $p = Get-Process -Id $ids -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
} elseif ($ProcessId -gt 0) {
  # The process itself or any descendant that owns a visible window (browsers render in child processes).
  $ids = @($ProcessId)
  for ($i = 0; $i -lt 4; $i++) {
    $ids += Get-CimInstance Win32_Process | Where-Object { $ids -contains $_.ParentProcessId } | ForEach-Object { $_.ProcessId }
    $ids = $ids | Select-Object -Unique
  }
  $p = Get-Process -Id $ids -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
} else {
  $p = Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like "*$Title*" } | Select-Object -First 1
}
if (-not $p) { Write-Output "NOTFOUND"; exit 1 }
$h = $p.MainWindowHandle
# A tapped Alt key lets a background process take the foreground (Windows focus-stealing rule).
[Win]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [Win]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
[void][Win]::ShowWindow($h, $(if ($Action -eq "max") { 3 } else { 9 }))
[void][Win]::BringWindowToTop($h)
[void][Win]::SetForegroundWindow($h)
Write-Output "OK $($p.ProcessName) '$($p.MainWindowTitle)'"
