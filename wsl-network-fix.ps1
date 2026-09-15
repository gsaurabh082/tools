# wsl-network-fix.ps1 — self-diagnosing WSL<->Windows docker networking fix.
# Called automatically by wsl-setup.bat. Not meant to be run manually, but
# safe to if you want to (defaults to -Phase Pre).
#
# Two phases:
#   -Phase Pre   Ensures .wslconfig has WSL2 mirrored networking + no idle
#                shutdown. Mirrored networking is what makes arbitrary
#                dynamically-published container ports (e.g. Testcontainers'
#                Ryuk resource reaper) reachable from Windows as plain
#                localhost, instead of just whatever single port a manual
#                portproxy rule happens to forward. Restarts WSL if it had
#                to change anything (required for the setting to take effect).
#
#   -Phase Post  After docker/compose is up, actually tests whether
#                localhost:<port> is reachable from Windows. If mirrored
#                networking isn't available on this Windows build, falls
#                back to an explicit (self-elevating) netsh portproxy rule
#                pointing at the current WSL IP, then re-tests.
param(
    [ValidateSet("Pre","Post")]
    [string]$Phase = "Pre",
    [string]$Distro = "Ubuntu",
    [int]$Port = 2375
)

function Ensure-WslConfigMirrored {
    $path = "$env:USERPROFILE\.wslconfig"
    $lines = @()
    if (Test-Path $path) { $lines = @(Get-Content $path) }

    $desired = [ordered]@{ "networkingMode" = "mirrored"; "vmIdleTimeout" = "-1" }
    $changed = $false

    $sectionStart = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Trim() -eq "[wsl2]") { $sectionStart = $i; break }
    }

    $sectionEnd = $lines.Count
    $sectionLines = @()
    if ($sectionStart -ge 0) {
        $sectionEnd = $lines.Count
        for ($i = $sectionStart + 1; $i -lt $lines.Count; $i++) {
            if ($lines[$i].Trim() -match '^\[.*\]$') { $sectionEnd = $i; break }
        }
        if ($sectionEnd -gt $sectionStart + 1) {
            $sectionLines = @($lines[($sectionStart + 1)..($sectionEnd - 1)])
        }
    }

    foreach ($key in $desired.Keys) {
        $val = $desired[$key]
        $found = $false
        for ($i = 0; $i -lt $sectionLines.Count; $i++) {
            if ($sectionLines[$i] -match "^\s*$key\s*=") {
                if ($sectionLines[$i].Trim() -ne "$key=$val") {
                    $sectionLines[$i] = "$key=$val"
                    $changed = $true
                }
                $found = $true
                break
            }
        }
        if (-not $found) {
            $sectionLines += "$key=$val"
            $changed = $true
        }
    }

    if ($sectionStart -ge 0) {
        $before = if ($sectionStart -ge 0) { @($lines[0..$sectionStart]) } else { @() }
        $after = if ($sectionEnd -le $lines.Count - 1) { @($lines[$sectionEnd..($lines.Count - 1)]) } else { @() }
        $newLines = $before + $sectionLines + $after
    } else {
        $newLines = $lines + @("[wsl2]") + $sectionLines
    }

    if ($changed) {
        Set-Content -Path $path -Value $newLines
    }
    return $changed
}

