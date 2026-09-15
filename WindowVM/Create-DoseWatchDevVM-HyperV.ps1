<#
.SYNOPSIS
    One-click bootstrap for a minimum-footprint Windows dev/QA VM using
    Hyper-V (native to Windows Pro/Enterprise/Education), sized for
    GE HealthCare DoseWatch install/testing.

.NOTES
    Run via Run-DoseWatchDevVM-HyperV.bat (double-click it), or manually
    as Administrator:
      1. Right-click PowerShell -> "Run as Administrator"
      2. cd to the folder containing this script
      3. Set-ExecutionPolicy -Scope Process Bypass -Force
      4. .\Create-DoseWatchDevVM-HyperV.ps1

    What it does, every time you run it:
      1. Checks Hyper-V is available and enabled (enables it + tells you to
         reboot if it isn't yet - Windows Home doesn't support this at all).
      2. Gets a Windows Server 2022 evaluation ISO fully automatically -
         tries Microsoft's Evaluation Center first, then an Archive.org
         mirror as backup, and verifies the actual Windows edition inside
         the downloaded file via DISM before trusting it (not just file
         size). No manual browser step, ever.
      3. Creates the VM (Generation 1 - legacy BIOS boot, which is what
         actually works reliably here; Gen 2/UEFI DVD boot failed
         repeatedly on this host even with verified-good media).
      4. Attaches the ISO plus a small auto-generated "unattend" disk that
         tells Windows Setup which edition to install (Desktop
         Experience/GUI, not Server Core), the admin password, and to skip
         every EULA/OOBE screen - so install is fully hands-off.
      5. Boots the VM and opens the Hyper-V console window for it. Setup
         then runs on its own to a login screen - no clicks required.

    Uses native Hyper-V PowerShell cmdlets throughout (New-VM, Set-VM*,
    Start-VM, etc.) instead of shelling out to a separate CLI tool - these
    throw real, structured PowerShell errors, so there's no CLI-text-parsing
    fragility like with third-party hypervisor tools.

    Safe to re-run any time - every step is idempotent.
#>

#Requires -RunAsAdministrator

param(
    # Ask   = prompt interactively if the VM already exists (default)
    # Reuse = always keep the existing VM/disk as-is, just start it
    # Clean = always wipe and reinstall from scratch, no prompt
    [ValidateSet("Ask", "Reuse", "Clean")]
    [string]$Mode = "Ask"
)

$ErrorActionPreference = "Stop"

# ---- Configurable settings ----
$VmName        = "DoseWatch-DevQA"
$VmBaseDir     = "$env:USERPROFILE\VMs\HyperV"
$IsoDir        = "$env:USERPROFILE\VMs\ISOs"
# New filename deliberately (was WindowsServer2022_Eval.iso): the old fwlink
# ID (2195174) turned out to actually resolve to the Windows Server 2016
# eval ISO, not 2022 - confirmed by mounting it and seeing a "NanoServer"
# folder and 2016 file dates. Using a new filename here means this script
# won't mistake the old, wrong file for a valid cached download.
$IsoPath       = "$IsoDir\WindowsServer2022_Eval_v2.iso"
# Two independent sources are tried automatically, in order - no manual
# browser step at all. Each download is verified by actually mounting the
# ISO and checking its Windows edition via DISM, not just its file size
# (a wrong-but-plausibly-sized file is exactly how we got burned before).
$IsoSources = @(
    @{ Name = "Microsoft Evaluation Center"; Url = "https://go.microsoft.com/fwlink/p/?LinkID=2195280&clcid=0x409&culture=en-us&country=US" },
    @{ Name = "Archive.org mirror (Microsoft-sourced, verified 4.7GB en-us build)"; Url = "https://archive.org/download/windows-server-2022-evaluation/SERVER_EVAL_x64FRE_en-us.iso" }
)
$IsoMinBytes    = 2GB   # real eval ISOs run 4-6GB; anything under 2GB is not the real file
$AdminPassword  = "DoseWatch#2026!"   # local Administrator password after unattended install
$UnattendVhd    = Join-Path $VmBaseDir "$VmName-unattend.vhd"
$VmMemoryMB    = 8192
$VmCpus        = 4
$VmDiskGB      = 100
# Dedicated NAT switch settings - NOT "Default Switch". Default Switch's
# DHCP failed outright on this host (guest got a 169.254.x.x APIPA address,
# no lease at all), most likely because corporate policy disables ICS,
# which Default Switch's NAT depends on internally. New-NetNat is a
# separate, more modern mechanism (also used by Docker Desktop/WSL2) that
# doesn't rely on ICS. The guest gets a STATIC IP via the unattend file
# instead of DHCP, since our own Internal switch has no DHCP server either.
$NatSwitchName  = "DoseWatchNAT"
$NatRuleName    = "DoseWatchNAT-Rule"
$NatHostIp      = "192.168.77.1"
$NatSubnet      = "192.168.77.0/24"
$GuestIpCidr    = "192.168.77.10/24"
$GuestGateway   = "192.168.77.1"
$GuestDns       = @("8.8.8.8", "1.1.1.1")
$ScriptVersion = "2026-08-06-hyperv-v10"

