@echo off
setlocal EnableDelayedExpansion

REM ============================================================
REM wsl-setup-universal.bat
REM
REM A parameterized version of wsl-setup.bat - no hardcoded
REM username, OneDrive tenant, or project path anywhere in this
REM file. Everything machine/project-specific is passed in as a
REM command-line argument, or - if you just double-click it - it
REM asks for it interactively.
REM
REM Usage (all optional, unset ones are asked for interactively):
REM   wsl-setup-universal.bat -Folder "C:\path\to\local-setup" ^
REM       -Distro Ubuntu -Port 2375 -TestUtils "C:\path\to\TestUtils"
REM
REM Run "wsl-setup-universal.bat -?" to see all options.
REM
REM What it does:
REM   1. Check WSL is installed and enabled (install/enable if not)
REM   2. Check the target distro is installed (install if not)
REM   3. Fix WSL network config (mirrored networking, no idle shutdown)
REM   4. Run wsl-setup.sh (from -Folder) inside the distro
REM   5. Verify Windows can reach the docker daemon (auto-fix if not)
REM   6. Sync every Windows terminal/tool to that daemon
REM   7. Optionally run a Testcontainers/Ryuk check against -TestUtils
REM ============================================================

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "IN_FOLDER="
set "DISTRO="
set "DOCKER_TCP_PORT="
set "DOCKER_CONTEXT_NAME=wsl"
set "SH_RELATIVE_PATH=wsl-setup.sh"
set "TESTUTILS_DIR="
set "TESTUTILS_GIVEN=0"

REM ------------------------------------------------------------
REM Parse command-line arguments
REM ------------------------------------------------------------
:ParseArgs
if "%~1"=="" goto :ArgsDone
if /I "%~1"=="-Folder"      (set "IN_FOLDER=%~2"        & shift & shift & goto :ParseArgs)
if /I "%~1"=="-Distro"      (set "DISTRO=%~2"           & shift & shift & goto :ParseArgs)
if /I "%~1"=="-Port"        (set "DOCKER_TCP_PORT=%~2"  & shift & shift & goto :ParseArgs)
if /I "%~1"=="-ContextName" (set "DOCKER_CONTEXT_NAME=%~2" & shift & shift & goto :ParseArgs)
if /I "%~1"=="-Sh"          (set "SH_RELATIVE_PATH=%~2" & shift & shift & goto :ParseArgs)
if /I "%~1"=="-TestUtils"   (set "TESTUTILS_DIR=%~2" & set "TESTUTILS_GIVEN=1" & shift & shift & goto :ParseArgs)
if /I "%~1"=="-?"           (call :ShowUsage & exit /b 0)
if /I "%~1"=="/?"           (call :ShowUsage & exit /b 0)
if /I "%~1"=="-Help"        (call :ShowUsage & exit /b 0)
echo Unknown argument: %~1
call :ShowUsage
exit /b 1
:ArgsDone

REM ------------------------------------------------------------
REM Anything not given on the command line is asked for here.
REM ------------------------------------------------------------
if not defined IN_FOLDER (
  set /p "IN_FOLDER=Folder containing wsl-setup.sh / wsl-network-fix.ps1 [%SCRIPT_DIR%]: "
  if not defined IN_FOLDER set "IN_FOLDER=%SCRIPT_DIR%"
)
if "%IN_FOLDER:~-1%"=="\" set "IN_FOLDER=%IN_FOLDER:~0,-1%"

if not defined DISTRO (
  set /p "DISTRO=WSL distro to use [Ubuntu]: "
  if not defined DISTRO set "DISTRO=Ubuntu"
)

if not defined DOCKER_TCP_PORT (
  set /p "DOCKER_TCP_PORT=Docker TCP port [2375]: "
  if not defined DOCKER_TCP_PORT set "DOCKER_TCP_PORT=2375"
)

if "%TESTUTILS_GIVEN%"=="0" (
  set /p "TESTUTILS_DIR=Optional Testcontainers/Ryuk check folder ^(Enter to skip^): "
)

set "NETFIX_PATH=%IN_FOLDER%\wsl-network-fix.ps1"

echo ============================================================
echo  Folder:      %IN_FOLDER%
echo  Distro:      %DISTRO%
echo  Docker port: %DOCKER_TCP_PORT%
if defined TESTUTILS_DIR (echo  TestUtils:   %TESTUTILS_DIR%) else (echo  TestUtils:   ^(skipped^))
echo ------------------------------------------------------------
echo  Steps: 1^) check WSL  2^) check/install distro  3^) network pre-check
echo         4^) run wsl-setup.sh  5^) network post-check  6^) sync docker
echo         7^) optional Testcontainers check
echo ============================================================
echo.

