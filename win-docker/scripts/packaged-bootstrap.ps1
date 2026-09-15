[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Cli,
  [switch]$Elevated
)

# This script is shipped inside the installed WinColima application. It never
# builds source code: it repairs only the prerequisites of the embedded CLI.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$LogDirectory = Join-Path $env:LOCALAPPDATA 'WinColima\logs'
$LogFile = Join-Path $LogDirectory 'packaged-bootstrap.log'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Refresh-ProcessPath {
  $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $user = [Environment]::GetEnvironmentVariable('Path', 'User')
  $env:Path = "$machine;$user"
}

function Test-Administrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Ensure-WingetPackage([string]$Id, [string]$Label) {
  if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
    throw "Windows Package Manager (winget) is required to install $Label automatically. Install App Installer, then reopen WinColima."
  }
  Write-Step "Installing $Label"
  & winget.exe install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { throw "winget could not install $Label (exit code $LASTEXITCODE)." }
  Refresh-ProcessPath
}

function Ensure-Command([string]$Command, [string]$PackageId, [string]$Label) {
  if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) { Ensure-WingetPackage $PackageId $Label }
  if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) { throw "$Label installed but $Command is unavailable in this Windows session. Reopen WinColima." }
}

function Ensure-DockerBuildx {
  & cmd.exe /d /c 'docker.exe buildx version >NUL 2>NUL'
  if ($LASTEXITCODE -eq 0) { return }
  $architecture = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
  $release = Invoke-RestMethod -UseBasicParsing -Uri 'https://api.github.com/repos/docker/buildx/releases/latest'
  $assetName = "buildx-$($release.tag_name).windows-$architecture.exe"
  $asset = $release.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1
  $checksums = $release.assets | Where-Object { $_.name -eq 'checksums.txt' } | Select-Object -First 1
  if (-not $asset) { throw 'The official Docker Buildx release did not contain a Windows plugin for this architecture.' }
  $cache = Join-Path $env:LOCALAPPDATA 'WinColima\cache'
  $pluginDirectory = Join-Path $env:USERPROFILE '.docker\cli-plugins'
  $plugin = Join-Path $pluginDirectory 'docker-buildx.exe'
  New-Item -ItemType Directory -Force -Path $cache, $pluginDirectory | Out-Null
  $manifest = Join-Path $cache "buildx-$($release.tag_name)-checksums.txt"
  $temporary = Join-Path $cache "$assetName.download"
  Write-Step "Installing verified Docker Buildx $($release.tag_name)"
  if ($checksums) { Invoke-WebRequest -UseBasicParsing -Uri $checksums.browser_download_url -OutFile $manifest }
  $expected = if ($checksums) { ([regex]::Match((Get-Content -Raw -LiteralPath $manifest), "(?m)^([a-fA-F0-9]{64})\s+\*?$([regex]::Escape($assetName))$")).Groups[1].Value } else { '' }
  if (-not $expected -and $asset.digest -match '^sha256:([a-fA-F0-9]{64})$') { $expected = $Matches[1] }
  if (-not $expected) { throw 'The official Docker Buildx release did not provide a SHA-256 checksum for the Windows plugin.' }
  Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $temporary
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $temporary).Hash -ne $expected.ToUpperInvariant()) { Remove-Item -LiteralPath $temporary -Force; throw 'Docker Buildx checksum verification failed.' }
  Move-Item -LiteralPath $temporary -Destination $plugin -Force
  & docker.exe buildx version
  if ($LASTEXITCODE -ne 0) { throw 'Docker Buildx was installed but could not be loaded by Docker CLI.' }
}

function Ensure-DockerCompose {
  & cmd.exe /d /c 'docker.exe compose version >NUL 2>NUL'
  if ($LASTEXITCODE -eq 0) { return }
  $architecture = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
  $release = Invoke-RestMethod -UseBasicParsing -Uri 'https://api.github.com/repos/docker/compose/releases/latest'
  $assetName = "docker-compose-windows-$architecture.exe"
  $asset = $release.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1
  $checksums = $release.assets | Where-Object { $_.name -eq 'checksums.txt' } | Select-Object -First 1
  if (-not $asset) { throw 'The official Docker Compose release did not contain a Windows plugin for this architecture.' }
  $cache = Join-Path $env:LOCALAPPDATA 'WinColima\cache'
  $pluginDirectory = Join-Path $env:USERPROFILE '.docker\cli-plugins'
  $plugin = Join-Path $pluginDirectory 'docker-compose.exe'
  New-Item -ItemType Directory -Force -Path $cache, $pluginDirectory | Out-Null
  $manifest = Join-Path $cache "compose-$($release.tag_name)-checksums.txt"
  $temporary = Join-Path $cache "$assetName.download"
  Write-Step "Installing verified Docker Compose $($release.tag_name)"
  if ($checksums) { Invoke-WebRequest -UseBasicParsing -Uri $checksums.browser_download_url -OutFile $manifest }
  $expected = if ($checksums) { ([regex]::Match((Get-Content -Raw -LiteralPath $manifest), "(?m)^([a-fA-F0-9]{64})\s+\*?$([regex]::Escape($assetName))$")).Groups[1].Value } else { '' }
  if (-not $expected -and $asset.digest -match '^sha256:([a-fA-F0-9]{64})$') { $expected = $Matches[1] }
  if (-not $expected) { throw 'The official Docker Compose release did not provide a SHA-256 checksum for the Windows plugin.' }
  Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $temporary
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $temporary).Hash -ne $expected.ToUpperInvariant()) { Remove-Item -LiteralPath $temporary -Force; throw 'Docker Compose checksum verification failed.' }
  Move-Item -LiteralPath $temporary -Destination $plugin -Force
  & docker.exe compose version
  if ($LASTEXITCODE -ne 0) { throw 'Docker Compose was installed but could not be loaded by Docker CLI.' }
}

