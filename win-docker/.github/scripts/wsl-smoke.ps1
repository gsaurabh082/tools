[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$RootfsPath)

# Run only on an isolated Windows self-hosted runner. This test creates then unregisters WinColima.
$ErrorActionPreference = 'Stop'
$binary = Join-Path $PSScriptRoot '..\..\dist\wincolima.exe'
& $binary start --rootfs $RootfsPath --cpu 2 --memory 2 --disk 20
& docker.exe --context wincolima version
& docker.exe --context wincolima run --rm hello-world
& $binary stop
& $binary delete --force
