[CmdletBinding()]
param(
  [switch]$Elevated,
  [Parameter(ValueFromRemainingArguments = $true)][string[]]$RemainingArguments
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $PSScriptRoot
$Cli = Join-Path $Root 'dist\wincolima.exe'
$Desktop = Join-Path $Root 'apps\desktop'
$LogDirectory = Join-Path $env:LOCALAPPDATA 'WinColima\logs'
$SetupLog = Join-Path $LogDirectory 'setup.log'

function Write-Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }

function Refresh-ProcessPath {
  $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $user = [Environment]::GetEnvironmentVariable('Path', 'User')
  $env:Path = "$machine;$user"
  $goBin = Join-Path $env:ProgramFiles 'Go\bin'
  if (Test-Path $goBin) { $env:Path = "$goBin;$env:Path" }
}

function Test-Administrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Ensure-WingetPackage([string]$Id, [string]$Label) {
  if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
    throw "Windows Package Manager (winget) is required to install $Label automatically. Install App Installer from Microsoft Store, then run this launcher again."
  }
  Write-Step "Installing $Label"
  & winget.exe install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { throw "winget could not install $Label (exit code $LASTEXITCODE)." }
  Refresh-ProcessPath
}

function Ensure-Command([string]$Command, [string]$PackageId, [string]$Label) {
  if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) { Ensure-WingetPackage $PackageId $Label }
  if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) { throw "$Label installation completed but $Command is not on PATH. Close this window and run the launcher again." }
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

function Repair-NodeCertificateEnvironment {
  # Keep a contributor's valid npm trust configuration intact. A stale process
  # setting must not make a public source build noisy or fail before npm starts.
  $extraCa = [Environment]::GetEnvironmentVariable('NODE_EXTRA_CA_CERTS', 'Process')
  if ($extraCa -and -not (Test-Path -LiteralPath $extraCa)) {
    Remove-Item Env:NODE_EXTRA_CA_CERTS -ErrorAction SilentlyContinue
  }
}

function Repair-ElectronBinary {
  $electronDir = Join-Path $Desktop 'node_modules\electron'
  $electronExe = Join-Path $electronDir 'dist\electron.exe'
  if (Test-Path -LiteralPath $electronExe) { return }

  $package = Get-Content -Raw (Join-Path $electronDir 'package.json') | ConvertFrom-Json
  $architecture = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
  $assetName = "electron-v$($package.version)-win32-$architecture.zip"
  $checksums = Get-Content -Raw (Join-Path $electronDir 'checksums.json') | ConvertFrom-Json
  $expected = $checksums.PSObject.Properties[$assetName].Value
  if (-not $expected) { throw "Electron checksum metadata is missing $assetName." }

  $cache = Join-Path $env:LOCALAPPDATA 'electron\Cache'
  $archive = Get-ChildItem -Path $cache -Recurse -File -Filter $assetName -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $archive -or (Get-FileHash -Algorithm SHA256 -LiteralPath $archive.FullName).Hash -ne $expected.ToUpperInvariant()) {
    Write-Step 'Downloading the verified Electron desktop runtime'
    Push-Location $electronDir
    try {
      $env:force_no_cache = 'true'
      & node.exe install.js
      if ($LASTEXITCODE -ne 0) { throw "Electron download failed (exit code $LASTEXITCODE)." }
    } finally {
      Remove-Item Env:force_no_cache -ErrorAction SilentlyContinue
      Pop-Location
    }
    $archive = Get-ChildItem -Path $cache -Recurse -File -Filter $assetName -ErrorAction SilentlyContinue |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1
  }
  if (-not $archive -or (Get-FileHash -Algorithm SHA256 -LiteralPath $archive.FullName).Hash -ne $expected.ToUpperInvariant()) { throw 'Electron archive is missing or failed checksum verification.' }

  Write-Step 'Repairing the Electron desktop runtime'
  Expand-Archive -LiteralPath $archive.FullName -DestinationPath (Join-Path $electronDir 'dist') -Force
  Set-Content -LiteralPath (Join-Path $electronDir 'path.txt') -Value 'electron.exe' -NoNewline
  if (-not (Test-Path -LiteralPath $electronExe)) { throw 'Electron archive extraction did not create electron.exe.' }
}

function Test-WinColimaDistro {
  $names = & wsl.exe --list --quiet 2>$null
  return ($names | ForEach-Object { $_.Trim() }) -contains 'wincolima'
}

