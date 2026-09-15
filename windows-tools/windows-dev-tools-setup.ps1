# windows-dev-tools-setup.ps1 — one-click Windows dev machine setup.
# Installs Chocolatey (if missing), then via choco: Corretto JDK 21, Maven,
# Docker CLI, Node.js 22 (pinned — choco's nodejs/nodejs-lts packages track
# whatever the current release line is, which has since moved past 22),
# DBeaver Community, and IntelliJ IDEA Community.
#
# Idempotent — safe to re-run. choco itself no-ops packages already installed
# at the requested version; this script's own checks (e.g. for choco itself)
# do the same.
#
# Run via windows-dev-tools-setup.bat (double-click), or directly:
#   powershell -ExecutionPolicy Bypass -File windows-dev-tools-setup.ps1

# ---- EDIT IF YOU WANT DIFFERENT VERSIONS/TOOLS -----------------------------
$NodeVersion = "22.22.1"   # latest Node 22.x published on Chocolatey as of writing
# -----------------------------------------------------------------------------

# --- self-elevate (choco install needs Admin) --------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Re-launching elevated (one UAC prompt)..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Wait
    exit
}

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "== $msg ==" -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
# Chocolatey itself
# ---------------------------------------------------------------------------
Write-Step "Chocolatey"
if (Get-Command choco -ErrorAction SilentlyContinue) {
    Write-Host "Already installed ($((choco --version))) - skipping install." -ForegroundColor Green
} else {
    Write-Host "Installing Chocolatey..." -ForegroundColor Yellow
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
    # choco installs into a new PATH entry; refresh this session so `choco`
    # is callable for the rest of this script without reopening a terminal.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

function Install-ChocoPackage {
    param([string]$PackageId, [string]$Version = $null, [string]$DisplayName = $PackageId)
    Write-Step $DisplayName
    $args = @("install", $PackageId, "-y", "--no-progress")
    if ($Version) { $args += "--version=$Version" }
    & choco @args
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1641 -and $LASTEXITCODE -ne 3010) {
        # 1641/3010 = success but reboot required/initiated - not real failures
        Write-Host "choco install $PackageId exited with code $LASTEXITCODE - check output above." -ForegroundColor Red
    }
}

Install-ChocoPackage -PackageId "corretto21jdk"          -DisplayName "Amazon Corretto JDK 21"
Install-ChocoPackage -PackageId "maven"                  -DisplayName "Maven"
Install-ChocoPackage -PackageId "docker-cli"              -DisplayName "Docker CLI"
Install-ChocoPackage -PackageId "nodejs" -Version $NodeVersion -DisplayName "Node.js $NodeVersion"
Install-ChocoPackage -PackageId "dbeaver"                 -DisplayName "DBeaver Community Edition"
Install-ChocoPackage -PackageId "intellijidea-community"  -DisplayName "IntelliJ IDEA Community Edition"

Write-Host ""
Write-Host "== Done ==" -ForegroundColor Green
Write-Host "Open a NEW terminal (or sign out/in) for PATH updates (java, mvn, node, docker) to take effect everywhere."
