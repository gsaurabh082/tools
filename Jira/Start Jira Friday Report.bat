@echo off
setlocal
cd /d "%~dp0"
title Jira Friday Report

where pwsh.exe >nul 2>&1
if %errorlevel% equ 0 (
    pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0start_jira_ui.ps1"
) else (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0start_jira_ui.ps1"
)

echo.
echo The Jira report server has stopped.
pause
endlocal
