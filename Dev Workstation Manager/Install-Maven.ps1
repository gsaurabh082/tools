$ErrorActionPreference = 'Stop'

# Maven's official distribution is no longer supplied through winget. Resolve the
# current release from Apache's Maven repository, then install it in the current
# user's local tools directory without requiring administrator access.
$metadataUrl = 'https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/maven-metadata.xml'
[xml]$metadata = Invoke-RestMethod -Uri $metadataUrl
$version = $metadata.metadata.versioning.release
if ([string]::IsNullOrWhiteSpace($version)) {
    throw 'Apache Maven did not return a current release version.'
}

$installRoot = Join-Path $env:LOCALAPPDATA 'DevToolManager\tools'
$mavenFolder = "apache-maven-$version"
$target = Join-Path $installRoot $mavenFolder

if (-not (Test-Path (Join-Path $target 'bin\mvn.cmd'))) {
    New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
    $archive = Join-Path $env:TEMP "$mavenFolder-bin.zip"
    $url = "https://dlcdn.apache.org/maven/maven-3/$version/binaries/$mavenFolder-bin.zip"
    Invoke-WebRequest -Uri $url -OutFile $archive
    Expand-Archive -Path $archive -DestinationPath $installRoot -Force
    Remove-Item -LiteralPath $archive -Force
}

$mavenBin = Join-Path $target 'bin'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathEntries = @($userPath -split ';' | Where-Object { $_ })
if ($pathEntries -notcontains $mavenBin) {
    [Environment]::SetEnvironmentVariable('Path', (($pathEntries + $mavenBin) -join ';'), 'User')
}

exit 0
