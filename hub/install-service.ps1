#Requires -Version 5.1
<#
.SYNOPSIS
    Clean install + register DevHub as a Windows startup task.
    Steps: locate Python -> stop hub -> clean workspace -> wipe packages -> fresh install -> register -> launch
    Never touches your data: bookmarks.json and ~/.devhub/profile.json are preserved.
#>

$ErrorActionPreference = "Stop"
$appName     = "DevHub"
$taskName    = "DevHub"
# earlier names of this app - removed on install so login doesn't start two hubs
$legacyTasks = @("MyDevHub", "LocalHub")
$hubPort    = 7272
$hubDir     = $PSScriptRoot
$hubScript  = Join-Path $hubDir "hub.py"
$account    = "$env:USERDOMAIN\$env:USERNAME"

Write-Host ""
Write-Host "  * $appName Installer" -ForegroundColor Cyan
Write-Host "  ---------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

# -- 1. Locate Python ---------------------------------------------------------
Write-Host "[1/6] Locating Python..." -ForegroundColor Cyan

$pyExe = $null
foreach ($cmd in @("python.exe", "py.exe")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) { $pyExe = $found.Source; break }
}
if (-not $pyExe) { throw "Python 3 not found. Install from https://python.org and retry." }

# Ask Python itself for sys.executable -- the only reliable way when py.exe
# (the launcher) or a Microsoft Store stub is on PATH instead of the real binary.
$realPy = (& $pyExe -c "import sys; print(sys.executable)" 2>&1).Trim()
if ($realPy -and (Test-Path $realPy)) { $pyExe = $realPy }

$pyDir  = Split-Path $pyExe
$pywExe = Join-Path $pyDir "pythonw.exe"
if (-not (Test-Path $pywExe)) { $pywExe = $pyExe }

Write-Host "  python  : $pyExe"  -ForegroundColor DarkGray
Write-Host "  pythonw : $pywExe" -ForegroundColor DarkGray

# Persist path so start-hub.vbs never has to search PATH
$pywExe | Out-File (Join-Path $hubDir "_pythonw.txt") -Encoding UTF8 -NoNewline

# -- 2. Stop any running hub ---------------------------------------------------------
Write-Host "[2/6] Stopping any running hub..." -ForegroundColor Cyan

# NOTE: $pid is a read-only PowerShell automatic variable -- never assign to it.
$owners = @()
try {
    $owners = Get-NetTCPConnection -LocalPort $hubPort -State Listen -ErrorAction Stop |
              Select-Object -ExpandProperty OwningProcess -Unique
} catch {
    # Get-NetTCPConnection is absent on some builds -- fall back to netstat
    $owners = netstat -ano 2>$null |
              Select-String "TCP\s+\S+:$hubPort\s" |
              ForEach-Object { ($_.Line.Trim() -split "\s+")[-1] } |
              Select-Object -Unique
}

$killed = 0
foreach ($owner in $owners) {
    $procId = 0
    if (-not [int]::TryParse("$owner", [ref]$procId)) { continue }
    if ($procId -le 0 -or $procId -eq $PID) { continue }
    try {
        $name = (Get-Process -Id $procId -ErrorAction Stop).ProcessName
        Stop-Process -Id $procId -Force -ErrorAction Stop
        Write-Host "  Stopped $name (PID $procId)" -ForegroundColor DarkGray
        $killed++
    } catch {
        Write-Host "  Could not stop PID $procId - $($_.Exception.Message)" -ForegroundColor Yellow
    }
}
# Catch strays too: a hub that crashed off-port, or one bound to a different port
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*hub.py*" -and $_.ProcessId -ne $PID } |
    ForEach-Object {
        try {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
            Write-Host "  Stopped stray hub.py (PID $($_.ProcessId))" -ForegroundColor DarkGray
            $killed++
        } catch {}
    }

if ($killed -eq 0) { Write-Host "  Nothing was running." -ForegroundColor DarkGray }
Start-Sleep -Milliseconds 800

# -- 3. Clean generated files -------------------------------------------------
Write-Host "[3/6] Cleaning workspace..." -ForegroundColor Cyan

# hub.py writes a _launch_<id>.vbs per service launch; they accumulate
$junk = @()
$junk += Get-ChildItem -Path $hubDir -Filter "_launch_*.vbs" -File -ErrorAction SilentlyContinue
$junk += Get-ChildItem -Path $hubDir -Filter "__pycache__" -Directory -Recurse -ErrorAction SilentlyContinue
$junk += Get-ChildItem -Path $hubDir -Filter "*.pyc" -File -Recurse -ErrorAction SilentlyContinue

