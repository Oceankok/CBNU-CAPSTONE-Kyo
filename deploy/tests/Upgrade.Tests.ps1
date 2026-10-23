$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$script = Join-Path $root 'deploy/Release.ps1'
$sandbox = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('kyo-upgrade-test-' + [guid]::NewGuid().ToString('N'))))
$variables = @('PATH','KYO_UPGRADE_LOG','KYO_UPGRADE_FAIL','KYO_UPGRADE_RUNNING','KYO_UPGRADE_BACKUP','PPE_API_IMAGE','PPE_WEB_IMAGE','PPE_WEB_BIND')
$saved = @{}
foreach ($name in $variables) { $saved[$name] = [Environment]::GetEnvironmentVariable($name) }
New-Item -ItemType Directory -Path $sandbox | Out-Null
try {
    $fake = Join-Path $sandbox 'docker.ps1'
    @'
@{args=@($args);api=$env:PPE_API_IMAGE} | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:KYO_UPGRADE_LOG
$global:LASTEXITCODE = 0
if ($args -contains 'ps') {
    Write-Output 'postgres'
    if ($env:KYO_UPGRADE_RUNNING -eq 'true') { Write-Output @('api','web','maintenance') }
}
if ($args -contains 'stop') { $env:KYO_UPGRADE_RUNNING = 'false' }
if ($args -contains 'cp' -and $args[-2] -like 'postgres:*') {
    [IO.File]::WriteAllText($args[-1], 'fake-database-dump')
    $env:KYO_UPGRADE_BACKUP = $args[-1]
}
if ($args -contains 'inspect' -and $env:KYO_UPGRADE_FAIL -eq 'inspect') { $global:LASTEXITCODE=71 }
if ($args -contains 'apply' -and $env:KYO_UPGRADE_FAIL -eq 'apply') { $env:KYO_UPGRADE_FAIL='';$global:LASTEXITCODE=72 }
if ($args -contains 'up' -and $args -contains 'api') {
    $env:KYO_UPGRADE_RUNNING='true'
    if ($env:KYO_UPGRADE_FAIL -in @('up','up_restore','tamper')) {
        if ($env:KYO_UPGRADE_FAIL -eq 'tamper') { [IO.File]::WriteAllText($env:KYO_UPGRADE_BACKUP,'corrupt') }
        $env:KYO_UPGRADE_FAIL = if ($env:KYO_UPGRADE_FAIL -eq 'up_restore') { 'restore' } else { '' }
        $global:LASTEXITCODE=73
    }
}
if ($args -contains 'pg_restore' -and $env:KYO_UPGRADE_FAIL -eq 'restore') { $global:LASTEXITCODE=74 }
if ($args -contains 'up' -and $args[-1] -eq 'maintenance' -and $args -notcontains 'api' -and $env:KYO_UPGRADE_FAIL -eq 'maintenance') { $global:LASTEXITCODE=75 }
'@ | Set-Content -LiteralPath $fake
    $env:PATH = $sandbox + [IO.Path]::PathSeparator + $saved['PATH']
    if ((Get-Command docker).Source -ne $fake) { throw 'Fake Docker required' }
    $old = 'a' * 40
    $new = 'b' * 40
    $oldApi = 'ghcr.io/oceankok/cbnu-capstone-kyo-api@sha256:' + ('1' * 64)
    $newApi = 'ghcr.io/oceankok/cbnu-capstone-kyo-api@sha256:' + ('2' * 64)
    $web = 'ghcr.io/oceankok/cbnu-capstone-kyo-web@sha256:' + ('3' * 64)
    function New-Deployment([string]$Name) {
        $target = Join-Path $sandbox $Name
        New-Item -ItemType Directory -Path $target | Out-Null
        @'
PPE_JWT_SECRET=only-test-secret-at-least-thirty-two-bytes
PPE_POSTGRES_PASSWORD=test-password
PPE_DATABASE_URL=postgresql://kyo:test-password@postgres:5432/kyo
PPE_COOKIE_SECURE=false
'@ | Set-Content -LiteralPath (Join-Path $target 'server.env')
        $env:KYO_UPGRADE_FAIL=''
        $env:KYO_UPGRADE_RUNNING='true'
        $env:KYO_UPGRADE_LOG=Join-Path $target 'docker.log'
        $null = & $script -DeploymentRoot $target -Commit $old -ApiImage $oldApi -WebImage $web -InitializeDatabase
        Clear-Content -LiteralPath $env:KYO_UPGRADE_LOG
        return $target
    }
    function Invoke-Upgrade([string]$Target) {
        & $script -DeploymentRoot $Target -Commit $new -ApiImage $newApi -WebImage $web -MigrateDatabase -ClientsPaused -ExpectedSchemaVersion 10 -TargetSchemaVersion 14
    }
    function Expect-Failure([scriptblock]$Action) {
        $failed=$false
        try { & $Action } catch { $failed=$true }
        if (-not $failed) { throw 'Expected upgrade failure' }
    }
    function Calls { @(Get-Content -LiteralPath $env:KYO_UPGRADE_LOG | ForEach-Object { $_ | ConvertFrom-Json }) }
    function Phase([string]$Target) { (Get-Content -LiteralPath (Join-Path $Target "releases/$new/upgrade-state.json") -Raw | ConvertFrom-Json).phase }
    function Current([string]$Target) { (Get-Content -LiteralPath (Join-Path $Target 'current-release.json') -Raw | ConvertFrom-Json).commit }
    $target=New-Deployment 'guards'
    Expect-Failure { & $script -DeploymentRoot $target -Commit $new -ApiImage $newApi -WebImage $web -MigrateDatabase -ExpectedSchemaVersion 10 -TargetSchemaVersion 14 }
    Expect-Failure { & $script -DeploymentRoot $target -Commit $new -ApiImage $newApi -WebImage $web -MigrateDatabase -ClientsPaused -ExpectedSchemaVersion 14 -TargetSchemaVersion 14 }
    if (@(Calls).Count) { throw 'Invalid migration invoked Docker' }
    $target=New-Deployment 'success'
    Invoke-Upgrade $target
    if ((Phase $target) -ne 'ready' -or (Current $target) -ne $new) { throw 'Successful upgrade not committed' }
    $calls=@(Calls)
    $stop=-1;$dump=-1;$apply=-1;$maintenance=-1
    for ($i=0;$i -lt $calls.Count;$i++) {
        if ($calls[$i].args -contains 'stop') { $stop=$i }
        if ($calls[$i].args -contains 'pg_dump') { $dump=$i }
        if ($calls[$i].args -contains 'apply') { $apply=$i }
        if ($calls[$i].args -contains 'up' -and $calls[$i].args[-1] -eq 'maintenance' -and $calls[$i].args -notcontains 'api') { $maintenance=$i }
    }
    if (-not (0 -le $stop -and $stop -lt $dump -and $dump -lt $apply -and $apply -lt $maintenance)) { throw 'Unsafe upgrade operation order' }
    foreach ($mode in @('inspect','apply','up','up_restore','tamper','maintenance')) {
        $target=New-Deployment $mode
        $env:KYO_UPGRADE_FAIL=$mode
        Expect-Failure { Invoke-Upgrade $target }
        $calls=@(Calls)
        $restores=@($calls|Where-Object {$_.args -contains 'pg_restore'})
        if ($mode -eq 'maintenance') {
            if ((Current $target) -ne $new -or (Phase $target) -ne 'ready_maintenance_failed' -or $restores.Count) { throw 'Post-commit failure rolled back live data' }
        } else {
            if ((Current $target) -ne $old) { throw 'Failed upgrade replaced release manifest' }
            if ($mode -in @('apply','up') -and ((Phase $target) -ne 'rolled_back' -or $restores.Count -ne 1)) { throw 'Failed migration did not restore DB and images' }
            if ($mode -in @('up_restore','tamper') -and $env:KYO_UPGRADE_RUNNING -ne 'false') { throw 'Unsafe startup after failed recovery' }
            if ($mode -eq 'tamper' -and @($calls|Where-Object {$_.args -contains 'dropdb'}).Count) { throw 'Corrupt backup caused database removal' }
            if ($mode -eq 'inspect' -and @($calls|Where-Object {$_.args -contains 'stop'}).Count) { throw 'Read-only preflight failure stopped services' }
        }
        if (Test-Path -LiteralPath (Join-Path $target 'release.lock')) { throw 'Upgrade lock leaked' }
    }
    Write-Output 'PASS: upgrade guards, pause/backup/apply order, DB+image rollback, checksum, failed recovery stop, post-commit maintenance failure'
    # Expected native failures must not become the Actions shell's final exit code.
    $global:LASTEXITCODE = 0
} finally {
    foreach ($name in $variables) { [Environment]::SetEnvironmentVariable($name,$saved[$name]) }
    $resolved=[IO.Path]::GetFullPath($sandbox)
    if ($resolved -ne $sandbox -or -not (Split-Path $resolved -Leaf).StartsWith('kyo-upgrade-test-') -or (Split-Path $resolved -Parent) -ne [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([IO.Path]::DirectorySeparatorChar)) { throw 'Unsafe upgrade test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
