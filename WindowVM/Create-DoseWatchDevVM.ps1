<#
.SYNOPSIS
    One-click bootstrap for a minimum-footprint Windows dev/QA VM (VirtualBox),
    sized for GE HealthCare DoseWatch install/testing.

.NOTES
    Run via Run-DoseWatchDevVM.bat (double-click it), or manually as Administrator:
      1. Right-click PowerShell -> "Run as Administrator"
      2. cd to the folder containing this script
      3. Set-ExecutionPolicy -Scope Process Bypass -Force
      4. .\Create-DoseWatchDevVM.ps1

    What it does, every time you run it:
      1. Installs VirtualBox via winget if not already present.
      2. Gets a Windows Server 2022 evaluation ISO (auto-download; falls back
         to a one-time manual browser step only if that fails).
      3. Creates the VM if it doesn't exist yet, or repairs/reuses it if it does.
      4. Attaches the OS disk and the ISO. If attaching the ISO fails (a
         genuinely corrupt/incomplete download), it automatically deletes the
         bad file, redownloads once, and retries - no manual cleanup needed
         in the common case.
      5. Boots the VM in a GUI window.

    Safe to re-run any time - every step is idempotent.
#>

#Requires -RunAsAdministrator

$ErrorActionPreference = "Continue"
# Deliberately "Continue", not "Stop": PowerShell promotes native-command
# stderr text into a terminating error under "Stop", which breaks expected/
# harmless conditions like "storage controller already exists". Every
# VBoxManage call below goes through Invoke-VBox, which checks success via
# the process's real exit code, not PowerShell's error stream - so genuine
# failures still stop the script, just without this false-positive class of
# crash.

# ---- Configurable settings ----
$VmName        = "DoseWatch-DevQA"
$VmBaseDir     = "$env:USERPROFILE\VMs"
$IsoDir        = "$env:USERPROFILE\VMs\ISOs"
$IsoPath       = "$IsoDir\WindowsServer2022_Eval.iso"
$IsoUrl        = "https://go.microsoft.com/fwlink/p/?LinkID=2195174&clcid=0x409&culture=en-us&country=US"
$EvalPage      = "https://www.microsoft.com/en-us/evalcenter/evaluate-windows-server-2022"
$IsoMinBytes   = 2GB   # real eval ISOs run 4-6GB; anything under 2GB is not the real file
$VmMemoryMB    = 8192
$VmCpus        = 4
$VmDiskGB      = 100
$VBoxManage    = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
$ScriptVersion = "2026-08-06-v8"

Write-Host "Create-DoseWatchDevVM.ps1 - version $ScriptVersion" -ForegroundColor DarkGray

function Write-Step($msg) { Write-Host "`n== $msg ==" -ForegroundColor Cyan }

function Format-ProcessArg([string]$Arg) {
    if ($Arg -match '[\s"]') { return '"' + ($Arg -replace '"', '\"') + '"' }
    return $Arg
}

function Invoke-VBox {
    # Runs a VBoxManage command via raw .NET Process rather than PowerShell's
    # native-command invocation, reading stdout/stderr directly as plain
    # strings. This sidesteps PowerShell's stderr/$ErrorActionPreference
    # interaction, which is inconsistent across versions and was the root
    # cause of earlier false crashes/false negatives in this script.
    param([Parameter(Mandatory)][string[]]$CmdArgs)

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $VBoxManage
    $psi.Arguments = ($CmdArgs | ForEach-Object { Format-ProcessArg([string]$_) }) -join ' '
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow  = $true

    $proc = [System.Diagnostics.Process]::Start($psi)
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()

    $text = ($stdout + $stderr).Trim()
    if ($text.Length -gt 0) { Write-Host $text }

    [PSCustomObject]@{
        ExitCode = $proc.ExitCode
        Text     = $text
    }
}

