param([switch]$NoBrowser)

Add-Type -AssemblyName System.Web

$ErrorActionPreference = 'Stop'
$script:port = 38217
$script:root = $PSScriptRoot
$script:jobs = @{}
$script:tools = @(
    [pscustomobject]@{ Id = 'java17'; Name = 'Java 17'; Description = 'Temurin JDK · LTS'; Category = 'Runtimes'; Command = 'java.exe'; PackageId = 'EclipseAdoptium.Temurin.17.JDK'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'python312'; Name = 'Python 3.12'; Description = 'Python interpreter'; Category = 'Runtimes'; Command = 'python.exe'; PackageId = 'Python.Python.3.12'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'pip'; Name = 'pip'; Description = 'Included with Python'; Category = 'Runtimes'; Command = 'pip.exe'; PackageId = ''; Installer = 'included' }
    [pscustomobject]@{ Id = 'node22'; Name = 'Node.js 22'; Description = 'JavaScript runtime · LTS'; Category = 'Runtimes'; Command = 'node.exe'; PackageId = 'OpenJS.NodeJS.22'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'go'; Name = 'Go'; Description = 'Go programming language'; Category = 'Runtimes'; Command = 'go.exe'; PackageId = 'GoLang.Go'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'maven'; Name = 'Maven'; Description = 'Java build tool'; Category = 'Build tools'; Command = 'mvn.cmd'; PackageId = ''; Installer = 'apache' }
    [pscustomobject]@{ Id = 'git'; Name = 'Git'; Description = 'Source control'; Category = 'Build tools'; Command = 'git.exe'; PackageId = 'Git.Git'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'docker'; Name = 'Docker Desktop'; Description = 'Containers & images'; Category = 'Build tools'; Command = 'docker.exe'; PackageId = 'Docker.DockerDesktop'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'kubectl'; Name = 'kubectl'; Description = 'Kubernetes command line'; Category = 'Cloud & DevOps'; Command = 'kubectl.exe'; PackageId = 'Kubernetes.kubectl'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'helm'; Name = 'Helm'; Description = 'Kubernetes package manager'; Category = 'Cloud & DevOps'; Command = 'helm.exe'; PackageId = 'Helm.Helm'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'terraform'; Name = 'Terraform'; Description = 'Infrastructure as code'; Category = 'Cloud & DevOps'; Command = 'terraform.exe'; PackageId = 'Hashicorp.Terraform'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'aws'; Name = 'AWS CLI'; Description = 'Amazon Web Services CLI'; Category = 'Cloud & DevOps'; Command = 'aws.exe'; PackageId = 'Amazon.AWSCLI'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'azure'; Name = 'Azure CLI'; Description = 'Microsoft Azure CLI'; Category = 'Cloud & DevOps'; Command = 'az.cmd'; PackageId = 'Microsoft.AzureCLI'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'intellij'; Name = 'IntelliJ IDEA'; Description = 'Community Edition'; Category = 'IDEs'; Command = 'idea64.exe'; PackageId = 'JetBrains.IntelliJIDEA.Community'; Installer = 'winget'; DetectPaths = @('%ProgramFiles%\JetBrains\IntelliJ IDEA Community Edition*\bin\idea64.exe', '%LOCALAPPDATA%\Programs\IntelliJ IDEA Community Edition*\bin\idea64.exe') }
    [pscustomobject]@{ Id = 'pycharm'; Name = 'PyCharm'; Description = 'Community Edition'; Category = 'IDEs'; Command = 'pycharm64.exe'; PackageId = 'JetBrains.PyCharm.Community'; Installer = 'winget'; DetectPaths = @('%ProgramFiles%\JetBrains\PyCharm Community*\bin\pycharm64.exe', '%LOCALAPPDATA%\Programs\PyCharm Community*\bin\pycharm64.exe') }
    [pscustomobject]@{ Id = 'vs'; Name = 'Visual Studio'; Description = 'Community 2022'; Category = 'IDEs'; Command = 'devenv.exe'; PackageId = 'Microsoft.VisualStudio.2022.Community'; Installer = 'winget'; DetectPaths = @('%ProgramFiles%\Microsoft Visual Studio\2022\Community\Common7\IDE\devenv.exe') }
    [pscustomobject]@{ Id = 'heidisql'; Name = 'HeidiSQL'; Description = 'SQL database client'; Category = 'Databases'; Command = 'heidisql.exe'; PackageId = 'HeidiSQL.HeidiSQL'; Installer = 'winget'; DetectPaths = @('%ProgramFiles%\HeidiSQL\heidisql.exe') }
    [pscustomobject]@{ Id = 'mysqlwb'; Name = 'MySQL Workbench'; Description = 'Visual MySQL design'; Category = 'Databases'; Command = 'MySQLWorkbench.exe'; PackageId = 'Oracle.MySQLWorkbench'; Installer = 'winget'; DetectPaths = @('%ProgramFiles%\MySQL\MySQL Workbench 8.0 CE\MySQLWorkbench.exe') }
    [pscustomobject]@{ Id = 'kafka'; Name = 'Kafka Assistant'; Description = 'Kafka desktop client'; Category = 'Databases'; Command = 'KafkaAssistant.exe'; PackageId = 'Redisant.KafkaAssistant'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'ollama'; Name = 'Ollama'; Description = 'Run models locally'; Category = 'AI & LLM'; Command = 'ollama.exe'; PackageId = 'Ollama.Ollama'; Installer = 'winget' }
    [pscustomobject]@{ Id = 'lmstudio'; Name = 'LM Studio'; Description = 'Local model desktop app'; Category = 'AI & LLM'; Command = 'LM Studio.exe'; PackageId = 'ElementLabs.LMStudio'; Installer = 'winget'; DetectPaths = @('%LOCALAPPDATA%\Programs\LM Studio\LM Studio.exe') }
    [pscustomobject]@{ Id = 'openwebui'; Name = 'Open WebUI'; Description = 'Local LLM web interface · requires Docker'; Category = 'AI & LLM'; Command = 'docker.exe'; PackageId = ''; Installer = 'docker-app'; ContainerName = 'open-webui'; DependsOn = @('docker') }
    [pscustomobject]@{ Id = 'codex'; Name = 'Codex CLI'; Description = 'OpenAI coding agent'; Category = 'AI & LLM'; Command = 'codex.cmd'; PackageId = '@openai/codex'; Installer = 'npm'; DependsOn = @('node22') }
    [pscustomobject]@{ Id = 'claude'; Name = 'Claude Code'; Description = 'Anthropic coding agent'; Category = 'AI & LLM'; Command = 'claude.cmd'; PackageId = '@anthropic-ai/claude-code'; Installer = 'npm'; DependsOn = @('node22') }
    [pscustomobject]@{ Id = 'gemini'; Name = 'Gemini CLI'; Description = 'Google coding agent'; Category = 'AI & LLM'; Command = 'gemini.cmd'; PackageId = '@google/gemini-cli'; Installer = 'npm'; DependsOn = @('node22') }
    [pscustomobject]@{ Id = 'aider'; Name = 'Aider'; Description = 'AI pair-programming in terminal'; Category = 'AI & LLM'; Command = 'aider.exe'; PackageId = 'aider-chat'; Installer = 'pip-package'; DependsOn = @('python312') }
    [pscustomobject]@{ Id = 'huggingface'; Name = 'Hugging Face CLI'; Description = 'Models and dataset command line'; Category = 'AI & LLM'; Command = 'hf.exe'; PackageId = 'huggingface_hub'; Installer = 'pip-package'; DependsOn = @('python312') }
)

