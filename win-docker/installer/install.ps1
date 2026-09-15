[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$MsiPath,
  [string]$RootfsPath,
  [string]$ExpectedRootfsSha256
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $MsiPath)) { throw "MSI was not found: $MsiPath" }
if ($RootfsPath) {
  if (-not (Test-Path -LiteralPath $RootfsPath)) { throw "Rootfs was not found: $RootfsPath" }
  if (-not $ExpectedRootfsSha256) { throw 'An expected SHA-256 is required for a rootfs bundle.' }
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $RootfsPath).Hash
  if ($actual -ne $ExpectedRootfsSha256.ToUpperInvariant()) { throw "Rootfs checksum mismatch. Expected $ExpectedRootfsSha256; got $actual" }
}

$arguments = @('/i', ('"{0}"' -f (Resolve-Path -LiteralPath $MsiPath)), '/qn', '/norestart')
if ($RootfsPath) { $arguments += ('ROOTFS_PATH="{0}"' -f (Resolve-Path -LiteralPath $RootfsPath)) }
Start-Process -FilePath msiexec.exe -ArgumentList $arguments -Wait -NoNewWindow
if ($LASTEXITCODE -ne 0) { throw "MSI installation failed with exit code $LASTEXITCODE" }