function Get-FileWithProgress {
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$OutFile
    )

    $request = [System.Net.HttpWebRequest]::Create($Url)
    $request.AllowAutoRedirect = $true
    $response = $request.GetResponse()
    $totalBytes = $response.ContentLength
    $responseStream = $response.GetResponseStream()
    $targetStream = [System.IO.File]::Open($OutFile, [System.IO.FileMode]::Create)

    $buffer = New-Object byte[] 1MB
    $totalRead = 0L
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $lastUpdate = [System.Diagnostics.Stopwatch]::StartNew()

    try {
        do {
            $read = $responseStream.Read($buffer, 0, $buffer.Length)
            if ($read -gt 0) {
                $targetStream.Write($buffer, 0, $read)
                $totalRead += $read

                if ($lastUpdate.ElapsedMilliseconds -ge 200 -or $read -lt $buffer.Length) {
                    $mbRead = [math]::Round($totalRead / 1MB, 1)
                    $speed  = if ($sw.Elapsed.TotalSeconds -gt 0) { [math]::Round(($totalRead / 1MB) / $sw.Elapsed.TotalSeconds, 2) } else { 0 }

                    if ($totalBytes -gt 0) {
                        $pct     = [math]::Min(100, [math]::Round(($totalRead / $totalBytes) * 100, 1))
                        $mbTotal = [math]::Round($totalBytes / 1MB, 1)
                        $etaSec  = if ($speed -gt 0) { [math]::Round(($mbTotal - $mbRead) / $speed, 0) } else { 0 }
                        Write-Progress -Activity "Downloading $(Split-Path $OutFile -Leaf)" `
                            -Status "$mbRead MB / $mbTotal MB  |  $speed MB/s  |  ETA ${etaSec}s" `
                            -PercentComplete $pct
                    } else {
                        Write-Progress -Activity "Downloading $(Split-Path $OutFile -Leaf)" `
                            -Status "$mbRead MB downloaded  |  $speed MB/s"
                    }
                    $lastUpdate.Restart()
                }
            }
        } while ($read -gt 0)
    } finally {
        $targetStream.Close()
        $responseStream.Close()
        $response.Close()
        Write-Progress -Activity "Downloading $(Split-Path $OutFile -Leaf)" -Completed
    }

    if ($totalBytes -gt 0 -and $totalRead -ne $totalBytes) {
        throw "Download incomplete: got $totalRead of $totalBytes bytes (connection likely dropped)."
    }
}

function Test-IsoLooksReal {
    # Deliberately simple: just a size floor. Real eval ISOs are several GB;
    # a redirected HTML/registration page or truncated download will be tiny
    # or clearly short. We do NOT try to pre-validate the exact medium format
    # here - VBoxManage's own type-detection for that (showmediuminfo) proved
    # unreliable across attempts. The real attach step later is the actual
    # source of truth, with automatic retry if it fails.
    param([Parameter(Mandatory)][string]$Path)
    (Test-Path $Path) -and ((Get-Item $Path).Length -ge $IsoMinBytes)
}

function Add-StorageControllerIfMissing {
    param($Name, $Type, $Controller)
    $r = Invoke-VBox -CmdArgs @("storagectl", $VmName, "--name", $Name, "--add", $Type, "--controller", $Controller)
    if ($r.ExitCode -ne 0 -and $r.Text -notmatch "already exists") {
        Write-Error "Failed to add storage controller '$Name': $($r.Text)"
        exit 1
    }
}

try {

# ---- Step 1: VirtualBox ----
Write-Step "Checking / installing VirtualBox"
if (-not (Test-Path $VBoxManage)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Oracle.VirtualBox --accept-package-agreements --accept-source-agreements
    } else {
        Write-Warning "winget not found on this machine."
        Write-Host "Install VirtualBox manually: https://www.virtualbox.org/wiki/Downloads"
        Write-Host "Then re-run this script."
        exit 1
    }
}
if (-not (Test-Path $VBoxManage)) {
    Write-Error "VirtualBox still not found at '$VBoxManage'. Open a new terminal (PATH may need to refresh) and re-run."
    exit 1
}
Write-Host "VirtualBox OK: $VBoxManage"

# ---- Step 2: Folders ----
Write-Step "Preparing folders"
New-Item -ItemType Directory -Force -Path $VmBaseDir | Out-Null
New-Item -ItemType Directory -Force -Path $IsoDir | Out-Null

# ---- Step 3: Windows ISO (size-sanity only; real validation happens at attach) ----
Write-Step "Getting Windows Server 2022 evaluation ISO"

if (-not (Test-IsoLooksReal $IsoPath)) {
    if (Test-Path $IsoPath) {
        Write-Warning "Existing file at $IsoPath is too small to be the real ISO. Deleting it."
        Remove-Item $IsoPath -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Attempting automatic download (this can take a while)..."
    try {
        Get-FileWithProgress -Url $IsoUrl -OutFile $IsoPath
    } catch {
        Write-Warning "Automatic download failed: $($_.Exception.Message)"
        if (Test-Path $IsoPath) { Remove-Item $IsoPath -Force -ErrorAction SilentlyContinue }
    }
}

if (-not (Test-IsoLooksReal $IsoPath)) {
    Write-Warning "No ISO of a plausible size at $IsoPath yet."
    Write-Host "Microsoft's Evaluation Center will open in your browser."
    Write-Host "Download the Windows Server 2022 (Standard or Datacenter) ISO there,"
    Write-Host "then save/move it to exactly this path:`n  $IsoPath"
    Start-Process $EvalPage
    Read-Host "`nPress Enter once the ISO file is saved at $IsoPath"
    if (-not (Test-IsoLooksReal $IsoPath)) {
        Write-Error "File at $IsoPath is still missing or too small. Re-run this script once a genuine ISO (several GB) is in place."
        exit 1
    }
}
Write-Host "ISO looks good (size check passed): $IsoPath"

# ---- Step 4: Create/repair the VM (idempotent - safe to re-run) ----
Write-Step "Creating/updating VirtualBox VM '$VmName'"
$existingVms = & $VBoxManage list vms
$vmExists = $existingVms -match [regex]::Escape("`"$VmName`"")

if (-not $vmExists) {
    $ostypes = (& $VBoxManage list ostypes) -join "`n"
    $OsType = "Windows2019_64"
    if ($ostypes -match "Windows2022_64") { $OsType = "Windows2022_64" }

    $r = Invoke-VBox -CmdArgs @("createvm", "--name", $VmName, "--ostype", $OsType, "--basefolder", $VmBaseDir, "--register")
    if ($r.ExitCode -ne 0) {
        Write-Error "Failed to create the VM: $($r.Text)"
        exit 1
    }
    Write-Host "VM registered."
} else {
    Write-Host "VM '$VmName' already exists - reusing it and making sure it's fully configured."
    $vmState = (& $VBoxManage showvminfo $VmName --machinereadable) -join "`n"
    if ($vmState -match 'VMState="running"') {
        Write-Host "VM is currently running - powering off so storage can be (re)attached."
        Invoke-VBox -CmdArgs @("controlvm", $VmName, "poweroff") | Out-Null
        Start-Sleep -Seconds 3
    }
}

$r = Invoke-VBox -CmdArgs @(
    "modifyvm", $VmName,
    "--memory", $VmMemoryMB, "--cpus", $VmCpus, "--vram", "128",
    "--nic1", "nat", "--boot1", "dvd", "--boot2", "disk", "--boot3", "none", "--boot4", "none",
    "--audio-driver", "none", "--usb-ehci", "on", "--clipboard-mode", "bidirectional"
)
if ($r.ExitCode -ne 0) {
    Write-Error "Failed to configure VM settings (CPU/RAM/network): $($r.Text)"
    exit 1
}

$DiskPath = "$VmBaseDir\$VmName\$VmName.vdi"
if (-not (Test-Path $DiskPath)) {
    $r = Invoke-VBox -CmdArgs @("createmedium", "disk", "--filename", $DiskPath, "--size", ($VmDiskGB * 1024), "--format", "VDI", "--variant", "Standard")
    if ($r.ExitCode -ne 0) {
        Write-Error "Failed to create the virtual disk: $($r.Text)"
        exit 1
    }
}

Add-StorageControllerIfMissing -Name "SATA" -Type sata -Controller IntelAhci
$r = Invoke-VBox -CmdArgs @("storageattach", $VmName, "--storagectl", "SATA", "--port", "0", "--device", "0", "--type", "hdd", "--medium", $DiskPath)
if ($r.ExitCode -ne 0) {
    Write-Error "Failed to attach the OS disk: $($r.Text)"
    exit 1
}

Add-StorageControllerIfMissing -Name "IDE" -Type ide -Controller PIIX4

# Attach the ISO. This is the REAL validity check for the ISO (not a separate
# pre-check) - if it fails, assume the file is bad, delete it, redownload
# once automatically, and try again before giving up.
$attachAttempt = 0
$attached = $false
while (-not $attached -and $attachAttempt -lt 2) {
    $attachAttempt++
    $r = Invoke-VBox -CmdArgs @("storageattach", $VmName, "--storagectl", "IDE", "--port", "0", "--device", "0", "--type", "dvddrive", "--medium", $IsoPath)
    if ($r.ExitCode -eq 0) {
        $attached = $true
        break
    }

    if ($attachAttempt -ge 2) {
        Write-Error "Could not attach a working ISO after $attachAttempt attempt(s): $($r.Text)`nDelete '$IsoPath' manually, download a fresh copy from $EvalPage, and re-run this script."
        exit 1
    }

    Write-Warning "Attaching the ISO failed - the file is likely corrupt or incomplete. Deleting it and redownloading automatically (attempt $attachAttempt of 2)..."
    Remove-Item $IsoPath -Force -ErrorAction SilentlyContinue
    try {
        Get-FileWithProgress -Url $IsoUrl -OutFile $IsoPath
    } catch {
        Write-Error "Redownload failed: $($_.Exception.Message)"
        exit 1
    }
    if (-not (Test-IsoLooksReal $IsoPath)) {
        Write-Error "Redownloaded file is still too small/missing. Check your internet connection or proxy, then re-run this script."
        exit 1
    }
}

Write-Host "VM ready: $VmCpus vCPU / $VmMemoryMB MB RAM / $VmDiskGB GB disk"

# ---- Step 5: Boot it ----
Write-Step "Starting the VM"
$r = Invoke-VBox -CmdArgs @("startvm", $VmName, "--type", "gui")
if ($r.ExitCode -ne 0 -and $r.Text -notmatch "already.*running") {
    Write-Error "Failed to start the VM: $($r.Text)"
    exit 1
}

Write-Host "`nDone. Walk through the Windows installer in the VM window." -ForegroundColor Green
Write-Host "Once setup finishes, take a VirtualBox snapshot immediately (Machine > Take Snapshot)" -ForegroundColor Green
Write-Host "so you always have a clean baseline to revert to before testing installers." -ForegroundColor Green
Write-Host "`nTo later resize for DoseWatch install testing (Recommended tier):" -ForegroundColor Yellow
Write-Host "  `"$VBoxManage`" modifyvm $VmName --memory 16384 --cpus 6   (VM must be powered off)" -ForegroundColor Yellow

} catch {
    Write-Host "`nUnexpected error: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed
    exit 1
}
