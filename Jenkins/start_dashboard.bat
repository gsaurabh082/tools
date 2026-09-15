@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD=py"
where py >nul 2>nul
if errorlevel 1 set "PYTHON_CMD=python"

if not exist ".venv\Scripts\python.exe" (
  echo [Jenkins Chain] Creating the local Python environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :failed
)

echo [Jenkins Chain] Checking dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :failed

if not exist ".env" (
  echo.
  echo [Jenkins Chain] No .env found. Copy .env.example to .env and fill in
  echo [Jenkins Chain] JENKINS_USER / JENKINS_API_TOKEN / GITLAB_TOKEN first.
  echo.
)

echo [Jenkins Chain] Starting on an automatically chosen free port...
".venv\Scripts\python.exe" run.py
goto :eof

:failed
echo.
echo [Jenkins Chain] Setup failed. Review the error above.
pause
exit /b 1
