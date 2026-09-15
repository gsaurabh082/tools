@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    set "PYTHON=python"
) else (
    set "PYTHON=py -3"
)

%PYTHON% -c "import fastapi, uvicorn, psutil" >nul 2>nul
if errorlevel 1 (
    echo Installing the required Python packages...
    %PYTHON% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Installation failed. Confirm that Python and pip are installed.
        pause
        exit /b 1
    )
)

for /f %%P in ('powershell -NoProfile -Command "$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0); $listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop(); $port"') do set "PORT=%%P"
if not defined PORT (
    echo Could not select a free local port.
    pause
    exit /b 1
)

echo Starting Port and Process Detective at http://127.0.0.1:%PORT%
start "Port and Process Detective" http://127.0.0.1:%PORT%
%PYTHON% -m uvicorn app:app --host 127.0.0.1 --port %PORT%

echo.
echo Server stopped.
pause
