$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$script = Join-Path $projectRoot 'deploy/Release.ps1'
$sandbox = Join-Path ([IO.Path]::GetTempPath()) ('kyo-release-test-' + [guid]::NewGuid().ToString('N'))
$originalPath = $env:PATH
$originalLog = $env:KYO_FAKE_LOG
$originalFail = $env:KYO_FAKE_FAIL
$originalApi = $env:PPE_API_IMAGE
$originalWeb = $env:PPE_WEB_IMAGE
$originalBind = $env:PPE_WEB_BIND
$originalCookie = $env:PPE_COOKIE_SECURE
$originalHttpsLog = $env:KYO_FAKE_HTTPS_LOG
$originalHttpsFail = $env:KYO_FAKE_HTTPS_FAIL
$expectedPath = [IO.Path]::GetFullPath($sandbox)
New-Item -ItemType Directory -Path $sandbox | Out-Null
try {
    $fake = Join-Path $sandbox 'docker.ps1'
    @'
$entry = @{args=@($args);api=$env:PPE_API_IMAGE;web=$env:PPE_WEB_IMAGE;bind=$env:PPE_WEB_BIND;secure=$env:PPE_COOKIE_SECURE} | ConvertTo-Json -Compress
Add-Content -LiteralPath $env:KYO_FAKE_LOG -Value $entry
$global:LASTEXITCODE = 0
if ($args -contains 'cp') { [IO.File]::WriteAllText($args[-1], 'fake-binary-dump') }
if ($env:KYO_FAKE_FAIL -and ($args -contains 'up')) {
    $env:KYO_FAKE_FAIL = ''
    $global:LASTEXITCODE = 19
}
'@ | Set-Content -LiteralPath $fake
    $env:PATH = $sandbox + [IO.Path]::PathSeparator + $originalPath
    $env:KYO_FAKE_LOG = Join-Path $sandbox 'docker.log'
    $env:KYO_FAKE_HTTPS_LOG = Join-Path $sandbox 'https.log'
    $env:KYO_FAKE_HTTPS_FAIL = ''
    if ((Get-Command docker).Source -ne $fake) { throw 'Fake Docker did not take precedence; refusing test' }
    $target = Join-Path $sandbox 'server'
    New-Item -ItemType Directory -Path $target | Out-Null
    @'
PPE_JWT_SECRET=test-only-secret-with-at-least-thirty-two-bytes
PPE_POSTGRES_PASSWORD=test-password
PPE_DATABASE_URL=postgresql://kyo:test-password@postgres:5432/kyo
PPE_COOKIE_SECURE=false
PPE_PUBLIC_HOST=demo.example.test
'@ | Set-Content -LiteralPath (Join-Path $target 'server.env')
    $first = 'a' * 40
    $second = 'b' * 40
    $api = 'ghcr.io/oceankok/cbnu-capstone-kyo-api@sha256:' + ('1' * 64)
    $web = 'ghcr.io/oceankok/cbnu-capstone-kyo-web@sha256:' + ('2' * 64)
    function Expect-Rejection([scriptblock]$Action) {
        $failed = $false
        try { & $Action } catch { $failed = $true }
        if (-not $failed) { throw 'Expected rejection' }
    }
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $first -ApiImage $api -WebImage $web }
    if (Test-Path -LiteralPath $env:KYO_FAKE_LOG) { throw 'First-release guard invoked Docker' }
    & $script -DeploymentRoot $target -Commit $first -ApiImage $api -WebImage $web -InitializeDatabase
    $manifest = Join-Path $target 'current-release.json'
    if ((Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).commit -ne $first) { throw 'First deployment not recorded' }
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $api -WebImage $web -Https }
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $api -WebImage $web -InitializeDatabase }
    $newApi = 'ghcr.io/oceankok/cbnu-capstone-kyo-api@sha256:' + ('3' * 64)
    $env:KYO_FAKE_FAIL = 'true'
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web }
    if ((Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).commit -ne $first) { throw 'Failed release replaced manifest' }
    $commands = Get-Content -LiteralPath $env:KYO_FAKE_LOG | ForEach-Object { $_ | ConvertFrom-Json }
    if (-not ($commands | Where-Object { $_.args -contains 'up' -and $_.api -eq $api })) { throw 'Previous image was not restored' }
    if (-not (Get-ChildItem -LiteralPath (Join-Path $target 'backups') -Filter '*.dump')) { throw 'Database backup missing' }
    & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web
    if ((Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).commit -ne $second) { throw 'Successful update not recorded' }
    if ((Get-Content -LiteralPath (Join-Path $target 'previous-release.json') -Raw | ConvertFrom-Json).commit -ne $first) { throw 'Previous release record missing' }
    if ($env:PPE_API_IMAGE -ne $originalApi -or $env:PPE_WEB_IMAGE -ne $originalWeb) { throw 'Image environment leaked' }
    # Existing pre-LAN manifests have no lan property and must remain readable.
    $legacyRecord = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
    $legacyRecord.PSObject.Properties.Remove('lan')
    $legacyRecord.PSObject.Properties.Remove('tunnel_https')
    $legacyRecord | ConvertTo-Json | Set-Content -LiteralPath $manifest -Encoding utf8
    $composeFile = Join-Path $target "releases/$second/compose.release.yaml"
    $savedCompose = Get-Content -LiteralPath $composeFile -Raw
    $savedCompose.Replace('${PPE_WEB_BIND:-127.0.0.1}', '127.0.0.1') | Set-Content -LiteralPath $composeFile
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web -Lan }
    $savedCompose | Set-Content -LiteralPath $composeFile
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web -Https -Lan }
    # A failed opt-in must restore localhost, not leave the web port exposed.
    $env:KYO_FAKE_FAIL = 'true'
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web -Lan }
    if ((Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).lan) { throw 'Failed LAN update replaced manifest' }
    $commands = Get-Content -LiteralPath $env:KYO_FAKE_LOG | ForEach-Object { $_ | ConvertFrom-Json }
    $lastUp = @($commands | Where-Object { $_.args -contains 'up' })[-1]
    if ($lastUp.bind -ne '127.0.0.1') { throw 'Failed LAN update did not restore localhost' }
    & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web -Lan
    if (-not (Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).lan) { throw 'LAN opt-in not recorded' }
    # A failed return to localhost must preserve the preceding LAN mode.
    $env:KYO_FAKE_FAIL = 'true'
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web }
    $commands = Get-Content -LiteralPath $env:KYO_FAKE_LOG | ForEach-Object { $_ | ConvertFrom-Json }
    $lastUp = @($commands | Where-Object { $_.args -contains 'up' })[-1]
    if ($lastUp.bind -ne '0.0.0.0') { throw 'Failed localhost update did not restore LAN' }
    & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web
    if ((Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).lan) { throw 'Localhost opt-out not recorded' }
    if ($env:PPE_WEB_BIND -ne $originalBind) { throw 'Web binding environment leaked' }

    # A custom project cannot silently reuse another project's persistent volumes.
    $beforeCount=@(Get-Content -LiteralPath $env:KYO_FAKE_LOG).Count
    Expect-Rejection { & $script -Project 'kyo-demo' -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web }
    if (@(Get-Content -LiteralPath $env:KYO_FAKE_LOG).Count -ne $beforeCount) { throw 'Project mismatch invoked Docker' }

    function Set-TestCookie([string]$value) {
        $path=Join-Path $target 'server.env'
        $text=Get-Content -LiteralPath $path -Raw
        [regex]::Replace($text,'(?m)^PPE_COOKIE_SECURE=.*$',('PPE_COOKIE_SECURE='+$value)) | Set-Content -LiteralPath $path
    }
    function Invoke-WebRequest {
        param([string]$Uri,[int]$TimeoutSec)
        if ($Uri -ne 'https://demo.example.test/health/ready') { throw 'Unexpected network request; refusing test' }
        Add-Content -LiteralPath $env:KYO_FAKE_HTTPS_LOG -Value $Uri
        if ($env:KYO_FAKE_HTTPS_FAIL -eq 'true') {
            $env:KYO_FAKE_HTTPS_FAIL=''
            throw 'Simulated HTTPS endpoint failure'
        }
        return [pscustomobject]@{StatusCode=200}
    }
    # Secure cookies require an explicit HTTPS mode; all modes are mutually exclusive.
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web -TunnelHttps }
    Set-TestCookie 'true'
    $beforeCount=@(Get-Content -LiteralPath $env:KYO_FAKE_LOG).Count
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web }
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web -TunnelHttps -Https }
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web -TunnelHttps -Lan }
    $envPath=Join-Path $target 'server.env'
    $validSettings=Get-Content -LiteralPath $envPath -Raw
    $validSettings.Replace('demo.example.test','https://demo.example.test')|Set-Content -LiteralPath $envPath
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web -TunnelHttps }
    $validSettings|Set-Content -LiteralPath $envPath
    if (@(Get-Content -LiteralPath $env:KYO_FAKE_LOG).Count -ne $beforeCount) { throw 'Invalid tunnel configuration invoked Docker' }

    # A failed tunnel change must restore the preceding local cookie policy.
    $env:KYO_FAKE_FAIL='true'
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web -TunnelHttps }
    $commands=Get-Content -LiteralPath $env:KYO_FAKE_LOG|ForEach-Object { $_|ConvertFrom-Json }
    $lastUp=@($commands|Where-Object { $_.args -contains 'up' })[-1]
    if ($lastUp.bind -ne '127.0.0.1' -or $lastUp.secure -ne 'false') { throw 'Failed tunnel activation did not restore local policy' }
    if ((Get-Content -LiteralPath $manifest -Raw|ConvertFrom-Json).tunnel_https) { throw 'Failed tunnel activation changed manifest' }

    # A process variable cannot weaken the validated tunnel cookie policy.
    $env:PPE_COOKIE_SECURE='false'
    $beforeCount=@(Get-Content -LiteralPath $env:KYO_FAKE_LOG).Count
    & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web -TunnelHttps
    $record=Get-Content -LiteralPath $manifest -Raw|ConvertFrom-Json
    if (-not $record.tunnel_https -or $record.https -or $record.lan) { throw 'Tunnel mode not recorded independently' }
    if (-not (Test-Path -LiteralPath $env:KYO_FAKE_HTTPS_LOG)) { throw 'HTTPS endpoint was not checked' }
    $newCommands=@(Get-Content -LiteralPath $env:KYO_FAKE_LOG|Select-Object -Skip $beforeCount|ForEach-Object { $_|ConvertFrom-Json })
    if ($newCommands|Where-Object { $_.args -contains 'gateway' -or $_.args -contains '--profile' }) { throw 'Tunnel mode started Caddy' }
    $up=@($newCommands|Where-Object { $_.args -contains 'up' })[-1]
    if ($up.bind -ne '127.0.0.1' -or $up.secure -ne 'true') { throw 'Tunnel mode exposed HTTP or weakened cookies' }
    if ($env:PPE_COOKIE_SECURE -ne 'false') { throw 'Cookie process environment was not restored' }

    # Failed return to local mode preserves the previous tunnel cookie policy.
    Set-TestCookie 'false'
    $env:KYO_FAKE_FAIL='true'
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web }
    $commands=Get-Content -LiteralPath $env:KYO_FAKE_LOG|ForEach-Object { $_|ConvertFrom-Json }
    $lastUp=@($commands|Where-Object { $_.args -contains 'up' })[-1]
    if ($lastUp.secure -ne 'true' -or $lastUp.bind -ne '127.0.0.1') { throw 'Failed local change lost preceding tunnel policy' }
    if (-not (Get-Content -LiteralPath $manifest -Raw|ConvertFrom-Json).tunnel_https) { throw 'Failed local change replaced manifest' }
    & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web
    if ((Get-Content -LiteralPath $manifest -Raw|ConvertFrom-Json).tunnel_https) { throw 'Local mode did not clear tunnel flag' }
    if (-not (Get-Content -LiteralPath (Join-Path $target 'previous-release.json') -Raw|ConvertFrom-Json).tunnel_https) { throw 'Mode-only change lost preceding release record' }

    # A failed real-origin check must not publish a successful release record.
    Set-TestCookie 'true'
    $env:KYO_FAKE_HTTPS_FAIL='true'
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web -TunnelHttps }
    if ((Get-Content -LiteralPath $manifest -Raw|ConvertFrom-Json).tunnel_https) { throw 'Failed HTTPS check changed manifest' }
    $commands=Get-Content -LiteralPath $env:KYO_FAKE_LOG|ForEach-Object { $_|ConvertFrom-Json }
    $lastUp=@($commands|Where-Object { $_.args -contains 'up' })[-1]
    if ($lastUp.secure -ne 'false') { throw 'Failed HTTPS check did not restore preceding cookie mode' }

    # The explicit project reaches all Compose commands, including first initialization.
    $demo=Join-Path $sandbox 'demo'
    New-Item -ItemType Directory -Path $demo|Out-Null
    Copy-Item -LiteralPath $envPath -Destination (Join-Path $demo 'server.env')
    $beforeCount=@(Get-Content -LiteralPath $env:KYO_FAKE_LOG).Count
    & $script -Project 'kyo-demo' -DeploymentRoot $demo -Commit $first -ApiImage $api -WebImage $web -TunnelHttps -InitializeDatabase
    if ((Get-Content -LiteralPath (Join-Path $demo 'current-release.json') -Raw|ConvertFrom-Json).project -ne 'kyo-demo') { throw 'Custom project not recorded' }
    $newCommands=@(Get-Content -LiteralPath $env:KYO_FAKE_LOG|Select-Object -Skip $beforeCount|ForEach-Object { $_|ConvertFrom-Json })
    foreach($entry in $newCommands) {
        $index=[Array]::IndexOf([object[]]$entry.args,'--project-name')
        if ($index -lt 0 -or $entry.args[$index+1] -ne 'kyo-demo') { throw 'Compose used the wrong project' }
    }
    Set-TestCookie 'false'
    $env:PPE_COOKIE_SECURE=$originalCookie

    $lock = Join-Path $target 'release.lock'
    if (Test-Path -LiteralPath $lock) { throw 'Deployment lock leaked' }
    Set-Content -LiteralPath $lock -Value 'another-deployment'
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web }
    if ((Get-Content -LiteralPath $lock -Raw).Trim() -ne 'another-deployment') { throw 'Another deployment lock was modified' }
    Write-Output 'PASS: initialization, HTTPS/LAN guards, backup, image and binding rollback, LAN opt-in/out, tunnel HTTPS mode/probe/cookie rollback, custom project, environment restoration, concurrent deployment lock'
} finally {
    $env:PATH = $originalPath
    $env:KYO_FAKE_LOG = $originalLog
    $env:KYO_FAKE_FAIL = $originalFail
    $env:PPE_API_IMAGE = $originalApi
    $env:PPE_WEB_IMAGE = $originalWeb
    $env:PPE_WEB_BIND = $originalBind
    $env:PPE_COOKIE_SECURE = $originalCookie
    $env:KYO_FAKE_HTTPS_LOG = $originalHttpsLog
    $env:KYO_FAKE_HTTPS_FAIL = $originalHttpsFail
    if ([IO.Path]::GetFullPath($sandbox) -ne $expectedPath -or -not (Split-Path $expectedPath -Leaf).StartsWith('kyo-release-test-')) { throw 'Unsafe cleanup path' }
    Remove-Item -LiteralPath $expectedPath -Recurse -Force
}