Write-Host "Create-DoseWatchDevVM-HyperV.ps1 - version $ScriptVersion" -ForegroundColor DarkGray

function Write-Step($msg) { Write-Host "`n== $msg ==" -ForegroundColor Cyan }

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
    param([Parameter(Mandatory)][string]$Path)
    (Test-Path $Path) -and ((Get-Item $Path).Length -ge $IsoMinBytes)
}

function Test-IsoIsServer2022 {
    # Authoritative check: mount the ISO for real and ask DISM what Windows
    # edition is actually inside it. This is what should have been used from
    # the start instead of trusting file size or a third-party tool's
    # medium-type guess - it directly answers "is this actually Windows
    # Server 2022?" instead of "is this plausibly a big enough file?".
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-IsoLooksReal $Path)) { return $false }

    $mounted = $false
    try {
        Mount-DiskImage -ImagePath $Path -ErrorAction Stop | Out-Null
        $mounted = $true
        Start-Sleep -Seconds 2
        $vol = Get-DiskImage -ImagePath $Path | Get-Volume -ErrorAction Stop
        $drive = "$($vol.DriveLetter):"

        if (Test-Path (Join-Path $drive "NanoServer")) {
            Write-Warning "Mounted ISO contains a 'NanoServer' folder - that marks it as Windows Server 2016 media, not 2022."
            return $false
        }

        $wimPath = Join-Path $drive "sources\install.wim"
        $esdPath = Join-Path $drive "sources\install.esd"
        $imagePath = if (Test-Path $wimPath) { $wimPath } elseif (Test-Path $esdPath) { $esdPath } else { $null }
        if (-not $imagePath) {
            Write-Warning "Mounted ISO has no sources\install.wim or install.esd - not valid Windows install media."
            return $false
        }

        $images = Get-WindowsImage -ImagePath $imagePath -ErrorAction Stop
        $match = $images | Where-Object { $_.ImageName -match "2022" }
        if (-not $match) {
            Write-Warning "Mounted ISO's edition name(s) don't mention 2022: $($images.ImageName -join ', ')"
            return $false
        }

        Write-Host "Verified genuine media: $($match[0].ImageName)" -ForegroundColor Green
        return $true
    } catch {
        Write-Warning "Could not verify ISO contents: $($_.Exception.Message)"
        return $false
    } finally {
        if ($mounted) { Dismount-DiskImage -ImagePath $Path -ErrorAction SilentlyContinue | Out-Null }
    }
}

