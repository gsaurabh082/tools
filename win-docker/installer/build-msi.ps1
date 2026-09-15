[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$PublishDir,
  [Parameter(Mandatory = $true)][string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$wix = Get-Command wix -ErrorAction SilentlyContinue
if (-not $wix) { throw 'WiX Toolset v4 is required. Install it from https://wixtoolset.org/ before packaging the MSI.' }
if (-not (Test-Path (Join-Path $PublishDir 'wincolima.exe'))) { throw "wincolima.exe was not found in $PublishDir" }
if (-not (Test-Path (Join-Path $PublishDir 'WinDock.exe'))) { throw "WinDock.exe was not found in $PublishDir" }

$source = Join-Path $PSScriptRoot 'wix\Product.wxs'
& $wix.Path build $source "-dPublishDir=$PublishDir" '-arch' 'x64' '-o' $OutputPath
if ($LASTEXITCODE -ne 0) { throw 'WiX build failed.' }
