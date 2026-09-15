@echo off
REM =====================================================================
REM  Double-click launcher for Create-DoseWatchDevVM.ps1
REM  - Elevates to Administrator automatically (UAC prompt will appear)
REM  - Bypasses PowerShell execution policy for this run only
REM  - Must sit in the SAME FOLDER as Create-DoseWatchDevVM.ps1
REM =====================================================================

net session >nul 2>&1
if %errorLevel% == 0 goto :run

echo Requesting administrator privileges - accept the UAC prompt...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:run
set "SCRIPT_DIR=%~dp0"
set "PS1=%SCRIPT_DIR%Create-DoseWatchDevVM.ps1"

if not exist "%PS1%" (
    echo Could not find Create-DoseWatchDevVM.ps1 next to this .bat file.
    echo Expected at: %PS1%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"

echo.
echo Script finished. Press any key to close this window.
pause >nul
