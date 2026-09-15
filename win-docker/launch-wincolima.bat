@echo off
setlocal EnableExtensions

rem Development source bootstrap and launcher.
rem It requests elevation once, installs build prerequisites with winget,
rem compiles WinColima, provisions WSL2/Docker on first use, then opens the app.
rem Public users should use the packaged WinColima setup EXE instead.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\one-click-launch.ps1" %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
