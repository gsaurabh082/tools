[CmdletBinding()]
param(
    [string]$Sprint,
    [string]$Jql,
    [string]$Config = (Join-Path $PSScriptRoot "jira_weekly_config.json"),
    [string]$OutputDirectory,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$reportScript = Join-Path $PSScriptRoot "jira_weekly_report.py"

$pythonCommand = Get-Command "py" -ErrorAction SilentlyContinue
$pythonArguments = @()
if ($pythonCommand) {
    $pythonArguments += "-3"
} else {
    $pythonCommand = Get-Command "python" -ErrorAction SilentlyContinue
}

if (-not $pythonCommand) {
    throw "Python 3 was not found. Install Python 3, then run this file again."
}

$pythonArguments += @($reportScript, "--config", $Config)
if ($Sprint) {
    $pythonArguments += @("--sprint", $Sprint)
}
if ($Jql) {
    $pythonArguments += @("--jql", $Jql)
}
if ($OutputDirectory) {
    $pythonArguments += @("--output-dir", $OutputDirectory)
}

$result = & $pythonCommand.Source @pythonArguments 2>&1
$exitCode = $LASTEXITCODE
$result | ForEach-Object { Write-Host $_ }

if ($exitCode -ne 0) {
    throw "Jira report generation failed with exit code $exitCode."
}

$htmlLine = $result | Where-Object { "$_".StartsWith("HTML_PATH=") } | Select-Object -Last 1
if ($htmlLine -and -not $NoOpen) {
    $htmlPath = "$htmlLine".Substring("HTML_PATH=".Length)
    Start-Process -FilePath $htmlPath
}
