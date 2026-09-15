@echo off
setlocal
cd /d "%~dp0"
title RDP Host Manager

where pwsh.exe >nul 2>&1
if %errorlevel% equ 0 (
    pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0start_rdp_ui.ps1"
) else (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0start_rdp_ui.ps1"
)

echo.
echo The RDP Host Manager server has stopped. Press any key to close this window.
pause >nul
endlocal
