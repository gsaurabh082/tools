@echo off
echo Stopping keep-awake script and cleaning up...

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'keep-awake-typer\.ps1' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
powershell -NoProfile -Command "Get-Process notepad -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like '*keepawake_scratch*' } | Stop-Process -Force"

del /q "%~dp0keepawake_scratch_*.txt" 2>nul

echo Done - script stopped, scratch file removed, normal sleep behavior restored.
pause
