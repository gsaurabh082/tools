@echo off
REM Double-click this. It installs Chocolatey (if missing) then, via choco:
REM Corretto JDK 21, Maven, Docker CLI, Node.js 22, DBeaver, and IntelliJ IDEA
REM Community Edition. Self-elevates (one UAC prompt) since choco needs Admin.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows-dev-tools-setup.ps1"
pause