REM ------------------------------------------------------------
REM 1/7 - Is WSL itself installed and enabled?
REM ------------------------------------------------------------
echo [1/7] Checking WSL installation...
where wsl >nul 2>&1
if errorlevel 1 goto :NoWsl
wsl --status >nul 2>&1
if errorlevel 1 goto :WslNotEnabled
echo   WSL is installed and enabled.
goto :CheckDistro

:NoWsl
echo   "wsl" isn't available on this machine. Attempting "wsl --install"
echo   ^(needs Administrator + internet, and usually a reboot^)...
wsl --install
echo.
echo   Reboot Windows now, then run this script again.
pause
exit /b 1

:WslNotEnabled
echo   WSL command exists but isn't fully enabled. Attempting to enable it...
wsl --install --no-distribution
echo   Reboot Windows now, then run this script again.
pause
exit /b 1

REM ------------------------------------------------------------
REM 2/7 - Is the target distro installed?
REM ------------------------------------------------------------
:CheckDistro
echo [2/7] Checking for the "%DISTRO%" distro...
wsl -l -q 2>nul | findstr /I /C:"%DISTRO%" >nul
if errorlevel 1 goto :InstallDistro
echo   Found.
goto :AfterDistro

:InstallDistro
echo   Not found. Installing "%DISTRO%" ^(this can take a few minutes^)...
wsl --install -d %DISTRO%
if errorlevel 1 goto :DistroInstallFailed
echo.
echo   Installed. If a window opened asking for a UNIX username/password,
echo   finish that first, then run this script again to continue setup.
pause
exit /b 0

:DistroInstallFailed
echo.
echo   Install failed - try running this script as Administrator, or install
echo   "%DISTRO%" manually from the Microsoft Store, then run it again.
pause
exit /b 1

:AfterDistro
wsl --set-default-version 2 >nul 2>&1

REM Translate -Folder into a WSL path using the target distro's own
REM wslpath, so it works regardless of drive letter, username, or
REM OneDrive tenant name.
set "WSL_FOLDER="
for /f "usebackq delims=" %%P in (`wsl -d %DISTRO% wslpath -a "%IN_FOLDER%" 2^>nul`) do set "WSL_FOLDER=%%P"
if "%WSL_FOLDER:~-1%"=="/" set "WSL_FOLDER=%WSL_FOLDER:~0,-1%"
set "SH_PATH=%WSL_FOLDER%/%SH_RELATIVE_PATH%"

REM ------------------------------------------------------------
REM 3/7 - Network pre-check (optional companion script)
REM ------------------------------------------------------------
echo.
echo [3/7] Checking WSL network configuration...
if not exist "%NETFIX_PATH%" (
  echo   wsl-network-fix.ps1 not found in %IN_FOLDER% - skipping.
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%NETFIX_PATH%" -Phase Pre -Distro %DISTRO% -Port %DOCKER_TCP_PORT%
)

REM ------------------------------------------------------------
REM 4/7 - Provision inside WSL (optional companion script)
REM ------------------------------------------------------------
echo.
echo [4/7] Provisioning inside %DISTRO%...
if not exist "%IN_FOLDER%\%SH_RELATIVE_PATH%" (
  echo   %SH_RELATIVE_PATH% not found in %IN_FOLDER% - skipping.
) else if not defined WSL_FOLDER (
  echo   Could not resolve %IN_FOLDER% as a WSL path - skipping.
) else (
  wsl -d %DISTRO% -u root -- bash "%SH_PATH%"
  if errorlevel 1 goto :ProvisionFailed
)
goto :NetworkPost

:ProvisionFailed
echo.
echo Something failed above - see output.
pause
exit /b 1

REM ------------------------------------------------------------
REM 5/7 - Network post-check (optional companion script)
REM ------------------------------------------------------------
:NetworkPost
echo.
echo [5/7] Verifying Windows can reach the docker daemon...
if not exist "%NETFIX_PATH%" (
  echo   wsl-network-fix.ps1 not found in %IN_FOLDER% - skipping.
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%NETFIX_PATH%" -Phase Post -Distro %DISTRO% -Port %DOCKER_TCP_PORT%
  if errorlevel 1 (
    echo.
    echo Network self-fix could not make localhost:%DOCKER_TCP_PORT% reachable.
    echo This means the block is coming from Windows Firewall/corporate policy,
    echo not from WSL or docker - worth escalating to IT with that specific detail.
  )
)

REM ------------------------------------------------------------
REM 6/7 - Keep every Windows terminal/tool in sync
REM ------------------------------------------------------------
echo.
echo [6/7] Syncing Windows to the WSL docker daemon...
where docker >nul 2>&1
if errorlevel 1 goto :NoDockerCli

