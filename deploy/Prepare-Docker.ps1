[CmdletBinding()]
param(
    [string]$RunnerTemp = $env:RUNNER_TEMP,
    [string]$GithubEnv = $env:GITHUB_ENV,
    [string]$GithubPath = $env:GITHUB_PATH,
    [string]$DockerExecutable
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $RunnerTemp -or -not $GithubEnv -or -not $GithubPath) { throw 'GitHub runner paths required' }
if (-not $DockerExecutable) {
    $command = Get-Command docker -ErrorAction SilentlyContinue
    if ($command) { $DockerExecutable = $command.Source }
    else { $DockerExecutable = Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/resources/bin/docker.exe' }
}
if (-not (Test-Path -LiteralPath $DockerExecutable -PathType Leaf)) { throw 'Docker executable unavailable for runner account' }
$binaryPath = Split-Path ([IO.Path]::GetFullPath($DockerExecutable)) -Parent
$pluginPath = Join-Path (Split-Path $binaryPath -Parent) 'cli-plugins'
$credentialPath = Join-Path ([IO.Path]::GetFullPath($RunnerTemp)) 'kyo-release-docker'
if (Test-Path -LiteralPath $credentialPath) { throw 'Temporary Docker config already exists; inspect previous job cleanup' }
New-Item -ItemType Directory -Path $credentialPath | Out-Null
# No personal Docker config is copied. Preserve plugin discovery before login-action
# adds this job's registry credentials to the same fresh configuration.
$config = @{}
if (Test-Path -LiteralPath $pluginPath -PathType Container) { $config.cliPluginsExtraDirs = @($pluginPath) }
$config | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $credentialPath 'config.json') -Encoding utf8
"DOCKER_CONFIG=$credentialPath" | Out-File -LiteralPath $GithubEnv -Encoding utf8 -Append
$binaryPath | Out-File -LiteralPath $GithubPath -Encoding utf8 -Append
$oldConfig = $env:DOCKER_CONFIG
try {
    $env:DOCKER_CONFIG = $credentialPath
    & $DockerExecutable compose version --short
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose unavailable with isolated runner configuration' }
} finally {
    $env:DOCKER_CONFIG = $oldConfig
}
