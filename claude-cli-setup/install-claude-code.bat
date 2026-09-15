@echo off
setlocal enabledelayedexpansion
echo === Claude Code CLI installer ===
echo.

where node >nul 2>nul
if errorlevel 1 (
    echo Node.js not found on PATH.
    echo.
    where winget >nul 2>nul
    if errorlevel 1 (
        echo winget isn't available either. Please install Node.js manually:
        echo   1. Go to https://nodejs.org and download the LTS installer.
        echo   2. Run it with default options.
        echo   3. Close this window, open a NEW terminal, and re-run this script.
        pause
        exit /b 1
    )
    echo Trying winget ^(Windows Package Manager^) to install Node.js LTS...
    winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
    echo.
    echo Node.js install attempted. Close this window, open a NEW terminal
    echo ^(so PATH picks up the change^), and re-run this script.
    pause
    exit /b 0
)

echo Found Node.js:
node --version
call npm --version
echo.

rem GE's corporate network re-signs outbound HTTPS with its own root CAs
rem (see ..\certs\gehealthcarerootca1/2.pem) - Node/npm don't trust those by
rem default even though Windows does, which is exactly what
rem SELF_SIGNED_CERT_IN_CHAIN means. Point Node at a bundle of both so npm
rem (and later, Claude Code's own API calls) go through cleanly.
set "CA_BUNDLE=%~dp0ge-ca-bundle.pem"
if exist "%CA_BUNDLE%" (
    echo Using GE root CA bundle for this session: %CA_BUNDLE%
    set "NODE_EXTRA_CA_CERTS=%CA_BUNDLE%"
    setx NODE_EXTRA_CA_CERTS "%CA_BUNDLE%" >nul
) else (
    echo Warning: %CA_BUNDLE% not found - skipping corporate CA setup.
)
echo.

echo Installing Claude Code CLI globally via npm...
call npm install -g @anthropic-ai/claude-code
if errorlevel 1 (
    echo.
    echo npm install failed - see the error above.
    echo If it says SELF_SIGNED_CERT_IN_CHAIN, close this window and re-run
    echo this script once more from a NEW terminal - the CA bundle setting
    echo above needs a fresh session to fully apply everywhere.
    echo ^(If it's a permissions error instead, try running this .bat as
    echo  Administrator, or ask IT if npm global installs are blocked.^)
    pause
    exit /b 1
)

echo.
echo Verifying install...
where claude
if errorlevel 1 (
    echo.
    echo 'claude' isn't on PATH in THIS window yet - that's normal right after
    echo install. Close this terminal, open a brand NEW one, and run:
    echo     where claude
    echo If that shows a path, you're set. Then restart run_db_web.bat so it
    echo picks up the new PATH.
    pause
    exit /b 0
)

echo.
echo Claude Code CLI installed. Logging in now ^(this may open your browser^)...
call claude --version
echo.
echo If you weren't prompted to log in above, just run "claude" once by itself
echo to complete login with your Claude account.
echo.
echo Once logged in, fully close and re-launch run_db_web.bat so the DB
echo Manager's Ask Claude tab can find and use "claude" too.
pause
