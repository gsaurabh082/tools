@echo off
setlocal EnableExtensions

rem WinDock release launcher. Runs scripts/release.sh in Git Bash.
rem Usage:  release.bat 0.2.0 [--msi] [--tag] [--skip-install]
rem Created and owned by Saurabh Gupta.

set "SH=%~dp0scripts\release.sh"

rem Prefer Git for Windows bash (has the Windows go/node/npm on PATH); fall back
rem to any bash on PATH.
set "BASH="
if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "BASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined BASH if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "BASH=%LocalAppData%\Programs\Git\bin\bash.exe"
if not defined BASH for %%B in (bash.exe) do if not defined BASH set "BASH=%%~$PATH:B"

if not defined BASH (
  echo Could not find Git Bash. Install Git for Windows from https://git-scm.com/download/win
  echo or add bash.exe to PATH, then re-run: release.bat %*
  pause
  exit /b 1
)

"%BASH%" "%SH%" %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
