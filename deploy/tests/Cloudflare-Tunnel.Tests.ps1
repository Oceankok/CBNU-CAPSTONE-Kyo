param([string]$DockerExecutable = 'docker')
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$script = Join-Path $repo 'deploy/Cloudflare-Tunnel.ps1'
$root = Join-Path ([IO.Path]::GetTempPath()) ('kyo-tunnel-test-' + [guid]::NewGuid().ToString('N'))
$expected = [IO.Path]::GetFullPath($root)
$oldExitCode = $global:LASTEXITCODE
$names = @('KYO_CLOUDFLARED_IMAGE','KYO_TUNNEL_TOKEN_FILE','KYO_TUNNEL_NETWORK','KYO_TUNNEL_TEST_LOG','KYO_TUNNEL_TEST_OWNER','KYO_TUNNEL_TEST_FAIL')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
function Assert([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Reject([scriptblock]$Action) {
    $failed = $false
    try { & $Action } catch { $failed = $true }
    Assert $failed 'Expected invalid tunnel configuration to fail'
}
New-Item -ItemType Directory -Path (Join-Path $root 'cloudflare') -Force | Out-Null
try {
    $image = 'cloudflare/cloudflared@sha256:' + ('1' * 64)
    $token = Join-Path $root 'cloudflare/tunnel-token'
    [IO.File]::WriteAllText($token, 'fake-test-token-not-a-credential')
    $manifest = Join-Path $root 'current-release.json'
    @{project='kyo-server';https=$false;lan=$false} | ConvertTo-Json | Set-Content -LiteralPath $manifest
    $fake = Join-Path $root 'docker.ps1'
    @'
@{args=@($args);image=$env:KYO_CLOUDFLARED_IMAGE;token_file=$env:KYO_TUNNEL_TOKEN_FILE;network=$env:KYO_TUNNEL_NETWORK}|ConvertTo-Json -Compress|Add-Content -LiteralPath $env:KYO_TUNNEL_TEST_LOG
$global:LASTEXITCODE=0
if ($args[0] -eq 'network') { Write-Output $env:KYO_TUNNEL_TEST_OWNER }
if ($env:KYO_TUNNEL_TEST_FAIL -and $args -contains 'up') { $global:LASTEXITCODE=17 }
'@ | Set-Content -LiteralPath $fake
    $env:KYO_TUNNEL_TEST_LOG = Join-Path $root 'docker.jsonl'
    $env:KYO_TUNNEL_TEST_OWNER = 'kyo-server'
    $env:KYO_TUNNEL_TEST_FAIL = ''
    $env:KYO_CLOUDFLARED_IMAGE = 'original-image'
    $env:KYO_TUNNEL_TOKEN_FILE = 'original-path'
    $env:KYO_TUNNEL_NETWORK = 'original-network'
    $invoke = @{DeploymentRoot=$root;Image=$image;DockerExecutable=$fake}
    & $script @invoke -Action Validate
    Assert (@(Get-Content -LiteralPath $env:KYO_TUNNEL_TEST_LOG).Count -eq 1) 'Validate started a connector'
    & $script @invoke -Action Start
    & $script @invoke -Action Stop
    & $script @invoke -Action Status
    $calls = @(Get-Content -LiteralPath $env:KYO_TUNNEL_TEST_LOG | ForEach-Object { $_ | ConvertFrom-Json })
    foreach ($call in $calls) {
        Assert ($call.image -eq $image -and $call.token_file -eq $token -and $call.network -eq 'kyo-server_default') 'Unvalidated process variables changed the target'
        Assert (-not (($call.args -join ' ').Contains('fake-test-token'))) 'Token contents reached Docker arguments'
        if ($call.args[0] -eq 'compose') {
            Assert ($call.args[2] -eq 'kyo-tunnel' -and $call.args[4] -eq 'NUL') 'Application project or personal env was used'
            Assert (-not ($call.args -contains 'down') -and -not ($call.args -contains 'postgres')) 'Application data or services were touched'
        }
    }
    $starts = @($calls | Where-Object { $_.args -contains 'up' })
    Assert ($starts.Count -eq 1 -and $starts[0].args -contains '--force-recreate') 'Token rotation would reuse the old connector process'
    $env:KYO_TUNNEL_TEST_OWNER = 'other-project'
    $before = @(Get-Content -LiteralPath $env:KYO_TUNNEL_TEST_LOG).Count
    Reject { & $script @invoke -Action Start }
    $extra = @(Get-Content -LiteralPath $env:KYO_TUNNEL_TEST_LOG | Select-Object -Skip $before | ForEach-Object { $_ | ConvertFrom-Json })
    Assert (-not ($extra | Where-Object { $_.args -contains 'up' })) 'Unrelated network accepted'
    $env:KYO_TUNNEL_TEST_OWNER = 'kyo-server'
    $env:KYO_TUNNEL_TEST_FAIL = '1'
    Reject { & $script @invoke -Action Start }
    Assert ($env:KYO_CLOUDFLARED_IMAGE -eq 'original-image' -and $env:KYO_TUNNEL_TOKEN_FILE -eq 'original-path' -and $env:KYO_TUNNEL_NETWORK -eq 'original-network') 'Failure changed caller environment'
    $env:KYO_TUNNEL_TEST_FAIL = ''
    Reject { & $script @invoke -Action Validate -ServerProject other-project }
    Reject { & $script -Action Validate -DeploymentRoot $repo -Image $image -DockerExecutable $fake }
    Reject { & $script -Action Validate -DeploymentRoot $root -Image 'cloudflare/cloudflared:latest' -DockerExecutable $fake }
    @{project='kyo-server';https=$false;lan=$true} | ConvertTo-Json | Set-Content -LiteralPath $manifest
    Reject { & $script @invoke -Action Start }
    @{project='kyo-server';https=$true;lan=$false} | ConvertTo-Json | Set-Content -LiteralPath $manifest
    Reject { & $script @invoke -Action Start }
    @{project='kyo-server';https=$false;lan=$false} | ConvertTo-Json | Set-Content -LiteralPath $manifest
    [IO.File]::WriteAllText($token, '')
    Reject { & $script @invoke -Action Validate }
    Remove-Item -LiteralPath $token
    Reject { & $script @invoke -Action Validate }

    # Real Compose parser only: no engine, images, network or cloud credentials.
    [IO.File]::WriteAllText($token, 'fake-test-token-not-a-credential')
    $env:KYO_CLOUDFLARED_IMAGE=$image
    $env:KYO_TUNNEL_TOKEN_FILE=$token
    $env:KYO_TUNNEL_NETWORK='kyo-server_default'
    $json = & $DockerExecutable compose --project-name kyo-tunnel --env-file NUL -f (Join-Path $repo 'compose.cloudflare.yaml') config --format json
    Assert ($LASTEXITCODE -eq 0) 'Compose rejected tunnel configuration'
    $config = ($json -join "`n") | ConvertFrom-Json
    Assert (@($config.services.PSObject.Properties).Count -eq 1) 'Unexpected service in connector stack'
    $service = $config.services.cloudflared
    Assert ($service.image -eq $image -and ($service.command -join ' ') -eq 'tunnel --no-autoupdate run --token-file /run/secrets/tunnel_token') 'File-based token launch contract differs'
    Assert (-not $service.PSObject.Properties['ports'] -and -not $service.PSObject.Properties['volumes'] -and -not $service.PSObject.Properties['environment']) 'Connector exposed ports, mounted app data or used token environment'
    Assert ($service.restart -eq 'unless-stopped' -and $service.read_only) 'Restart or read-only policy missing'
    Assert ($config.networks.application.external -and $config.networks.application.name -eq 'kyo-server_default') 'Connector would create a different server network'
    Assert ($config.secrets.tunnel_token.file -eq $token) 'Secret source differs'
    Assert (-not (($json -join '').Contains('fake-test-token'))) 'Compose printed token contents'
    Write-Output 'PASS: isolated connector lifecycle, network/project guards, digest pin, missing token, caller environment, real Compose parsing and secret/port boundaries'
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    $temporary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($expected -ne [IO.Path]::GetFullPath($root) -or -not $expected.StartsWith($temporary, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path $expected -Leaf) -notmatch '^kyo-tunnel-test-[0-9a-f]{32}$') { throw 'Unsafe test cleanup path' }
    Remove-Item -LiteralPath $expected -Recurse -Force
    $global:LASTEXITCODE = $oldExitCode
}