function Get-DesktopExperienceImageIndex {
    # Mounts the ISO and asks DISM which image index is the GUI ("Desktop
    # Experience") edition of Standard - falls back to any non-Core image,
    # then to index 1, so this never fails outright.
    param([Parameter(Mandatory)][string]$Path)
    $mounted = $false
    try {
        Mount-DiskImage -ImagePath $Path -ErrorAction Stop | Out-Null
        $mounted = $true
        Start-Sleep -Seconds 2
        $vol = Get-DiskImage -ImagePath $Path | Get-Volume -ErrorAction Stop
        $drive = "$($vol.DriveLetter):"
        $wimPath = Join-Path $drive "sources\install.wim"
        if (-not (Test-Path $wimPath)) { $wimPath = Join-Path $drive "sources\install.esd" }

        $images = Get-WindowsImage -ImagePath $wimPath -ErrorAction Stop
        $best = $images | Where-Object { $_.ImageName -match "SERVERSTANDARD" -and $_.ImageName -notmatch "Core" } | Select-Object -First 1
        if (-not $best) { $best = $images | Where-Object { $_.ImageName -notmatch "Core" } | Select-Object -First 1 }
        if (-not $best) { $best = $images | Select-Object -First 1 }

        Write-Host "Selected install image: [$($best.ImageIndex)] $($best.ImageName)" -ForegroundColor Green
        return [int]$best.ImageIndex
    } catch {
        Write-Warning "Could not enumerate images, defaulting to index 1: $($_.Exception.Message)"
        return 1
    } finally {
        if ($mounted) { Dismount-DiskImage -ImagePath $Path -ErrorAction SilentlyContinue | Out-Null }
    }
}

