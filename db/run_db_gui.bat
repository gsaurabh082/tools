@echo off
REM Double-click this to open the DB Connection Manager desktop window.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw db_manager_gui.py
) else (
    python db_manager_gui.py
)
