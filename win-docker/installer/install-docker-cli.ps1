[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$DockerCliZip,
  [Parameter(Mandatory = $true)][string]$ExpectedSha256,
  [Parameter(Mandatory = $true)][string]$Destination
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $DockerCliZip)) { throw "Docker CLI archive not found: $DockerCliZip" }
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $DockerCliZip).Hash
if ($actual -ne $ExpectedSha256.ToUpperInvariant()) { throw "Docker CLI checksum mismatch. Expected $ExpectedSha256; got $actual" }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Expand-Archive -LiteralPath $DockerCliZip -DestinationPath $Destination -Force
Write-Host "Docker CLI extracted to $Destination. Add its docker directory to PATH through your endpoint-management policy."