function New-UnattendVhd {
    # Builds a tiny VHD containing autounattend.xml at its root and attaches
    # it as a secondary disk. Windows Setup automatically scans all attached
    # media (not just removable) for this file, so this gives a fully
    # unattended install - correct edition, no EULA/OOBE screens, known
    # admin password - with zero manual clicks, regardless of what the ISO
    # itself would otherwise do by default.
    param(
        [Parameter(Mandatory)][string]$VhdPath,
        [Parameter(Mandatory)][int]$ImageIndex,
        [Parameter(Mandatory)][string]$ComputerName,
        [Parameter(Mandatory)][string]$Password,
        [Parameter(Mandatory)][string]$IpCidr,
        [Parameter(Mandatory)][string]$Gateway,
        [Parameter(Mandatory)][string[]]$DnsServers
    )

    $dnsLines = @()
    for ($i = 0; $i -lt $DnsServers.Count; $i++) {
        $dnsLines += "<IpAddress wcm:action=`"add`" wcm:keyValue=`"$($i + 1)`">$($DnsServers[$i])</IpAddress>"
    }
    $dnsEntriesXml = $dnsLines -join "`n        "

    $xml = @"
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-International-Core-WinPE" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <SetupUILanguage><UILanguage>en-US</UILanguage></SetupUILanguage>
      <InputLocale>en-US</InputLocale>
      <SystemLocale>en-US</SystemLocale>
      <UILanguage>en-US</UILanguage>
      <UserLocale>en-US</UserLocale>
    </component>
    <component name="Microsoft-Windows-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <DiskConfiguration>
        <Disk wcm:action="add">
          <CreatePartitions>
            <CreatePartition wcm:action="add">
              <Order>1</Order>
              <Type>Primary</Type>
              <Extend>true</Extend>
            </CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add">
              <Order>1</Order>
              <PartitionID>1</PartitionID>
              <Format>NTFS</Format>
              <Label>OS</Label>
              <Letter>C</Letter>
              <Active>true</Active>
            </ModifyPartition>
          </ModifyPartitions>
          <DiskID>0</DiskID>
          <WillWipeDisk>true</WillWipeDisk>
        </Disk>
      </DiskConfiguration>
      <ImageInstall>
        <OSImage>
          <InstallFrom>
            <MetaData wcm:action="add">
              <Key>/IMAGE/INDEX</Key>
              <Value>$ImageIndex</Value>
            </MetaData>
          </InstallFrom>
          <InstallTo>
            <DiskID>0</DiskID>
            <PartitionID>1</PartitionID>
          </InstallTo>
        </OSImage>
      </ImageInstall>
      <UserData>
        <AcceptEula>true</AcceptEula>
      </UserData>
    </component>
  </settings>
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <ComputerName>$ComputerName</ComputerName>
    </component>
    <component name="Microsoft-Windows-TCPIP" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <Interfaces>
        <Interface wcm:action="add">
          <Identifier>Ethernet</Identifier>
          <Ipv4Settings>
            <DhcpEnabled>false</DhcpEnabled>
          </Ipv4Settings>
          <UnicastIpAddresses>
            <IpAddress wcm:action="add" wcm:keyValue="1">$IpCidr</IpAddress>
          </UnicastIpAddresses>
          <Routes>
            <Route wcm:action="add">
              <Identifier>1</Identifier>
              <Prefix>0.0.0.0/0</Prefix>
              <NextHopAddress>$Gateway</NextHopAddress>
            </Route>
          </Routes>
        </Interface>
      </Interfaces>
    </component>
    <component name="Microsoft-Windows-DNS-Client" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <Interfaces>
        <Interface wcm:action="add">
          <Identifier>Ethernet</Identifier>
          <DNSServerSearchOrder>
            $dnsEntriesXml
          </DNSServerSearchOrder>
        </Interface>
      </Interfaces>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <UserAccounts>
        <AdministratorPassword>
          <Value>$Password</Value>
          <PlainText>true</PlainText>
        </AdministratorPassword>
      </UserAccounts>
      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideOEMRegistrationScreen>true</HideOEMRegistrationScreen>
        <HideOnlineAccountScreens>true</HideOnlineAccountScreens>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <NetworkLocation>Work</NetworkLocation>
        <ProtectYourPC>3</ProtectYourPC>
        <SkipMachineOOBE>true</SkipMachineOOBE>
        <SkipUserOOBE>true</SkipUserOOBE>
      </OOBE>
      <TimeZone>UTC</TimeZone>
    </component>
  </settings>
</unattend>
"@

    if (Test-Path $VhdPath) { Remove-Item $VhdPath -Force -ErrorAction SilentlyContinue }
    New-VHD -Path $VhdPath -SizeBytes 50MB -Fixed | Out-Null
    $disk = Mount-VHD -Path $VhdPath -PassThru | Get-Disk
    try {
        Initialize-Disk -Number $disk.Number -PartitionStyle MBR -Confirm:$false
        $partition = New-Partition -DiskNumber $disk.Number -UseMaximumSize -IsActive -AssignDriveLetter
        Format-Volume -Partition $partition -FileSystem FAT32 -Confirm:$false -Force | Out-Null
        $driveLetter = ($partition | Get-Volume).DriveLetter
        Set-Content -Path "${driveLetter}:\autounattend.xml" -Value $xml -Encoding UTF8 -NoNewline
    } finally {
        Dismount-VHD -Path $VhdPath
    }
}

function Send-VMKeystroke {
    # Windows install media shows "Press any key to boot from CD or DVD..."
    # with a short timeout - if nothing presses a key in time, the BIOS
    # silently falls through to the next boot device (the hard disk),
    # which is exactly what caused Setup to be skipped entirely on a prior
    # run. This sends a real keystroke into the VM's virtual keyboard via
    # Hyper-V's native WMI interface - no UI automation/window focus
    # needed, works even though nothing is watching the console.
    param([Parameter(Mandatory)][string]$VMName, [string]$Text = " ")
    try {
        $vmCim = Get-CimInstance -Namespace "root\virtualization\v2" -ClassName Msvm_ComputerSystem -Filter "ElementName='$VMName'"
        $kbd = Get-CimAssociatedInstance -InputObject $vmCim -ResultClassName Msvm_Keyboard
        Invoke-CimMethod -InputObject $kbd -MethodName TypeText -Arguments @{ asciiText = $Text } | Out-Null
    } catch {
        Write-Warning "Could not send keystroke to VM console: $($_.Exception.Message)"
    }
}

