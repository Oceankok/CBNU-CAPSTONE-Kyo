$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$script = Join-Path $projectRoot 'deploy/Release.ps1'
$sandbox = Join-Path ([IO.Path]::GetTempPath()) ('kyo-release-test-' + [guid]::NewGuid().ToString('N'))
$originalPath = $env:PATH
$originalLog = $env:KYO_FAKE_LOG
$originalFail = $env:KYO_FAKE_FAIL
$originalApi = $env:PPE_API_IMAGE
$originalWeb = $env:PPE_WEB_IMAGE
$expectedPath = [IO.Path]::GetFullPath($sandbox)
New-Item -ItemType Directory -Path $sandbox | Out-Null
try {
    $fake = Join-Path $sandbox 'docker.ps1'
    @'
$entry = @{args=@($args);api=$env:PPE_API_IMAGE;web=$env:PPE_WEB_IMAGE} | ConvertTo-Json -Compress
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
    if ((Get-Command docker).Source -ne $fake) { throw 'Fake Docker did not take precedence; refusing test' }
    $target = Join-Path $sandbox 'server'
    New-Item -ItemType Directory -Path $target | Out-Null
    @'
PPE_JWT_SECRET=test-only-secret-with-at-least-thirty-two-bytes
PPE_POSTGRES_PASSWORD=test-password
PPE_DATABASE_URL=postgresql://kyo:test-password@postgres:5432/kyo
PPE_COOKIE_SECURE=false
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
    $lock = Join-Path $target 'release.lock'
    if (Test-Path -LiteralPath $lock) { throw 'Deployment lock leaked' }
    Set-Content -LiteralPath $lock -Value 'another-deployment'
    Expect-Rejection { & $script -DeploymentRoot $target -Commit $second -ApiImage $newApi -WebImage $web }
    if ((Get-Content -LiteralPath $lock -Raw).Trim() -ne 'another-deployment') { throw 'Another deployment lock was modified' }
    Write-Output 'PASS: first-release guard, explicit initialization, HTTPS guard, migration guard, backup, failed update rollback, successful update, environment restoration, concurrent deployment lock'
} finally {
    $env:PATH = $originalPath
    $env:KYO_FAKE_LOG = $originalLog
    $env:KYO_FAKE_FAIL = $originalFail
    $env:PPE_API_IMAGE = $originalApi
    $env:PPE_WEB_IMAGE = $originalWeb
    if ([IO.Path]::GetFullPath($sandbox) -ne $expectedPath -or -not (Split-Path $expectedPath -Leaf).StartsWith('kyo-release-test-')) { throw 'Unsafe cleanup path' }
    Remove-Item -LiteralPath $expectedPath -Recurse -Force
}
