@echo off
rem Double-click to stop the SimScrub backend and UI.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1" -Stop
