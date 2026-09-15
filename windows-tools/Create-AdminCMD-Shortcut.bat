@echo off
setlocal

fltmc >nul 2>&1
if not "%errorlevel%"=="0" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "PS1=%TEMP%\CreateAdminCMD.ps1"

> "%PS1%" (
echo $ErrorActionPreference = 'Stop'
echo $taskName = 'AdminCMD'
echo $startDir = 'C:\Users\250020392\OneDrive - GE HealthCare\Desktop'
echo if (-not ^(Test-Path $startDir^)^) { $startDir = $env:USERPROFILE }
echo $user = "$env:USERDOMAIN\$env:USERNAME"
echo $arg = "/k cd /d ""$startDir"""
echo $action = New-ScheduledTaskAction -Execute "$env:ComSpec" -Argument $arg
echo $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
echo Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Description 'Open elevated Command Prompt' -Force ^| Out-Null
echo $desktop = :GetFolderPath('Desktop')
echo $lnkPath = Join-Path $desktop 'Admin CMD.lnk'
echo $wsh = New-Object -ComObject WScript.Shell
echo $shortcut = $wsh.CreateShortcut($lnkPath)
echo $shortcut.TargetPath = "$env:windir\System32\schtasks.exe"
echo $shortcut.Arguments = '/run /tn "AdminCMD"'
echo $shortcut.WorkingDirectory = "$env:USERPROFILE"
echo $shortcut.IconLocation = "$env:windir\System32\cmd.exe,0"
echo $shortcut.WindowStyle = 1
echo $shortcut.Save()
echo Write-Host "AdminCMD task and Desktop shortcut created successfully."
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"

del "%PS1%" >nul 2>&1

echo(
echo Done.
echo Desktop shortcut created: Admin CMD
echo Right-click it and choose Pin to taskbar.
echo(
pause