foreach ($item in $junk) {
    try { Remove-Item $item.FullName -Recurse -Force -ErrorAction Stop } catch {}
}
Write-Host "  Removed $($junk.Count) generated file(s)." -ForegroundColor DarkGray
Write-Host "  Kept your bookmarks and profile." -ForegroundColor DarkGray

# -- 4. Remove old packages for a clean slate ---------------------------------
Write-Host "[4/6] Removing old packages..." -ForegroundColor Cyan

$remove = @("fastapi","uvicorn","psutil","starlette","anyio","httptools",
            "websockets","watchfiles","python-multipart","h11","click")
foreach ($pkg in $remove) {
    & $pyExe -m pip uninstall -y $pkg 2>&1 | Out-Null
}
Write-Host "  Done." -ForegroundColor DarkGray

# -- 5. Fresh install ---------------------------------------------------------
Write-Host "[5/6] Installing latest dependencies..." -ForegroundColor Cyan

& $pyExe -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }

& $pyExe -m pip install --upgrade --force-reinstall -r (Join-Path $hubDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed. See errors above." }

# Verify imports before registering anything
$check = & $pyExe -c "import fastapi, uvicorn, psutil; print('OK')" 2>&1
if ("$check".Trim() -ne "OK") { throw "Import verification failed: $check" }
Write-Host "  Imports OK." -ForegroundColor Green

# -- 6. Register startup task -------------------------------------------------
Write-Host "[6/6] Registering Task Scheduler task '$taskName'..." -ForegroundColor Cyan

# Drop pre-rename tasks so the hub does not get started twice at login
foreach ($legacy in $legacyTasks) {
    if (Get-ScheduledTask -TaskName $legacy -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $legacy -Confirm:$false
        Write-Host "  Removed legacy task '$legacy'." -ForegroundColor DarkGray
    }
}

$action   = New-ScheduledTaskAction -Execute $pywExe `
                -Argument "`"$hubScript`" --no-browser" -WorkingDirectory $hubDir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $account
$settings = New-ScheduledTaskSettingsSet `
                -ExecutionTimeLimit (New-TimeSpan -Hours 0) -RestartCount 3 `
                -RestartInterval (New-TimeSpan -Minutes 1) `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

function Register-HubTask([string]$RunLevel) {
    $principal = New-ScheduledTaskPrincipal -UserId $account `
                     -LogonType Interactive -RunLevel $RunLevel
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
}

try {
    Register-HubTask "Highest"
} catch {
    # -RunLevel Highest needs elevation; a normal task works fine for a local web app
    Write-Host "  Not elevated - registering without admin rights." -ForegroundColor Yellow
    Register-HubTask "Limited"
}

Write-Host ""
Write-Host "  [OK] Task '$taskName' registered - auto-starts at every login." -ForegroundColor Green
Write-Host "  [OK] URL : http://127.0.0.1:$hubPort" -ForegroundColor Green
Write-Host ""

# -- Launch now ---------------------------------------------------------------
$ans = Read-Host "Launch $appName now? [Y/n]"
if ($ans -notin @('n','N')) {
    Write-Host "  Starting hub..." -ForegroundColor Cyan
    Start-Process $pywExe -ArgumentList "`"$hubScript`" --no-browser" `
        -WorkingDirectory $hubDir -WindowStyle Hidden

    Write-Host "  Waiting for port $hubPort (up to 15 s)..." -ForegroundColor DarkGray
    $ready = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        try {
            # a bound socket isn't "up" -- ask for the page itself
            $null = Invoke-WebRequest "http://127.0.0.1:$hubPort/api/user" `
                        -UseBasicParsing -TimeoutSec 2
            $ready = $true; break
        } catch {}
    }

    if ($ready) {
        Write-Host "  Hub is live - opening browser." -ForegroundColor Green
        Start-Process "http://127.0.0.1:$hubPort"
    } else {
        Write-Host ""
        Write-Host "  [WARN] Hub did not respond on :$hubPort within 15 s." -ForegroundColor Yellow
        Write-Host "  Run manually to see errors:" -ForegroundColor Yellow
        Write-Host "    $pyExe `"$hubScript`"" -ForegroundColor Yellow
    }
}