function Export-WindowsTrustBundle {
  $cache = Join-Path $env:LOCALAPPDATA 'WinColima\cache'
  $bundle = Join-Path $cache 'node-corporate-roots.pem'
  New-Item -ItemType Directory -Force -Path $cache | Out-Null
  # WSL uses a separate trust store. Copy only public root certificates already
  # trusted by Windows and only for the dedicated WinColima distro.
  $roots = Get-ChildItem Cert:\CurrentUser\Root,Cert:\LocalMachine\Root -ErrorAction SilentlyContinue |
    Group-Object Thumbprint | ForEach-Object { $_.Group[0] }
  if (-not $roots) { return }
  $pem = foreach ($root in $roots) {
    $encoded = [Convert]::ToBase64String($root.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert), [Base64FormattingOptions]::InsertLineBreaks)
    "-----BEGIN CERTIFICATE-----`r`n$encoded`r`n-----END CERTIFICATE-----`r`n"
  }
  [IO.File]::WriteAllText($bundle, ($pem -join "`r`n"), [Text.Encoding]::ASCII)
}

function Enable-WSL2 {
  if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw 'WSL is unavailable on this Windows installation.' }
  & wsl.exe --status 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { return }
  Write-Step 'Enabling WSL2'
  & wsl.exe --install --no-distribution
  if ($LASTEXITCODE -ne 0) { throw "WSL2 installation failed (exit code $LASTEXITCODE)." }
  throw 'WSL2 was enabled. Restart Windows once, then reopen WinColima.'
}

function Test-WinColimaDistro {
  $names = & wsl.exe --list --quiet 2>$null
  return ($names | ForEach-Object { $_.Trim() }) -contains 'wincolima'
}

function Get-Rootfs {
  $architecture = if ([Environment]::Is64BitOperatingSystem -and $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
  $fileName = "ubuntu-noble-wsl-$architecture-wsl.rootfs.tar.gz"
  $baseUrl = 'https://cloud-images.ubuntu.com/wsl/releases/24.04/current'
  $cache = Join-Path $env:LOCALAPPDATA 'WinColima\cache'
  $path = Join-Path $cache $fileName
  New-Item -ItemType Directory -Force -Path $cache | Out-Null
  $sumResponse = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/SHA256SUMS"
  $sumContent = if ($sumResponse.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($sumResponse.Content) } else { [string]$sumResponse.Content }
  $expected = ([regex]::Match($sumContent, "(?m)^([a-fA-F0-9]{64})\s+\*?$([regex]::Escape($fileName))$")).Groups[1].Value
  if (-not $expected) { throw 'The official Ubuntu checksum manifest did not contain the expected WSL rootfs.' }
  $valid = (Test-Path -LiteralPath $path) -and ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash -eq $expected.ToUpperInvariant())
  if (-not $valid) {
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    Write-Step 'Downloading and verifying Ubuntu 24.04 WSL rootfs'
    Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$fileName" -OutFile $path
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash -ne $expected.ToUpperInvariant()) {
      Remove-Item -LiteralPath $path -Force
      throw 'Ubuntu rootfs checksum verification failed.'
    }
  }
  return $path
}

Refresh-ProcessPath
if (-not (Test-Path -LiteralPath $Cli)) { throw "Embedded WinColima control plane is missing: $Cli" }
$wslReady = $false
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
  & wsl.exe --status 2>$null | Out-Null
  $wslReady = $LASTEXITCODE -eq 0
}
$needsAdmin = -not $wslReady -or -not (Get-Command docker.exe -ErrorAction SilentlyContinue) -or -not (Get-Command kubectl.exe -ErrorAction SilentlyContinue)
if ($needsAdmin -and -not (Test-Administrator)) {
  $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Cli `"$Cli`" -Elevated"
  $process = Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList $arguments -Wait -PassThru
  exit $process.ExitCode
}

try {
  New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
  Start-Transcript -Path $LogFile -Append | Out-Null
  Refresh-ProcessPath
  Ensure-Command 'docker.exe' 'Docker.DockerCLI' 'Docker CLI'
	Ensure-DockerBuildx
	Ensure-DockerCompose
  Ensure-Command 'kubectl.exe' 'Kubernetes.kubectl' 'Kubernetes kubectl'
  Enable-WSL2
  Export-WindowsTrustBundle
  if (Test-WinColimaDistro) {
    Write-Step 'Recovering WinColima runtime'
    & $Cli start --activate
  } else {
    $rootfs = Get-Rootfs
    Write-Step 'Creating WinColima runtime'
    & $Cli start --rootfs $rootfs --activate
  }
  if ($LASTEXITCODE -ne 0) { throw "WinColima startup failed (exit code $LASTEXITCODE)." }
} finally {
  try { Stop-Transcript | Out-Null } catch { }
}
