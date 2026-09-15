@echo off
rem Sends folders of DICOM files to the dev DICOM listener via dcm4che's
rem DcmSnd (see send-to-dicom.sh for the actual command / defaults). Loops so
rem you can send several folders in one go - just re-enter a new folder (and
rem AET, if it changes) each time, or leave the folder blank to quit.
rem
rem Usage:
rem   - Drag and drop a folder onto this .bat to pre-fill the first send, or
rem   - Double-click it and paste/type the folder path (and optionally the
rem     calling AET) when prompted.

setlocal enabledelayedexpansion

set "FOLDER=%~1"
set "AET=%~2"

set "SCRIPT_DIR=%~dp0"
set "SH_SCRIPT=%SCRIPT_DIR%send-to-dicom.sh"

rem Prefer real Git Bash - it understands Windows-style paths (C:\...)
rem directly. If WSL is also installed, "bash" on PATH often resolves to
rem WSL's bash.exe instead (a System32 shim), which needs /mnt/c/... paths
rem and will fail with "No such file or directory" on a plain C:\ path - so
rem we look for Git Bash by its real install location first rather than
rem trusting whatever "bash" happens to resolve to.
set "GITBASH="
if exist "%ProgramFiles%\Git\bin\bash.exe" set "GITBASH=%ProgramFiles%\Git\bin\bash.exe"
if not defined GITBASH if exist "%ProgramFiles(x86)%\Git\bin\bash.exe" set "GITBASH=%ProgramFiles(x86)%\Git\bin\bash.exe"
if not defined GITBASH if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "GITBASH=%LocalAppData%\Programs\Git\bin\bash.exe"

if not defined GITBASH (
    where wsl >nul 2>&1
    if errorlevel 1 (
        echo Could not find Git Bash or WSL. Install Git for Windows
        echo ^(see windows-tools\windows-dev-tools-setup.bat, or https://git-scm.com/download/win^),
        echo or install/enable WSL, then re-run this.
        pause
        exit /b 1
    )
    for /f "delims=" %%i in ('wsl wslpath -u "%SH_SCRIPT%" 2^>nul') do set "WSL_SH=%%i"
    if not defined WSL_SH (
        echo Could not translate the script path for WSL. Install Git for Windows instead.
        pause
        exit /b 1
    )
)

:loop
if "%FOLDER%"=="" (
    set /p FOLDER="Folder to send (leave blank to quit): "
)
if "%FOLDER%"=="" goto :done

if "%AET%"=="" (
    set /p AET="Calling AET [default: ct01]: "
)
if "%AET%"=="" set "AET=ct01"

if defined GITBASH (
    "%GITBASH%" "%SH_SCRIPT%" "%FOLDER%" "%AET%"
) else (
    for /f "delims=" %%i in ('wsl wslpath -u "%FOLDER%" 2^>nul') do set "WSL_FOLDER=%%i"
    wsl bash "%WSL_SH%" "%WSL_FOLDER%" "%AET%"
)

echo.
set "FOLDER="
set /p AGAIN="Send another folder? [Y/n]: "
if /i "%AGAIN%"=="n" goto :done
goto :loop

:done
echo.
echo Done.
pause