function Clear-StaleDockerHostEnvVar {
    # A permanently-set DOCKER_HOST env var (User or Machine scope - e.g. left
    # over from an old `setx`/start-docker.ps1 run pointing at a since-changed
    # WSL IP) silently overrides .testcontainers.properties every time, since
    # env vars have higher precedence. This is exactly what caused Testcontainers
    # to keep using a stale IP no matter how many times the properties file got
    # fixed. .testcontainers.properties is now the single source of truth for
    # this, so any persisted DOCKER_HOST is cleared rather than kept in sync.
    $userVal = [System.Environment]::GetEnvironmentVariable("DOCKER_HOST", "User")
    if ($userVal) {
        Write-Host "Clearing stale User-scope DOCKER_HOST ($userVal)" -ForegroundColor Yellow
        [System.Environment]::SetEnvironmentVariable("DOCKER_HOST", $null, "User")
    }

    $machineVal = [System.Environment]::GetEnvironmentVariable("DOCKER_HOST", "Machine")
    if ($machineVal) {
        $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if ($isAdmin) {
            Write-Host "Clearing stale Machine-scope DOCKER_HOST ($machineVal)" -ForegroundColor Yellow
            [System.Environment]::SetEnvironmentVariable("DOCKER_HOST", $null, "Machine")
        } else {
            Write-Host "Machine-scope DOCKER_HOST is set ($machineVal) and clearing it needs admin - re-launching elevated (one UAC prompt)..." -ForegroundColor Yellow
            Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command `"[System.Environment]::SetEnvironmentVariable('DOCKER_HOST',`$null,'Machine')`"" -Wait
        }
    }
}

if ($Phase -eq "Pre") {
    Write-Host "== Checking .wslconfig (mirrored networking + no idle shutdown) ==" -ForegroundColor Cyan
    $changed = Ensure-WslConfigMirrored
    if ($changed) {
        Write-Host "Updated .wslconfig - restarting WSL for it to take effect..." -ForegroundColor Yellow
        wsl --shutdown
        Start-Sleep -Seconds 5
        Write-Host "Done." -ForegroundColor Green
    } else {
        Write-Host ".wslconfig already correct - no restart needed." -ForegroundColor Green
    }

    Write-Host "== Checking for a stale persisted DOCKER_HOST env var ==" -ForegroundColor Cyan
    Clear-StaleDockerHostEnvVar
    exit 0
}

if ($Phase -eq "Post") {
    Write-Host "== Self-testing: is localhost:$Port reachable from Windows? ==" -ForegroundColor Cyan
    $test = Test-NetConnection -ComputerName localhost -Port $Port -WarningAction SilentlyContinue
    if ($test.TcpTestSucceeded) {
        Write-Host "PASS - localhost:$Port is reachable. Mirrored networking/forwarding is working." -ForegroundColor Green
        exit 0
    }

    Write-Host "FAIL - localhost:$Port is not reachable. Falling back to an explicit portproxy rule." -ForegroundColor Yellow

    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Write-Host "Re-launching elevated to configure netsh portproxy (one UAC prompt)..." -ForegroundColor Yellow
        Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Phase Post -Distro $Distro -Port $Port" -Wait
        $test2 = Test-NetConnection -ComputerName localhost -Port $Port -WarningAction SilentlyContinue
        if ($test2.TcpTestSucceeded) {
            Write-Host "PASS - localhost:$Port reachable now via portproxy fallback." -ForegroundColor Green
            exit 0
        } else {
            Write-Host "STILL FAILING after portproxy fallback. Likely a Windows Firewall policy blocking this port/profile - needs manual investigation with your IT/security team." -ForegroundColor Red
            exit 1
        }
    }

    $wslIp = (wsl -d $Distro hostname -I).Trim().Split(" ")[0]
    if ([string]::IsNullOrWhiteSpace($wslIp)) {
        Write-Host "Could not determine WSL IP for portproxy fallback." -ForegroundColor Red
        exit 1
    }
    netsh interface portproxy delete v4tov4 listenport=$Port listenaddress=127.0.0.1 2>$null | Out-Null
    netsh interface portproxy add v4tov4 listenport=$Port listenaddress=127.0.0.1 connectport=$Port connectaddress=$wslIp | Out-Null
    Write-Host "Added portproxy rule: 127.0.0.1:$Port -> ${wslIp}:$Port" -ForegroundColor Green

    $test3 = Test-NetConnection -ComputerName localhost -Port $Port -WarningAction SilentlyContinue
    if ($test3.TcpTestSucceeded) {
        Write-Host "PASS - reachable now." -ForegroundColor Green
        exit 0
    } else {
        Write-Host "STILL FAILING even after portproxy. This is almost certainly a Windows Firewall policy blocking inbound connections on this port/profile - needs your IT/security team, not something a script can fix." -ForegroundColor Red
        exit 1
    }
}
