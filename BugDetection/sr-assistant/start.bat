@echo off
setlocal
cd /d "%~dp0"

echo.
echo  DoseWatch SR Assistant
echo  ========================
echo.

:: ── First-time setup ─────────────────────────────────────────────
if not exist "backend\.env" (
    copy ".env.example" "backend\.env" >nul
    echo  [SETUP] Created backend\.env from .env.example
    echo         ^> Edit backend\.env and add your API keys before using the app.
    echo.
)

if not exist "backend\node_modules" (
    echo  [SETUP] Installing backend dependencies...
    pushd backend
    call npm install --silent
    popd
    echo  [SETUP] Backend dependencies installed.
    echo.
)

if not exist "frontend\node_modules" (
    echo  [SETUP] Installing frontend dependencies...
    pushd frontend
    call npm install --silent
    popd
    echo  [SETUP] Frontend dependencies installed.
    echo.
)

:: ── Start services ────────────────────────────────────────────────
echo  [START] Starting backend on http://localhost:3001 ...
start "SR-Assistant Backend" cmd /k "cd /d "%~dp0backend" && npm run dev"

echo  [START] Waiting for backend to initialise...
timeout /t 4 /nobreak >nul

echo  [START] Starting frontend on http://localhost:5173 ...
start "SR-Assistant Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"

echo  [START] Waiting for frontend to initialise...
timeout /t 4 /nobreak >nul

:: ── Open browser ──────────────────────────────────────────────────
echo  [OPEN]  Opening http://localhost:5173 in browser...
start "" "http://localhost:5173"

echo.
echo  SR Assistant is running.
echo  Backend  ^> http://localhost:3001
echo  Frontend ^> http://localhost:5173
echo.
echo  Close the two terminal windows to stop the services.
echo.
pause
