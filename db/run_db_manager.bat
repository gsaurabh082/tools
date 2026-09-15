@echo off
REM Double-click this file to open the DB Connection Manager interactive menu.
cd /d "%~dp0"
python db_manager.py %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Something went wrong. Make sure Python and pymysql are installed:
    echo     pip install pymysql --break-system-packages
)
pause