function Initialize-VMSwitch {
    # Deliberately NOT "Default Switch": its DHCP failed outright on this
    # host (guest got a 169.254.x.x APIPA address), most likely because
    # corporate policy disables ICS, which Default Switch's NAT depends on.
    # This builds our own Internal switch + WinNat rule instead - a
    # completely separate mechanism from ICS. The guest gets a static IP
    # via the unattend file rather than DHCP, since this switch has no
    # DHCP server of its own either.
    Write-Host "Setting up dedicated NAT switch '$NatSwitchName' ($NatSubnet, unrelated to ICS/Default Switch)..."

    if (-not (Get-VMSwitch -Name $NatSwitchName -ErrorAction SilentlyContinue)) {
        New-VMSwitch -Name $NatSwitchName -SwitchType Internal | Out-Null
        Start-Sleep -Seconds 3
    }

    $adapter = Get-NetAdapter | Where-Object { $_.Name -like "*$NatSwitchName*" } | Select-Object -First 1
    if (-not $adapter) {
        throw "Could not find the host-side network adapter for switch '$NatSwitchName' after creating it."
    }

    $hasIp = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -eq $NatHostIp }
    if (-not $hasIp) {
        New-NetIPAddress -IPAddress $NatHostIp -PrefixLength 24 -InterfaceIndex $adapter.ifIndex -ErrorAction Stop | Out-Null
    }

    if (-not (Get-NetNat -Name $NatRuleName -ErrorAction SilentlyContinue)) {
        New-NetNat -Name $NatRuleName -InternalIPInterfaceAddressPrefix $NatSubnet -ErrorAction Stop | Out-Null
    }

    Write-Host "NAT switch ready: host $NatHostIp, guest will use static IP $GuestIpCidr"
    return $NatSwitchName
}

try {

# ---- Step 1: Hyper-V availability ----
Write-Step "Checking Hyper-V"

$edition = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -Name EditionID -ErrorAction SilentlyContinue).EditionID
if ($edition -match "^Core") {
    Write-Error "This is Windows Home edition ('$edition') - Hyper-V isn't supported on Home. Use VirtualBox or VMware instead."
    exit 1
}

$feature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -ErrorAction SilentlyContinue
if ($feature -and $feature.State -ne "Enabled") {
    Write-Host "Enabling Hyper-V - this requires a restart before it can be used."
    Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All -NoRestart | Out-Null
    Write-Warning "Hyper-V has been enabled but needs a RESTART to activate."
    Write-Host "Restart your computer, then run this script again to continue." -ForegroundColor Yellow
    exit 0
}

if (-not (Get-Module -ListAvailable -Name Hyper-V)) {
    Write-Error "Hyper-V PowerShell module not found. If you just restarted, open a fresh PowerShell window and retry. Otherwise this device/edition may not support Hyper-V."
    exit 1
}
Import-Module Hyper-V -ErrorAction Stop
Write-Host "Hyper-V OK."

# ---- Step 2: Folders ----
Write-Step "Preparing folders"
New-Item -ItemType Directory -Force -Path $VmBaseDir | Out-Null
New-Item -ItemType Directory -Force -Path $IsoDir | Out-Null

# ---- Step 3: Windows ISO - fully automatic, no manual steps ----
Write-Step "Getting a verified Windows Server 2022 evaluation ISO"

if (Test-IsoIsServer2022 $IsoPath) {
    Write-Host "Existing file already verified as genuine Windows Server 2022 media: $IsoPath"
} else {
    if (Test-Path $IsoPath) {
        Write-Warning "Existing file didn't verify as genuine Windows Server 2022 media. Deleting it."
        Remove-Item $IsoPath -Force -ErrorAction SilentlyContinue
    }

    $verified = $false
    foreach ($src in $IsoSources) {
        if ($verified) { break }

        Write-Host "`nTrying source: $($src.Name)"
        try {
            Get-FileWithProgress -Url $src.Url -OutFile $IsoPath
        } catch {
            Write-Warning "Download from $($src.Name) failed: $($_.Exception.Message)"
            if (Test-Path $IsoPath) { Remove-Item $IsoPath -Force -ErrorAction SilentlyContinue }
            continue
        }

        if (Test-IsoIsServer2022 $IsoPath) {
            $verified = $true
        } else {
            Write-Warning "$($src.Name) did not provide genuine Windows Server 2022 media. Discarding and trying the next source."
            Remove-Item $IsoPath -Force -ErrorAction SilentlyContinue
        }
    }

    if (-not $verified) {
        Write-Error "Could not obtain a verified Windows Server 2022 ISO automatically from any source. Check your internet connection/proxy and re-run this script."
        exit 1
    }
}
Write-Host "ISO verified: $IsoPath"