function Enable-WSL2 {
  if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw 'WSL is unavailable on this Windows installation.' }
  & wsl.exe --status 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { return }
  Write-Step 'Enabling WSL2 (Windows may require one restart)'
  & wsl.exe --install --no-distribution
  if ($LASTEXITCODE -ne 0) { throw "WSL2 installation failed (exit code $LASTEXITCODE)." }
  throw 'WSL2 was enabled. Restart Windows once, then double-click launch-wincolima.bat again.'
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
  $valid = (Test-Path $path) -and ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash -eq $expected.ToUpperInvariant())
  if (-not $valid) {
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    Write-Step 'Downloading and verifying Ubuntu 24.04 WSL rootfs (about 340 MB)'
    Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$fileName" -OutFile $path
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
    if ($actual -ne $expected.ToUpperInvariant()) { Remove-Item -LiteralPath $path -Force; throw 'Ubuntu rootfs checksum verification failed.' }
  }
  return $path
}

Refresh-ProcessPath
$wslReady = $false
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
  & wsl.exe --status 2>$null | Out-Null
  $wslReady = $LASTEXITCODE -eq 0
}
$missingPrerequisite = -not (Get-Command go.exe -ErrorAction SilentlyContinue) -or
  -not (Get-Command node.exe -ErrorAction SilentlyContinue) -or
  -not (Get-Command npm.cmd -ErrorAction SilentlyContinue) -or
  -not (Get-Command docker.exe -ErrorAction SilentlyContinue)

# Normal start/import is per-user. Elevation is only needed when Windows must
# install a prerequisite or enable WSL for the first time.
if (-not (Test-Administrator) -and ($missingPrerequisite -or -not $wslReady)) {
  Write-Host 'Requesting administrator permission for prerequisite or WSL installation...' -ForegroundColor Yellow
  $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated"
  $process = Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList $arguments -Wait -PassThru
  exit $process.ExitCode
}

try {
  New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
  Start-Transcript -Path $SetupLog -Append | Out-Null
  Refresh-ProcessPath
  Ensure-Command 'go.exe' 'GoLang.Go' 'Go 1.23+'
  Ensure-Command 'node.exe' 'OpenJS.NodeJS.LTS' 'Node.js LTS'
  Ensure-Command 'npm.cmd' 'OpenJS.NodeJS.LTS' 'Node.js npm'
  Ensure-Command 'docker.exe' 'Docker.DockerCLI' 'Docker CLI'
	Ensure-DockerBuildx
	Ensure-DockerCompose
  Ensure-Command 'kubectl.exe' 'Kubernetes.kubectl' 'Kubernetes kubectl'
  Repair-NodeCertificateEnvironment

  Write-Step 'Building WinColima control plane'
  Push-Location $Root
  try {
    & go.exe mod tidy
    if ($LASTEXITCODE -ne 0) { throw "go mod tidy failed (exit code $LASTEXITCODE)." }
    New-Item -ItemType Directory -Force -Path (Split-Path $Cli) | Out-Null
    & go.exe build -trimpath -o $Cli '.\cmd\wincolima'
    if ($LASTEXITCODE -ne 0) { throw "WinColima CLI build failed (exit code $LASTEXITCODE)." }
  } finally { Pop-Location }

  Write-Step 'Installing desktop dependencies and building the dashboard'
  Push-Location $Desktop
  try {
    & npm.cmd approve-scripts electron electron-winstaller esbuild
    if ($LASTEXITCODE -ne 0) { throw "npm script approval failed (exit code $LASTEXITCODE)." }
    & npm.cmd install --no-audit --no-fund --fetch-timeout 30000 --fetch-retries 2 --progress=false
    if ($LASTEXITCODE -ne 0) { throw "npm install failed (exit code $LASTEXITCODE)." }
    & npm.cmd rebuild electron electron-winstaller esbuild --foreground-scripts
    if ($LASTEXITCODE -ne 0) { throw "npm dependency rebuild failed (exit code $LASTEXITCODE)." }
    Repair-ElectronBinary
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "desktop build failed (exit code $LASTEXITCODE)." }
  } finally { Pop-Location }

  Enable-WSL2
  if (-not (Test-WinColimaDistro)) {
    $rootfs = Get-Rootfs
    Write-Step 'Creating WinColima and installing Docker Engine'
    & $Cli start --rootfs $rootfs --activate
  } else {
    Write-Step 'Starting WinColima'
    & $Cli start --activate
  }
  if ($LASTEXITCODE -ne 0) { throw "WinColima startup failed (exit code $LASTEXITCODE)." }

  Write-Step 'Opening WinColima'
  # Electron runs in a child process. Pass the freshly built CLI explicitly so
  # it never depends on a system PATH entry or a separately installed package.
  $env:WINCOLIMA_BIN = $Cli
  $desktopProcess = Start-Process -FilePath (Join-Path $Desktop 'node_modules\electron\dist\electron.exe') -WorkingDirectory $Desktop -ArgumentList '.' -PassThru
  Start-Sleep -Seconds 5
  if ($desktopProcess.HasExited) { throw "The WinColima desktop process exited during launch. Review $LogDirectory\desktop.log." }
} catch {
  Write-Host "`nWinColima setup failed: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
} finally {
  try { Stop-Transcript | Out-Null } catch { }
}
