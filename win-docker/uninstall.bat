@echo off
setlocal EnableExtensions

rem WinDock uninstaller launcher. Runs scripts/uninstall.sh in Git Bash, which
rem always removes everything automatically - no flag needed for that.
rem Usage:  uninstall.bat [--silent]
rem Created and owned by Saurabh Gupta.

set "SH=%~dp0scripts\uninstall.sh"

set "BASH="
if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined BASH if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "BASH=%LocalAppData%\Programs\Git\bin\bash.exe"
if not defined BASH for %%B in (bash.exe) do if not defined BASH set "BASH=%%~$PATH:B"

if not defined BASH (
  echo Could not find Git Bash. You can uninstall from Windows Settings ^> Installed apps
  echo ^(search "WinDock"^), or install Git for Windows from https://git-scm.com/download/win
  pause
  exit /b 1
)

"%BASH%" "%SH%" %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
