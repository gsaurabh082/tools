[CmdletBinding()]
param(
  [string]$Cli = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'dist\wincolima.exe'),
  [switch]$ColdStart,
  [switch]$SkipKubernetes,
  [string]$ReportPath
)

# Safe, repeatable release smoke test. It intentionally uses only resources
# named wincolima-qa-* and never reads Docker credential files or user Compose
# projects. The optional cold-start test shuts down every WSL distribution, so
# do not use it while unrelated WSL workloads are running.
$ErrorActionPreference = 'Stop'
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $ReportPath) {
  $ReportPath = Join-Path $env:LOCALAPPDATA "WinColima\reports\smoke-e2e-$timestamp.json"
}
$reportDirectory = Split-Path -Parent $ReportPath
New-Item -ItemType Directory -Force -Path $reportDirectory | Out-Null

$report = [ordered]@{
  product = 'WinDock'
  owner = 'Saurabh Gupta'
  startedAt = (Get-Date).ToUniversalTime().ToString('o')
  coldStart = [bool]$ColdStart
  checks = @()
  result = 'failed'
}
$containerName = "wincolima-qa-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
$composeProject = "wincolimaqa$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$qaRoot = Join-Path $env:TEMP "WinColima-QA-$timestamp"
$composeFile = Join-Path $qaRoot 'compose.yaml'

function Add-Check([string]$Name, [scriptblock]$Action) {
  $started = Get-Date
  try {
    $output = & $Action 2>&1 | Out-String
    $script:report.checks += [ordered]@{ name = $Name; status = 'passed'; durationMs = [int]((Get-Date) - $started).TotalMilliseconds; output = $output.Trim().Substring(0, [Math]::Min(2000, $output.Trim().Length)) }
  } catch {
    $script:report.checks += [ordered]@{ name = $Name; status = 'failed'; durationMs = [int]((Get-Date) - $started).TotalMilliseconds; output = $_.Exception.Message }
    throw
  }
}

try {
  if (-not (Test-Path -LiteralPath $Cli)) { throw "WinColima CLI was not found: $Cli" }
  if (-not (Get-Command docker.exe -ErrorAction SilentlyContinue)) { throw 'docker.exe is not available on PATH.' }
  if ($ColdStart) {
    Add-Check 'WSL cold shutdown' { & wsl.exe --shutdown; if ($LASTEXITCODE -ne 0) { throw "wsl.exe --shutdown exited $LASTEXITCODE" } }
  }
  Add-Check 'Runtime start and recovery' { & $Cli start --activate; if ($LASTEXITCODE -ne 0) { throw "wincolima start exited $LASTEXITCODE" } }
  Add-Check 'Runtime status' { & $Cli status; if ($LASTEXITCODE -ne 0) { throw "wincolima status exited $LASTEXITCODE" } }
  Add-Check 'Docker context version' { & docker.exe --context wincolima version; if ($LASTEXITCODE -ne 0) { throw "docker version exited $LASTEXITCODE" } }
  Add-Check 'Docker Buildx available' { & docker.exe --context wincolima buildx version; if ($LASTEXITCODE -ne 0) { throw "docker buildx exited $LASTEXITCODE" } }
  Add-Check 'Docker Compose available' { & docker.exe --context wincolima compose version; if ($LASTEXITCODE -ne 0) { throw "docker compose exited $LASTEXITCODE" } }
  Add-Check 'Container create, logs, restart, remove' {
    & docker.exe --context wincolima run --detach --name $containerName busybox:1.36.1 sh -c 'echo wincolima-qa; sleep 30'
    if ($LASTEXITCODE -ne 0) { throw "docker run exited $LASTEXITCODE" }
    & docker.exe --context wincolima logs $containerName
    if ($LASTEXITCODE -ne 0) { throw "docker logs exited $LASTEXITCODE" }
    & docker.exe --context wincolima restart $containerName
    if ($LASTEXITCODE -ne 0) { throw "docker restart exited $LASTEXITCODE" }
    & docker.exe --context wincolima rm --force $containerName
    if ($LASTEXITCODE -ne 0) { throw "docker rm exited $LASTEXITCODE" }
  }
  New-Item -ItemType Directory -Force -Path $qaRoot | Out-Null
  @"
services:
  hello:
    image: busybox:1.36.1
    command: ["sh", "-c", "echo wincolima-compose-qa; sleep 20"]
"@ | Set-Content -LiteralPath $composeFile -Encoding utf8
  Add-Check 'Compose validate and lifecycle' {
    & docker.exe --context wincolima compose --project-name $composeProject --file $composeFile config --quiet
    if ($LASTEXITCODE -ne 0) { throw "compose config exited $LASTEXITCODE" }
    & docker.exe --context wincolima compose --project-name $composeProject --file $composeFile up --detach --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw "compose up exited $LASTEXITCODE" }
    & docker.exe --context wincolima compose --project-name $composeProject --file $composeFile ps --all
    if ($LASTEXITCODE -ne 0) { throw "compose ps exited $LASTEXITCODE" }
    & docker.exe --context wincolima compose --project-name $composeProject --file $composeFile down --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw "compose down exited $LASTEXITCODE" }
  }
  if (-not $SkipKubernetes) {
    Add-Check 'Kubernetes command integration' {
      & $Cli kubernetes kubectl get nodes
      if ($LASTEXITCODE -ne 0) { throw "kubectl integration exited $LASTEXITCODE" }
    }
  }
  $report.result = 'passed'
} catch {
  $report.error = $_.Exception.Message
  throw
} finally {
  & docker.exe --context wincolima rm --force $containerName 2>$null | Out-Null
  if (Test-Path -LiteralPath $composeFile) { & docker.exe --context wincolima compose --project-name $composeProject --file $composeFile down --remove-orphans 2>$null | Out-Null }
  if (Test-Path -LiteralPath $qaRoot) { Remove-Item -LiteralPath $qaRoot -Recurse -Force -ErrorAction SilentlyContinue }
  $report.completedAt = (Get-Date).ToUniversalTime().ToString('o')
  $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReportPath -Encoding utf8
  Write-Host "WinColima QA report: $ReportPath"
}
