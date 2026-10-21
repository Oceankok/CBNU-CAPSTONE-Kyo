$ErrorActionPreference = 'Stop'
$script = Join-Path (Split-Path $PSScriptRoot -Parent) 'Prepare-Docker.ps1'
$sandbox = Join-Path ([IO.Path]::GetTempPath()) ('kyo-docker-config-test-' + [guid]::NewGuid().ToString('N'))
$expectedPath = [IO.Path]::GetFullPath($sandbox)
$oldConfig = $env:DOCKER_CONFIG
$oldFailure = $env:KYO_COMPOSE_FAIL
$oldExitCode = $global:LASTEXITCODE ?? 0
New-Item -ItemType Directory -Path (Join-Path $sandbox 'resources/bin') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $sandbox 'resources/cli-plugins') | Out-Null
try {
    $fake = Join-Path $sandbox 'resources/bin/docker.ps1'
    @'
$config = Get-Content -LiteralPath (Join-Path $env:DOCKER_CONFIG 'config.json') -Raw | ConvertFrom-Json
if (-not $config.cliPluginsExtraDirs -or $args[0] -ne 'compose') { throw 'Compose plugin discovery missing' }
if ($env:KYO_COMPOSE_FAIL) { $global:LASTEXITCODE = 19 } else { $global:LASTEXITCODE = 0 }
'@ | Set-Content -LiteralPath $fake
    $githubEnv = Join-Path $sandbox 'github.env'
    $githubPath = Join-Path $sandbox 'github.path'
    $env:DOCKER_CONFIG = 'personal-config-must-not-be-read'
    & $script -RunnerTemp $sandbox -GithubEnv $githubEnv -GithubPath $githubPath -DockerExecutable $fake
    if ($env:DOCKER_CONFIG -ne 'personal-config-must-not-be-read') { throw 'Host Docker environment was changed' }
    $configFile = Join-Path $sandbox 'kyo-release-docker/config.json'
    $config = Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
    if ($config.cliPluginsExtraDirs.Count -ne 1 -or $config.PSObject.Properties.Name -contains 'auths') { throw 'Unexpected plugin or credential configuration' }
    if ((Get-Content $githubEnv) -ne ('DOCKER_CONFIG='+(Join-Path $sandbox 'kyo-release-docker'))) { throw 'Job configuration path not exported' }
    if ((Get-Content $githubPath) -ne (Join-Path $sandbox 'resources/bin')) { throw 'Docker executable path not exported' }
    $failed = $false
    try { & $script -RunnerTemp $sandbox -GithubEnv $githubEnv -GithubPath $githubPath -DockerExecutable $fake } catch { $failed = $true }
    if (-not $failed) { throw 'Existing configuration was overwritten' }
    $failureRoot = Join-Path $sandbox 'failure'
    New-Item -ItemType Directory -Path $failureRoot | Out-Null
    $env:KYO_COMPOSE_FAIL = 'true'
    $failed = $false
    try { & $script -RunnerTemp $failureRoot -GithubEnv $githubEnv -GithubPath $githubPath -DockerExecutable $fake } catch { $failed = $true }
    if (-not $failed -or $env:DOCKER_CONFIG -ne 'personal-config-must-not-be-read') { throw 'Compose failure guard or environment restoration failed' }
    if (-not ((Get-Content $githubEnv) -contains ('DOCKER_CONFIG='+(Join-Path $failureRoot 'kyo-release-docker')))) { throw 'Failed job cleanup path unavailable' }
    Write-Output 'DOCKER CONFIG TESTS PASS: plugin discovery, isolated config, exports, overwrite guard, failure cleanup'
} finally {
    $env:DOCKER_CONFIG = $oldConfig
    $env:KYO_COMPOSE_FAIL = $oldFailure
    if ([IO.Path]::GetFullPath($sandbox) -ne $expectedPath -or -not (Split-Path $expectedPath -Leaf).StartsWith('kyo-docker-config-test-')) { throw 'Unsafe test cleanup path' }
    Remove-Item -LiteralPath $expectedPath -Recurse -Force
    # The expected fake Compose failure must not become the CI shell's exit code.
    $global:LASTEXITCODE = $oldExitCode
}
