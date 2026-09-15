@echo off
mkdir C:\Tools 2>nul

(
echo @echo off
echo doskey desktop=cd /d "C:\Users\250020392\OneDrive - GE HealthCare\Desktop"
) > C:\Tools\aliases.cmd

reg add "HKCU\Software\Microsoft\Command Processor" /v AutoRun /t REG_SZ /d "C:\Tools\aliases.cmd" /f

echo.
echo Desktop command installed successfully.
echo Close all CMD windows and open a new CMD.
echo Then simply type:
echo.
echo desktop
echo.
pause
