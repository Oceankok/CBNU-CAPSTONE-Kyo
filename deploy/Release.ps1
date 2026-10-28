[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$DeploymentRoot,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{40}$')][string]$Commit,
    [Parameter(Mandatory)][ValidatePattern('^ghcr\.io/[a-z0-9_./-]+@sha256:[0-9a-f]{64}$')][string]$ApiImage,
    [Parameter(Mandatory)][ValidatePattern('^ghcr\.io/[a-z0-9_./-]+@sha256:[0-9a-f]{64}$')][string]$WebImage,
    [ValidatePattern('^[a-z][a-z0-9-]{2,40}$')][string]$Project = 'kyo-server',
    [switch]$Https,
    [switch]$Lan,
    [switch]$TunnelHttps,
    [switch]$InitializeDatabase
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
if (([int][bool]$Lan + [int][bool]$Https + [int][bool]$TunnelHttps) -gt 1) {
    throw 'Choose one access mode: LAN HTTP, Caddy HTTPS, or tunnel HTTPS'
}
if (-not $settings.ContainsKey('PPE_JWT_SECRET') -or [Text.Encoding]::UTF8.GetByteCount($settings['PPE_JWT_SECRET']) -lt 32) { throw 'PPE_JWT_SECRET must contain at least 32 bytes' }
if (-not $settings.ContainsKey('PPE_DATABASE_URL') -or $settings['PPE_DATABASE_URL'] -notmatch '^postgresql://[^/]+@postgres:5432/kyo$' -or $settings['PPE_DATABASE_URL'] -match 'CHANGE_ME') { throw 'Set PostgreSQL URL for the internal postgres service' }
if (($Https -or $TunnelHttps) -and (-not $settings.ContainsKey('PPE_PUBLIC_HOST') -or $settings['PPE_PUBLIC_HOST'] -notmatch '^(?=.{1,253}$)([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$' -or $settings['PPE_COOKIE_SECURE'] -ne 'true')) { throw 'HTTPS requires a public hostname and secure cookies' }
if (-not ($Https -or $TunnelHttps) -and $settings['PPE_COOKIE_SECURE'] -eq 'true') { throw 'Local HTTP requires PPE_COOKIE_SECURE=false' }
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
$previousTunnel = $false
if ($previous -and $previous.PSObject.Properties['lan']) { $previousLan = [bool]$previous.lan }
if ($previous -and $previous.PSObject.Properties['tunnel_https']) { $previousTunnel = [bool]$previous.tunnel_https }
if ($previous -and $InitializeDatabase) { throw 'InitializeDatabase is only for the first release; migrations require a separate backup/compatibility plan' }
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
$oldCookie = $env:PPE_COOKIE_SECURE
$lockPath = Join-Path $deploymentPath 'release.lock'
$deploymentLock = [IO.File]::Open($lockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
$lockBytes = [Text.Encoding]::UTF8.GetBytes([string]$PID)
$deploymentLock.Write($lockBytes, 0, $lockBytes.Length)
$deploymentLock.Flush()
$env:PPE_API_IMAGE = $ApiImage
$env:PPE_WEB_IMAGE = $WebImage
$env:PPE_WEB_BIND = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
# Compose prioritizes process variables over server.env. Pin the validated mode.
$env:PPE_COOKIE_SECURE = if ($Https -or $TunnelHttps) { 'true' } else { 'false' }
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
try {
    Invoke-Docker ($arguments + @('config','--quiet'))
    Invoke-Docker ($arguments + @('pull'))
    if ($previous) {
        # Back up PostgreSQL before changing application images. Binary dump is copied,
        # never piped through Windows text redirection.
        $backupRoot = Join-Path $deploymentPath 'backups'
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
        $backupName = 'postgres-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $Commit.Substring(0,8) + '.dump'
        Invoke-Docker ($arguments + @('exec','-T','postgres','pg_dump','-U','kyo','-d','kyo','-Fc','-f','/tmp/release-backup.dump'))
        Invoke-Docker ($arguments + @('cp','postgres:/tmp/release-backup.dump',(Join-Path $backupRoot $backupName)))
        Invoke-Docker ($arguments + @('exec','-T','postgres','rm','-f','/tmp/release-backup.dump'))
    }
    if ($InitializeDatabase) {
        Invoke-Docker ($arguments + @('up','-d','--wait','--wait-timeout','120','postgres'))
        Invoke-Docker ($arguments + @('run','--rm','db-init'))
    }
    $changed = $true
    $services = @('postgres','api','web','maintenance')
    if ($Https) { $services += 'gateway' }
    Invoke-Docker ($arguments + @('up','-d','--no-build','--wait','--wait-timeout','180') + $services)
    Confirm-Ready $releasePath
    if ($Https -or $TunnelHttps) {
        # No certificate bypass. The server must reach the selected public HTTPS origin.
        $response = Invoke-WebRequest -Uri ('https://' + $settings['PPE_PUBLIC_HOST'] + '/health/ready') -TimeoutSec 15
        if ($response.StatusCode -ne 200) { throw 'HTTPS readiness failed' }
    }
    $record = @{commit=$Commit;api_image=$ApiImage;web_image=$WebImage;project=$Project;https=[bool]$Https;lan=[bool]$Lan;tunnel_https=[bool]$TunnelHttps}
    if ($previous -and ($previous.commit -ne $Commit -or $previousLan -ne [bool]$Lan -or $previousTunnel -ne [bool]$TunnelHttps)) {
        $previous | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $deploymentPath 'previous-release.json') -Encoding utf8
    }
    $temporary = $manifestPath + '.tmp'
    $record | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $manifestPath -Force
    Write-Output "Release ready: $Commit"
} catch {
    $failure = $_
    if ($changed -and $previous) {
        Write-Warning 'Deployment failed; restoring previous application images. Database is not restored automatically.'
        $env:PPE_API_IMAGE = $previous.api_image
        $env:PPE_WEB_IMAGE = $previous.web_image
        $env:PPE_WEB_BIND = if ($previousLan) { '0.0.0.0' } else { '127.0.0.1' }
        $env:PPE_COOKIE_SECURE = if ([bool]$previous.https -or $previousTunnel) { 'true' } else { 'false' }
        $previousPath = Join-Path (Join-Path $deploymentPath 'releases') $previous.commit
        $restoreServices = @('api','web','maintenance')
        if ($Https) { $restoreServices += 'gateway' }
        Invoke-Docker ((Compose-Arguments $previousPath) + @('up','-d','--no-build','--wait','--wait-timeout','180') + $restoreServices)
        Confirm-Ready $previousPath
    }
    throw $failure
} finally {
    $env:PPE_API_IMAGE = $oldApi
    $env:PPE_WEB_IMAGE = $oldWeb
    $env:PPE_WEB_BIND = $oldBind
    $env:PPE_COOKIE_SECURE = $oldCookie
    $deploymentLock.Dispose()
    Remove-Item -LiteralPath $lockPath -Force
}
