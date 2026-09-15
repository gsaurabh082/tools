@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD=py"
where py >nul 2>nul
if errorlevel 1 set "PYTHON_CMD=python"

if not exist ".venv\Scripts\python.exe" (
  echo [GitLab Focus] Creating the local Python environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :failed
)

echo [GitLab Focus] Checking dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :failed

if not exist ".env" (
  echo.
  echo [GitLab Focus] No .env found. Starting with preview data.
  echo [GitLab Focus] Copy .env.example to .env and add GITLAB_TOKEN for live data.
  echo.
)

echo [GitLab Focus] Starting on an automatically chosen free port...
".venv\Scripts\python.exe" run.py
goto :eof

:failed
echo.
echo [GitLab Focus] Setup failed. Review the error above.
pause
exit /b 1
