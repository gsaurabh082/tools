@echo off
title Uninstall DevHub
echo.
echo  Removing DevHub startup task...
echo.

set FOUND=0
schtasks /Delete /TN "DevHub" /F >nul 2>&1 && set FOUND=1
schtasks /Delete /TN "MyDevHub" /F >nul 2>&1 && set FOUND=1
schtasks /Delete /TN "LocalHub" /F >nul 2>&1 && set FOUND=1

if %FOUND% equ 1 (
    echo  [OK] Startup task removed. DevHub will no longer start at login.
) else (
    echo  [WARN] No startup task found - already removed.
)

echo.
echo  Stopping any running hub...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":7272 " ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

echo  [OK] Done.
echo.
echo  Your bookmarks and profile were not touched.
echo  Profile: %USERPROFILE%\.devhub\profile.json
echo.
pause
