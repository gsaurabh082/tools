[CmdletBinding()]
param(
    [ValidateRange(0, 65535)]
    [int]$Port = 0,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$pythonCommand = Get-Command "py" -ErrorAction SilentlyContinue
$pythonPrefix = @()
if ($pythonCommand) {
    $pythonPrefix += "-3"
} else {
    $pythonCommand = Get-Command "python" -ErrorAction SilentlyContinue
}

if (-not $pythonCommand) {
    throw "Python 3 was not found. Install Python 3, then run this file again."
}

if ($Port -eq 0) {
    $portListener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    try {
        $portListener.Start()
        $Port = ([System.Net.IPEndPoint]$portListener.LocalEndpoint).Port
    } finally {
        $portListener.Stop()
    }
}

& $pythonCommand.Source @pythonPrefix -c "import fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing the local UI requirements..." -ForegroundColor Cyan
    & $pythonCommand.Source @pythonPrefix -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "The UI requirements could not be installed."
    }
}

$url = "http://127.0.0.1:$Port"
Write-Host ""
Write-Host "Jira Friday Report is starting at $url" -ForegroundColor Green
Write-Host "Keep this window open. Press Ctrl+C to stop." -ForegroundColor DarkGray

if (-not $NoOpen) {
    $openJob = Start-Job -ScriptBlock {
        param($Address)
        Start-Sleep -Seconds 2
        Start-Process $Address
    } -ArgumentList $url
}

Push-Location $PSScriptRoot
try {
    & $pythonCommand.Source @pythonPrefix -m uvicorn fastapi_app:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
    if ($openJob) {
        Remove-Job $openJob -Force -ErrorAction SilentlyContinue
    }
}