function Refresh-EnvironmentPath {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $paths = @($env:Path, $machinePath, $userPath) | Where-Object { $_ } | Select-Object -Unique
    $env:Path = $paths -join ';'
}

function Test-ToolInstalled {
    param($Tool)
    if ($Tool.Installer -eq 'docker-app') {
        if ($null -eq (Get-Command 'docker.exe' -ErrorAction SilentlyContinue)) { return $false }
        try {
            $info = [System.Diagnostics.ProcessStartInfo]::new()
            $info.FileName = 'docker.exe'
            $info.Arguments = 'ps -a --format "{{.Names}}"'
            $info.UseShellExecute = $false
            $info.RedirectStandardOutput = $true
            $info.CreateNoWindow = $true
            $probe = [System.Diagnostics.Process]::new()
            $probe.StartInfo = $info
            [void]$probe.Start()
            if (-not $probe.WaitForExit(1000)) {
                $probe.Kill()
                return $false
            }
            return @($probe.StandardOutput.ReadToEnd().Trim() -split "`r?`n") -contains $Tool.ContainerName
        }
        catch { return $false }
    }
    if ($null -ne (Get-Command $Tool.Command -ErrorAction SilentlyContinue)) { return $true }
    if ($Tool.PSObject.Properties.Name -contains 'DetectPaths') {
        foreach ($path in $Tool.DetectPaths) {
            if (Test-Path ([Environment]::ExpandEnvironmentVariables($path))) { return $true }
        }
    }
    return $false
}

