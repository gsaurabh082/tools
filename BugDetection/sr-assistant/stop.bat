@echo off
echo  Stopping SR Assistant...
taskkill /FI "WINDOWTITLE eq SR-Assistant Backend*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq SR-Assistant Frontend*" /T /F >nul 2>&1
echo  Done.
