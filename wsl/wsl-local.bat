@echo off
setlocal enabledelayedexpansion
REM Double-click this on Windows. It runs wsl-setup.sh inside WSL, with
REM self-diagnosing network setup on either side so Windows can actually
REM reach the docker daemon (and dynamically-published container ports,
REM e.g. Testcontainers' Ryuk) running inside it.
REM Edit DISTRO and SH_PATH below if yours differ.
REM
REM This is the machine-specific ("local") counterpart to wsl-universal.bat
REM in this same folder, which takes the folder/distro/etc. as arguments
REM instead of hardcoding them.

set DISTRO=Ubuntu
set SH_PATH=/mnt/c/Users/250020392/OneDrive - GE HealthCare/Desktop/local-setup/wsl/wsl-setup.sh
set NETFIX_PATH=%~dp0wsl-network-fix.ps1
set DOCKER_TCP_PORT=2375

echo Checking WSL network configuration (mirrored networking, no idle shutdown)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%NETFIX_PATH%" -Phase Pre -Distro %DISTRO% -Port %DOCKER_TCP_PORT%

wsl -d %DISTRO% -u root -- bash "%SH_PATH%"
if errorlevel 1 (
  echo.
  echo Something failed above — see output.
  pause
  exit /b 1
)

echo.
echo Verifying Windows can actually reach the docker daemon (auto-fixes if not)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%NETFIX_PATH%" -Phase Post -Distro %DISTRO% -Port %DOCKER_TCP_PORT%
if errorlevel 1 (
  echo.
  echo Network self-fix could not make localhost:%DOCKER_TCP_PORT% reachable.
  echo This means the block is coming from Windows Firewall/corporate policy,
  echo not from WSL or docker — worth escalating to IT with that specific detail.
)

echo.
echo Setting DOCKER_HOST for this window...
set DOCKER_HOST=tcp://localhost:2375

where docker >nul 2>&1
if errorlevel 1 (
  echo Docker CLI isn't installed on Windows, so skipping "docker version" here.
  echo Containers are managed fine from inside WSL regardless - this DOCKER_HOST
  echo is only useful if you install Docker CLI on Windows later.
) else (
  docker version
)

set TESTUTILS_DIR=C:\Users\250020392\project\dosewatch-all\commons\TestUtils
where mvn >nul 2>&1
if errorlevel 1 (
  echo.
  echo mvn not found on PATH - skipping the real Testcontainers/Ryuk check.
  echo ^(install via windows-tools\windows-dev-tools-setup.bat, or run it yourself
  echo  once Maven is available: mvn -o process-classes from TestUtils^)
) else if not exist "%TESTUTILS_DIR%" (
  echo.
  echo TestUtils module not found at %TESTUTILS_DIR% - skipping the real check.
) else (
  echo.
  echo Running the actual failing module ^(TestUtils antrun -^> Testcontainers/Ryuk^)
  echo to verify end-to-end, not just port reachability...
  pushd "%TESTUTILS_DIR%"
  mvn -o -q process-classes > "%TEMP%\testutils-verify.log" 2>&1
  set "MVN_EXIT=!errorlevel!"
  popd
  findstr /C:"is not listening" "%TEMP%\testutils-verify.log" >nul 2>&1
  set "FOUND_STALE=!errorlevel!"
  if "!FOUND_STALE!"=="0" (
    echo.
    echo FAIL - Testcontainers still can't reach the docker daemon from this
    echo module. Full log: %TEMP%\testutils-verify.log
    findstr /C:"DOCKER_HOST" /C:"is not listening" "%TEMP%\testutils-verify.log"
  ) else if "!MVN_EXIT!"=="0" (
    echo.
    echo PASS - TestUtils built cleanly, Ryuk/Testcontainers connected fine.
  ) else (
    echo.
    echo Build did not report the docker-host issue, but did not exit cleanly
    echo either ^(exit !MVN_EXIT!^) - see %TEMP%\testutils-verify.log for the
    echo real reason ^(likely unrelated to networking^).
  )
)

echo.
echo Done. Dropping into a live WSL shell below — leave THIS window open to
echo keep WSL (and dockerd/containers inside it) running. Closing it detaches
echo the last session, and Windows will shut the WSL VM down shortly after.
echo Minimize it if you don't need it, but don't close it.
echo.
wsl -d %DISTRO%