function Get-ToolsData {
    Refresh-EnvironmentPath
    return @($script:tools | ForEach-Object {
        [pscustomobject]@{
            id = $_.Id
            name = $_.Name
            description = $_.Description
            category = $_.Category
            installed = Test-ToolInstalled $_
        }
    })
}

function Send-Response {
    param($Context, [int]$StatusCode, [string]$ContentType, [string]$Body)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
    $Context.Response.StatusCode = $StatusCode
    $Context.Response.ContentType = "$ContentType; charset=utf-8"
    $Context.Response.ContentLength64 = $bytes.Length
    $Context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $Context.Response.Close()
}

function Send-Json {
    param($Context, [int]$StatusCode, $Data)
    Send-Response $Context $StatusCode 'application/json' ($Data | ConvertTo-Json -Depth 7 -Compress)
}

function Test-Winget {
    return $null -ne (Get-Command 'winget.exe' -ErrorAction SilentlyContinue)
}

function Start-ToolInstall {
    param($Tool)
    Refresh-EnvironmentPath
    if ($Tool.Installer -eq 'apache') {
        $mavenInstaller = Join-Path $script:root 'Install-Maven.ps1'
        return Start-Process -FilePath 'powershell.exe' -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$mavenInstaller`"" -WindowStyle Hidden -PassThru
    }
    if ($Tool.Installer -eq 'npm') {
        return Start-Process -FilePath 'npm.cmd' -ArgumentList "install --global $($Tool.PackageId)" -WindowStyle Hidden -PassThru
    }
    if ($Tool.Installer -eq 'pip-package') {
        return Start-Process -FilePath 'python.exe' -ArgumentList "-m pip install --upgrade $($Tool.PackageId)" -WindowStyle Hidden -PassThru
    }
    if ($Tool.Installer -eq 'docker-app') {
        $arguments = 'run -d --name open-webui -p 3000:8080 --add-host=host.docker.internal:host-gateway -v open-webui:/app/backend/data ghcr.io/open-webui/open-webui:main'
        return Start-Process -FilePath 'docker.exe' -ArgumentList $arguments -WindowStyle Hidden -PassThru
    }

    $arguments = "install --id $($Tool.PackageId) --exact --accept-package-agreements --accept-source-agreements --silent --disable-interactivity"
    return Start-Process -FilePath 'winget.exe' -ArgumentList $arguments -WindowStyle Hidden -PassThru
}

function Start-InstallJob {
    param([string[]]$ToolIds)
    $selectedTools = foreach ($id in $ToolIds) {
        $tool = $script:tools | Where-Object { $_.Id -eq $id } | Select-Object -First 1
        if ($tool -and $tool.Installer -eq 'included') {
            $script:tools | Where-Object { $_.Id -eq 'python312' }
        }
        elseif ($tool) { $tool }
    }
    $requested = [System.Collections.Generic.List[object]]::new()
    $added = [System.Collections.Generic.HashSet[string]]::new()
    function Add-ToolAndDependencies {
        param($Tool)
        if ($added.Contains($Tool.Id)) { return }
        if ($Tool.PSObject.Properties.Name -contains 'DependsOn') {
            foreach ($dependencyId in $Tool.DependsOn) {
                $dependency = $script:tools | Where-Object { $_.Id -eq $dependencyId } | Select-Object -First 1
                if ($dependency -and -not (Test-ToolInstalled $dependency)) { Add-ToolAndDependencies $dependency }
            }
        }
        [void]$added.Add($Tool.Id)
        $requested.Add($Tool)
    }
    foreach ($tool in $selectedTools) { Add-ToolAndDependencies $tool }
    if ($requested.Count -eq 0) { throw 'Select at least one tool.' }
    if (($requested | Where-Object { $_.Installer -eq 'winget' }) -and -not (Test-Winget)) {
        throw 'Windows Package Manager (winget) is not available. Install or update App Installer from Microsoft Store, then launch this tool again.'
    }

    $jobId = [guid]::NewGuid().ToString('N')
    $queue = [System.Collections.Queue]::new()
    foreach ($tool in $requested) { $queue.Enqueue($tool) }
    $script:jobs[$jobId] = [pscustomobject]@{
        id = $jobId; status = 'queued'; queue = $queue; activeProcess = $null; current = $null; results = [System.Collections.ArrayList]::new()
    }
    return $script:jobs[$jobId]
}

function Update-InstallJobs {
    foreach ($job in @($script:jobs.Values)) {
        if ($job.status -eq 'complete' -or $job.status -eq 'failed') { continue }
        if ($null -ne $job.activeProcess -and $job.activeProcess.HasExited) {
            $exitCode = $job.activeProcess.ExitCode
            [void]$job.results.Add([pscustomobject]@{ name = $job.current.Name; success = ($exitCode -eq 0); message = if ($exitCode -eq 0) { 'Installed' } else { "Installer exited with code $exitCode" } })
            $job.activeProcess = $null
            $job.current = $null
        }
        if ($null -eq $job.activeProcess) {
            if ($job.queue.Count -eq 0) {
                $job.status = 'complete'
                continue
            }
            $job.current = $job.queue.Dequeue()
            try {
                $job.activeProcess = Start-ToolInstall $job.current
                $job.status = 'installing'
            }
            catch {
                [void]$job.results.Add([pscustomobject]@{ name = $job.current.Name; success = $false; message = $_.Exception.Message })
                $job.current = $null
            }
        }
    }
}

function Get-JobData {
    param($Job)
    return [pscustomobject]@{
        id = $Job.id
        status = $Job.status
        current = if ($Job.current) { $Job.current.Name } else { $null }
        queued = $Job.queue.Count
        results = @($Job.results)
    }
}

$pagePath = Join-Path $script:root 'index.html'
if (-not (Test-Path $pagePath)) { throw 'index.html is missing.' }
$page = Get-Content -LiteralPath $pagePath -Raw -Encoding UTF8
$url = "http://127.0.0.1:$($script:port)/"
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add($url)

try {
    $listener.Start()
}
catch {
    if ($NoBrowser) { Write-Error "Could not start local server: $($_.Exception.Message)" }
    else { Start-Process $url }
    exit
}

if (-not $NoBrowser) { Start-Process $url }

$contextTask = $listener.GetContextAsync()
while ($listener.IsListening) {
    Update-InstallJobs
    if (-not $contextTask.Wait(125)) { continue }
    $context = $contextTask.Result
    $contextTask = $listener.GetContextAsync()
    try {
        $path = $context.Request.Url.AbsolutePath
        $method = $context.Request.HttpMethod
        if ($method -eq 'GET' -and $path -eq '/') {
            Send-Response $context 200 'text/html' $page
        }
        elseif ($method -eq 'GET' -and $path -eq '/api/tools') {
            Send-Json $context 200 (Get-ToolsData)
        }
        elseif ($method -eq 'POST' -and $path -eq '/api/install') {
            $reader = [System.IO.StreamReader]::new($context.Request.InputStream, $context.Request.ContentEncoding)
            $payload = $reader.ReadToEnd() | ConvertFrom-Json
            $reader.Close()
            $job = Start-InstallJob @($payload.tools)
            Send-Json $context 202 (Get-JobData $job)
        }
        elseif ($method -eq 'GET' -and $path -match '^/api/jobs/([a-z0-9]+)$') {
            $job = $script:jobs[$Matches[1]]
            if ($null -eq $job) { Send-Json $context 404 @{ error = 'Installation job not found.' } }
            else { Send-Json $context 200 (Get-JobData $job) }
        }
        else {
            Send-Json $context 404 @{ error = 'Not found.' }
        }
    }
    catch {
        Send-Json $context 500 @{ error = $_.Exception.Message }
    }
}