REM Docker Desktop (if installed) can silently reset the active docker
REM *context* back to its own default whenever it starts, which is why a
REM context alone isn't reliable here. DOCKER_HOST always overrides
REM whatever context is active, so we persist that as the real fix and
REM keep the context as a convenience for tools that specifically read it.
docker context inspect %DOCKER_CONTEXT_NAME% >nul 2>&1
if errorlevel 1 (
  docker context create %DOCKER_CONTEXT_NAME% --docker "host=tcp://localhost:%DOCKER_TCP_PORT%" >nul
) else (
  docker context update %DOCKER_CONTEXT_NAME% --docker "host=tcp://localhost:%DOCKER_TCP_PORT%" >nul
)
docker context use %DOCKER_CONTEXT_NAME% >nul
echo   Docker context "%DOCKER_CONTEXT_NAME%" -^> tcp://localhost:%DOCKER_TCP_PORT%

setx DOCKER_HOST "tcp://localhost:%DOCKER_TCP_PORT%" >nul
set "DOCKER_HOST=tcp://localhost:%DOCKER_TCP_PORT%"
echo   Persisted DOCKER_HOST=tcp://localhost:%DOCKER_TCP_PORT% ^(HKCU\Environment^).
echo   Every NEW terminal or tool that shells out to "docker" will use this
echo   automatically - nothing to re-set by hand, and it wins even if Docker
echo   Desktop resets its own context on startup.
echo   ^(Already-open terminals/IDE processes still need a restart to see it.^)

powershell -NoProfile -Command "$sig='[DllImport(\"user32.dll\",SetLastError=true,CharSet=CharSet.Auto)] public static extern IntPtr SendMessageTimeout(IntPtr h,uint m,UIntPtr w,string l,uint f,uint t,out UIntPtr r);'; Add-Type -Namespace Win32 -Name Native -MemberDefinition $sig; $r=[UIntPtr]::Zero; [Win32.Native]::SendMessageTimeout([IntPtr]0xffff,0x1a,[UIntPtr]::Zero,'Environment',2,5000,[ref]$r) | Out-Null" >nul 2>&1

echo.
docker version
goto :TestUtilsCheck

:NoDockerCli
echo   Docker CLI isn't installed on Windows - skipping.
echo   Containers are managed fine from inside WSL regardless.

REM ------------------------------------------------------------
REM 7/7 - Optional Testcontainers/Ryuk check
REM ------------------------------------------------------------
:TestUtilsCheck
echo.
echo [7/7] Optional Testcontainers check...
if not defined TESTUTILS_DIR (
  echo   No -TestUtils folder given - skipping.
  goto :End
)
where mvn >nul 2>&1
if errorlevel 1 (
  echo   mvn not found on PATH - skipping the real Testcontainers/Ryuk check.
  goto :End
)
if not exist "%TESTUTILS_DIR%" (
  echo   Folder not found at %TESTUTILS_DIR% - skipping.
  goto :End
)
echo   Running the actual failing module ^(TestUtils antrun -^> Testcontainers/Ryuk^)
echo   to verify end-to-end, not just port reachability...
pushd "%TESTUTILS_DIR%"
mvn -o -q process-classes > "%TEMP%\testutils-verify.log" 2>&1
set "MVN_EXIT=%errorlevel%"
popd
findstr /C:"is not listening" "%TEMP%\testutils-verify.log" >nul 2>&1
if not errorlevel 1 (
  echo   FAIL - Testcontainers still can't reach the docker daemon from this
  echo   module. Full log: %TEMP%\testutils-verify.log
  findstr /C:"DOCKER_HOST" /C:"is not listening" "%TEMP%\testutils-verify.log"
) else if "%MVN_EXIT%"=="0" (
  echo   PASS - TestUtils built cleanly, Ryuk/Testcontainers connected fine.
) else (
  echo   Build did not report the docker-host issue, but did not exit cleanly
  echo   ^(exit %MVN_EXIT%^) - see %TEMP%\testutils-verify.log for the real
  echo   reason ^(likely unrelated to networking^).
)

:End
echo.
echo Done. Dropping into a live WSL shell below - leave THIS window open to
echo keep WSL ^(and dockerd/containers inside it^) running. Closing it detaches
echo the last session, and Windows will shut the WSL VM down shortly after.
echo Minimize it if you don't need it, but don't close it.
echo.
wsl -d %DISTRO%
exit /b 0

:ShowUsage
echo Usage: wsl-setup-universal.bat [options]
echo   -Folder ^<path^>       Folder containing wsl-setup.sh / wsl-network-fix.ps1
echo   -Distro ^<name^>       WSL distro to use ^(default: Ubuntu^)
echo   -Port ^<number^>       Docker TCP port ^(default: 2375^)
echo   -ContextName ^<name^>  Docker context name to create ^(default: wsl^)
echo   -Sh ^<filename^>       Provisioning script filename ^(default: wsl-setup.sh^)
echo   -TestUtils ^<path^>    Optional Testcontainers/Ryuk check folder
echo.
echo Any option not given on the command line is asked for interactively.
exit /b
