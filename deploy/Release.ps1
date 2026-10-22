[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$DeploymentRoot,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{40}$')][string]$Commit,
    [Parameter(Mandatory)][ValidatePattern('^ghcr\.io/[a-z0-9_./-]+@sha256:[0-9a-f]{64}$')][string]$ApiImage,
    [Parameter(Mandatory)][ValidatePattern('^ghcr\.io/[a-z0-9_./-]+@sha256:[0-9a-f]{64}$')][string]$WebImage,
    [ValidatePattern('^[a-z][a-z0-9-]{2,40}$')][string]$Project = 'kyo-server',
    [switch]$Https,
    [switch]$Lan,
    [switch]$InitializeDatabase,
    [switch]$MigrateDatabase,
    [switch]$ClientsPaused,
    [int]$ExpectedSchemaVersion = 0,
    [int]$TargetSchemaVersion = 0
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# This script uses only published images; no model, OAuth, or development DB access.
$sourceRoot = Split-Path $PSScriptRoot -Parent
$deploymentPath = [IO.Path]::GetFullPath($DeploymentRoot)
if ($deploymentPath.StartsWith([IO.Path]::GetFullPath($sourceRoot) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or $deploymentPath -eq [IO.Path]::GetFullPath($sourceRoot)) {
    throw 'DeploymentRoot must be outside the checkout'
}
if (-not [IO.Directory]::Exists($deploymentPath)) { throw 'Create DeploymentRoot and server.env first' }
$secretFile = Join-Path $deploymentPath 'server.env'
if (-not (Test-Path -LiteralPath $secretFile -PathType Leaf)) { throw 'Missing server.env' }
$settings = @{}
foreach ($line in Get-Content -LiteralPath $secretFile) {
    if ($line -match '^([A-Z_]+)=(.*)$') { $settings[$Matches[1]] = $Matches[2].Trim() }
}
if ($Lan -and $Https) { throw 'Choose LAN HTTP or public HTTPS, not both' }
if (-not $settings.ContainsKey('PPE_JWT_SECRET') -or [Text.Encoding]::UTF8.GetByteCount($settings['PPE_JWT_SECRET']) -lt 32) { throw 'PPE_JWT_SECRET must contain at least 32 bytes' }
if (-not $settings.ContainsKey('PPE_DATABASE_URL') -or $settings['PPE_DATABASE_URL'] -notmatch '^postgresql://[^/]+@postgres:5432/kyo$' -or $settings['PPE_DATABASE_URL'] -match 'CHANGE_ME') { throw 'Set PostgreSQL URL for the internal postgres service' }
if ($Https -and (-not $settings.ContainsKey('PPE_PUBLIC_HOST') -or $settings['PPE_PUBLIC_HOST'] -notmatch '^(?=.{1,253}$)([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$' -or $settings['PPE_COOKIE_SECURE'] -ne 'true')) { throw 'HTTPS requires a public hostname and secure cookies' }
if (-not $Https -and $settings['PPE_COOKIE_SECURE'] -eq 'true') { throw 'Local HTTP requires PPE_COOKIE_SECURE=false' }
$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) { $dockerPath = $docker.Source } else {
    $dockerPath = Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/resources/bin/docker.exe'
    if (-not (Test-Path -LiteralPath $dockerPath)) { throw 'Docker executable unavailable' }
}
function Invoke-Docker([string[]]$Arguments) {
    & $dockerPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed ($LASTEXITCODE)" }
}
$releasePath = Join-Path (Join-Path $deploymentPath 'releases') $Commit
$manifestPath = Join-Path $deploymentPath 'current-release.json'
$previous = $null
if (Test-Path -LiteralPath $manifestPath) { $previous = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json }
$previousLan = $false
if ($previous -and $previous.PSObject.Properties['lan']) { $previousLan = [bool]$previous.lan }
if ($previous -and $InitializeDatabase) { throw 'InitializeDatabase is only for the first release; migrations require a separate backup/compatibility plan' }
if ($MigrateDatabase -and ($InitializeDatabase -or -not $previous -or -not $ClientsPaused -or $ExpectedSchemaVersion -lt 10 -or $TargetSchemaVersion -le $ExpectedSchemaVersion)) { throw 'Migration requires an existing release, paused external clients/workers, and explicit increasing schema versions (10 or newer)' }
if ($ClientsPaused -and -not $MigrateDatabase) { throw 'ClientsPaused is only for an explicit database migration' }
if (-not $MigrateDatabase -and ($ExpectedSchemaVersion -or $TargetSchemaVersion)) { throw 'Schema versions require MigrateDatabase' }
if (-not $previous -and -not $InitializeDatabase) { throw 'First release requires an explicit manual InitializeDatabase run' }
if ($previous -and $previous.project -ne $Project) { throw 'Changing the persistent Compose project is not allowed' }
if ($previous -and [bool]$previous.https -ne [bool]$Https) { throw 'Change HTTPS mode through a separate reviewed deployment' }
$releaseRecord = Join-Path $releasePath 'release.json'
if (Test-Path -LiteralPath $releaseRecord) {
    $existing = Get-Content -LiteralPath $releaseRecord -Raw | ConvertFrom-Json
    if ($existing.api_image -ne $ApiImage -or $existing.web_image -ne $WebImage) { throw 'Commit already has different image digests' }
} else {
    New-Item -ItemType Directory -Path (Join-Path $releasePath 'deploy') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'compose.release.yaml') -Destination $releasePath
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Caddyfile') -Destination (Join-Path $releasePath 'deploy')
    @{commit=$Commit;api_image=$ApiImage;web_image=$WebImage} | ConvertTo-Json | Set-Content -LiteralPath $releaseRecord -Encoding utf8
}
if ($Lan -and -not (Get-Content -LiteralPath (Join-Path $releasePath 'compose.release.yaml') -Raw).Contains('${PPE_WEB_BIND')) {
    throw 'This release predates LAN binding support; choose a newer release'
}
$oldApi = $env:PPE_API_IMAGE
$oldWeb = $env:PPE_WEB_IMAGE
$oldBind = $env:PPE_WEB_BIND
$lockPath = Join-Path $deploymentPath 'release.lock'
$deploymentLock = [IO.File]::Open($lockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
$lockBytes = [Text.Encoding]::UTF8.GetBytes([string]$PID)
$deploymentLock.Write($lockBytes, 0, $lockBytes.Length)
$deploymentLock.Flush()
$env:PPE_API_IMAGE = $ApiImage
$env:PPE_WEB_IMAGE = $WebImage
$env:PPE_WEB_BIND = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
function Compose-Arguments([string]$Release) {
    $arguments = @('compose','--project-name',$Project,'--env-file',$secretFile,'-f',(Join-Path $Release 'compose.release.yaml'))
    if ($Https) { $arguments += @('--profile','https') }
    return $arguments
}
function Confirm-Ready([string]$Release) {
    $arguments = Compose-Arguments $Release
    Invoke-Docker ($arguments + @('exec','-T','api','python','-c',"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=5).close()"))
    Invoke-Docker ($arguments + @('exec','-T','web','wget','-q','-O','/dev/null','http://127.0.0.1:8080/health/ready'))
}
$arguments = Compose-Arguments $releasePath
$changed = $false
$migrationStarted = $false
$migrationCommitted = $false
$backupFile = $null
$backupHash = $null
$upgradeState = Join-Path $releasePath 'upgrade-state.json'
$services = @('postgres','api','web','maintenance')
if ($Https) { $services += 'gateway' }
$writers = @($services | Where-Object { $_ -ne 'postgres' })
function Write-UpgradeState([string]$Phase) {
    @{phase=$Phase;from_version=$ExpectedSchemaVersion;target_version=$TargetSchemaVersion;backup_file=$backupFile;backup_sha256=$backupHash;updated_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $upgradeState -Encoding utf8
}
function Stop-Writers {
    Invoke-Docker ($arguments + @('stop','--timeout','30') + $writers)
    $stillRunning = @(Invoke-Docker ($arguments + @('ps','--status','running','--services')))
    if (@($writers | Where-Object { $stillRunning -contains $_ }).Count) { throw 'Application writers are still running' }
}
try {
    Invoke-Docker ($arguments + @('config','--quiet'))
    Invoke-Docker ($arguments + @('pull'))
    if ($MigrateDatabase) {
        $running = @(Invoke-Docker ($arguments + @('ps','--status','running','--services')))
        if ($running -notcontains 'api' -or $running -notcontains 'postgres') { throw 'Start the current API/PostgreSQL release before upgrading' }
        Invoke-Docker ($arguments + @('run','--rm','--no-deps','-T','api','python','-m','backend.db.upgrade','inspect','--expected-version',"$ExpectedSchemaVersion",'--target-version',"$TargetSchemaVersion"))
        $changed = $true
        Stop-Writers
    }
    if ($previous) {
        # Back up PostgreSQL before changing application images. Binary dump is copied,
        # never piped through Windows text redirection.
        $backupRoot = Join-Path $deploymentPath 'backups'
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
        $backupName = 'postgres-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $Commit.Substring(0,8) + '-' + [guid]::NewGuid().ToString('N').Substring(0,8) + '.dump'
        Invoke-Docker ($arguments + @('exec','-T','postgres','pg_dump','-U','kyo','-d','kyo','-Fc','-f','/tmp/release-backup.dump'))
        $backupFile = Join-Path $backupRoot $backupName
        Invoke-Docker ($arguments + @('cp','postgres:/tmp/release-backup.dump',$backupFile))
        Invoke-Docker ($arguments + @('exec','-T','postgres','rm','-f','/tmp/release-backup.dump'))
        if (-not (Test-Path -LiteralPath $backupFile -PathType Leaf) -or (Get-Item -LiteralPath $backupFile).Length -eq 0) { throw 'Empty database backup' }
        $backupHash = (Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash
    }
    if ($MigrateDatabase) {
        Write-UpgradeState 'backed_up'
        $migrationStarted = $true
        Invoke-Docker ($arguments + @('run','--rm','--no-deps','-T','api','python','-m','backend.db.upgrade','apply','--expected-version',"$ExpectedSchemaVersion",'--target-version',"$TargetSchemaVersion",'--writers-paused'))
        Write-UpgradeState 'migrated'
    }
    if ($InitializeDatabase) {
        Invoke-Docker ($arguments + @('up','-d','--wait','--wait-timeout','120','postgres'))
        Invoke-Docker ($arguments + @('run','--rm','db-init'))
    }
    $changed = $true
    $startServices = if ($MigrateDatabase) { @($services | Where-Object { $_ -ne 'maintenance' }) } else { $services }
    Invoke-Docker ($arguments + @('up','-d','--no-build','--wait','--wait-timeout','180') + $startServices)
    Confirm-Ready $releasePath
    if ($Https) {
        # No certificate validation bypass. Requires hairpin DNS/routing from the server.
        $response = Invoke-WebRequest -Uri ('https://' + $settings['PPE_PUBLIC_HOST'] + '/health/ready') -TimeoutSec 15
        if ($response.StatusCode -ne 200) { throw 'HTTPS readiness failed' }
    }
    $record = @{commit=$Commit;api_image=$ApiImage;web_image=$WebImage;project=$Project;https=[bool]$Https;lan=[bool]$Lan}
    if ($MigrateDatabase) { $record['schema_version'] = $TargetSchemaVersion }
    elseif ($previous -and $previous.PSObject.Properties['schema_version']) { $record['schema_version'] = $previous.schema_version }
    if ($previous -and ($previous.commit -ne $Commit -or $previousLan -ne [bool]$Lan)) {
        $previous | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $deploymentPath 'previous-release.json') -Encoding utf8
    }
    $temporary = $manifestPath + '.tmp'
    $record | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding utf8
    if ($MigrateDatabase) { Write-UpgradeState 'ready' }
    Move-Item -LiteralPath $temporary -Destination $manifestPath -Force
    if ($MigrateDatabase) {
        $migrationCommitted = $true
        # Never run deletion maintenance before the schema/image rollback boundary.
        Invoke-Docker ($arguments + @('up','-d','--no-build','maintenance'))
    }
    Write-Output "Release ready: $Commit"
} catch {
    $failure = $_
    if ($migrationCommitted) {
        Write-UpgradeState 'ready_maintenance_failed'
        Write-Warning 'The new schema and release are committed; retry maintenance startup. Do not restore an old database over new writes.'
    } elseif ($changed -and $previous) {
        if ($migrationStarted) {
            Write-Warning 'Upgrade failed; stopping application writers and restoring the pre-upgrade database.'
            Write-UpgradeState 'rollback_started'
            Stop-Writers
            if ((Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash -ne $backupHash) { throw 'Backup checksum mismatch; services remain stopped for manual recovery' }
            Invoke-Docker ($arguments + @('cp',$backupFile,'postgres:/tmp/release-rollback.dump'))
            Invoke-Docker ($arguments + @('exec','-T','postgres','dropdb','-U','kyo','--force','kyo'))
            Invoke-Docker ($arguments + @('exec','-T','postgres','createdb','-U','kyo','kyo'))
            Invoke-Docker ($arguments + @('exec','-T','postgres','pg_restore','-U','kyo','-d','kyo','--no-owner','--no-privileges','--exit-on-error','/tmp/release-rollback.dump'))
            Invoke-Docker ($arguments + @('exec','-T','postgres','rm','-f','/tmp/release-rollback.dump'))
            Write-UpgradeState 'database_restored'
        }
        Write-Warning 'Deployment failed; restoring previous application images.'
        $env:PPE_API_IMAGE = $previous.api_image
        $env:PPE_WEB_IMAGE = $previous.web_image
        $env:PPE_WEB_BIND = if ($previousLan) { '0.0.0.0' } else { '127.0.0.1' }
        $previousPath = Join-Path (Join-Path $deploymentPath 'releases') $previous.commit
        $restoreServices = @('api','web','maintenance')
        if ($Https) { $restoreServices += 'gateway' }
        Invoke-Docker ((Compose-Arguments $previousPath) + @('up','-d','--no-build','--wait','--wait-timeout','180') + $restoreServices)
        Confirm-Ready $previousPath
        if ($MigrateDatabase) { Write-UpgradeState 'rolled_back' }
    }
    throw $failure
} finally {
    $env:PPE_API_IMAGE = $oldApi
    $env:PPE_WEB_IMAGE = $oldWeb
    $env:PPE_WEB_BIND = $oldBind
    $deploymentLock.Dispose()
    Remove-Item -LiteralPath $lockPath -Force
}
