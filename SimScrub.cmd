@echo off
rem Double-click to start SimScrub and open it. Run "SimScrub.cmd stop" to shut it down.
if /i "%~1"=="stop" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1" -Stop
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1"
)
