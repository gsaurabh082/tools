@echo off
title Install DevHub Startup Service
echo.
echo  DevHub - Clean Install ^& Startup Service
echo  ========================================
echo  Stops any running hub, clears generated files, reinstalls
echo  dependencies, and registers DevHub to start at Windows login.
echo.
echo  Your bookmarks and profile are preserved.
echo  URL: http://127.0.0.1:7272
echo.

where pwsh.exe >nul 2>&1
if %errorlevel% equ 0 (
    pwsh.exe -NoLogo -ExecutionPolicy Bypass -File "%~dp0install-service.ps1"
) else (
    powershell.exe -NoLogo -ExecutionPolicy Bypass -File "%~dp0install-service.ps1"
)

pause
