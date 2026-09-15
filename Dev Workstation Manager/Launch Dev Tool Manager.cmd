@echo off
setlocal
start "Dev Tool Manager" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0BrowserServer.ps1"