# ---- Step 4: Networking ----
Write-Step "Setting up networking"
$switchName = Initialize-VMSwitch
Write-Host "Using virtual switch: $switchName"

# ---- Step 5: Create, or reuse, the VM ----
Write-Step "Checking for existing VM '$VmName'"
$vhdPath = Join-Path $VmBaseDir "$VmName.vhdx"
$vm = Get-VM -Name $VmName -ErrorAction SilentlyContinue

$doClean = $true
if ($vm) {
    switch ($Mode) {
        "Reuse" { $doClean = $false }
        "Clean" { $doClean = $true }
        default {
            Write-Host "`nVM '$VmName' already exists (state: $($vm.State))." -ForegroundColor Yellow
            $answer = Read-Host "Reuse it as-is, or wipe it for a Clean reinstall? [R]euse / [C]lean (default: R)"
            $doClean = ($answer -match '^[Cc]')
        }
    }
}

if ($doClean) {
    Write-Step "Building Hyper-V VM '$VmName' (clean install)"
    if ($vm) {
        Write-Host "Removing existing VM '$VmName' to rebuild it clean..."
        if ($vm.State -ne "Off") { Stop-VM -Name $VmName -TurnOff -Force }
        Remove-VM -Name $VmName -Force
    }
    if (Test-Path $vhdPath) { Remove-Item $vhdPath -Force -ErrorAction SilentlyContinue }
    if (Test-Path $UnattendVhd) { Remove-Item $UnattendVhd -Force -ErrorAction SilentlyContinue }

    New-VM -Name $VmName `
        -MemoryStartupBytes ([int64]$VmMemoryMB * 1MB) `
        -Generation 1 `
        -NewVHDPath $vhdPath `
        -NewVHDSizeBytes ([int64]$VmDiskGB * 1GB) `
        -SwitchName $switchName `
        -Path $VmBaseDir | Out-Null
    Write-Host "VM created (Generation 1)."

    Set-VMProcessor -VMName $VmName -Count $VmCpus
    Set-VMMemory -VMName $VmName -StartupBytes ([int64]$VmMemoryMB * 1MB)

    # Explicit IDE slots so the OS disk, the unattend-answer disk, and the
    # DVD never collide: Controller 0 = OS disk (loc 0) + unattend disk
    # (loc 1); Controller 1 = DVD (loc 0). New-VM already creates a default
    # empty DVD drive at IDE 1:0 for Generation 1 VMs, so reuse that one
    # instead of adding a second drive on top of it (which errors with
    # "no available locations").
    $dvd = Get-VMDvdDrive -VMName $VmName -ControllerNumber 1 -ControllerLocation 0 -ErrorAction SilentlyContinue
    if ($dvd) {
        Set-VMDvdDrive -VMName $VmName -ControllerNumber 1 -ControllerLocation 0 -Path $IsoPath
    } else {
        Add-VMDvdDrive -VMName $VmName -ControllerNumber 1 -ControllerLocation 0 -Path $IsoPath
    }

    Write-Step "Building unattended-install answer file"
    $imageIndex = Get-DesktopExperienceImageIndex -Path $IsoPath
    New-UnattendVhd -VhdPath $UnattendVhd -ImageIndex $imageIndex -ComputerName $VmName -Password $AdminPassword `
        -IpCidr $GuestIpCidr -Gateway $GuestGateway -DnsServers $GuestDns
    Add-VMHardDiskDrive -VMName $VmName -ControllerType IDE -ControllerNumber 0 -ControllerLocation 1 -Path $UnattendVhd

    # CD first: this run needs to boot the installer.
    Set-VMBios -VMName $VmName -StartupOrder @("CD", "IDE", "LegacyNetworkAdapter", "Floppy")
    Write-Host "VM ready: $VmCpus vCPU / $VmMemoryMB MB RAM / $VmDiskGB GB disk"
} else {
    Write-Step "Reusing existing VM '$VmName' as-is"
    Set-VMProcessor -VMName $VmName -Count $VmCpus
    Set-VMMemory -VMName $VmName -StartupBytes ([int64]$VmMemoryMB * 1MB)
    # IDE (hard disk) first: this run must NOT boot the installer again -
    # doing so would hit the same autounattend.xml and wipe the disk.
    Set-VMBios -VMName $VmName -StartupOrder @("IDE", "CD", "LegacyNetworkAdapter", "Floppy")
    Write-Host "Keeping the existing install untouched - booting straight to it."
}

# ---- Step 6: Boot it ----
Write-Step "Starting the VM"
if ($vm -and $vm.State -eq "Running") {
    Write-Host "VM is already running."
} else {
    Start-VM -Name $VmName
}
Start-Process "vmconnect.exe" -ArgumentList "localhost", "`"$VmName`""

if ($doClean) {
    # Dismiss the "Press any key to boot from CD or DVD" prompt
    # automatically - sent a few times over a couple seconds to reliably
    # land inside its short timeout window regardless of exact boot timing.
    Write-Host "Sending boot keystrokes to confirm CD boot..."
    Start-Sleep -Seconds 3
    1..4 | ForEach-Object {
        Send-VMKeystroke -VMName $VmName -Text " "
        Start-Sleep -Milliseconds 700
    }
}

Write-Host "`nDone. The Hyper-V console window should be opening now." -ForegroundColor Green
if ($doClean) {
    Write-Host "Windows Setup is now fully unattended - it will partition the disk," -ForegroundColor Green
    Write-Host "install the Desktop Experience (GUI) edition, and boot to a login" -ForegroundColor Green
    Write-Host "screen on its own. This typically takes 10-20 minutes - no clicks needed." -ForegroundColor Green
    Write-Host "`nLogin once it reaches the sign-in screen:" -ForegroundColor Cyan
    Write-Host "  Username: Administrator" -ForegroundColor Cyan
    Write-Host "  Password: $AdminPassword" -ForegroundColor Cyan
    Write-Host "`nNetworking: static IP $GuestIpCidr via dedicated NAT switch '$NatSwitchName'" -ForegroundColor Cyan
    Write-Host "(not DHCP/Default Switch, which failed on this host). Internet should work" -ForegroundColor Cyan
    Write-Host "out of the box; if not, it likely means VPN/corporate policy is blocking the" -ForegroundColor Cyan
    Write-Host "$NatSubnet range too - disconnect VPN and retest to confirm." -ForegroundColor Cyan
    Write-Host "`nAfter first login, take a checkpoint immediately" -ForegroundColor Green
    Write-Host "(Hyper-V Manager > right-click the VM > Checkpoint) as your clean baseline." -ForegroundColor Green
} else {
    Write-Host "Booting your existing install - no reinstall, no data touched." -ForegroundColor Green
}
Write-Host "`nTo later resize for DoseWatch install testing (Recommended tier), power the VM off first:" -ForegroundColor Yellow
Write-Host "  Set-VMProcessor -VMName '$VmName' -Count 6; Set-VMMemory -VMName '$VmName' -StartupBytes 16GB" -ForegroundColor Yellow
Write-Host "`nTip: skip this prompt next time with .\$($MyInvocation.MyCommand.Name) -Mode Reuse (or -Mode Clean)" -ForegroundColor DarkGray

} catch {
    Write-Host "`nUnexpected error: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed
    exit 1
}
