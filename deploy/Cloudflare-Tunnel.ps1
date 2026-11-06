[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Validate','Start','Stop','Status')][string]$Action,
    [Parameter(Mandatory)][string]$DeploymentRoot,
    [Parameter(Mandatory)][ValidatePattern('^cloudflare/cloudflared@sha256:[0-9a-f]{64}$')][string]$Image,
    [ValidatePattern('^[a-z][a-z0-9-]{2,40}$')][string]$ServerProject = 'kyo-server',
    [string]$DockerExecutable = 'docker'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$sourceRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$root = [IO.Path]::GetFullPath($DeploymentRoot)
if ($root -eq $sourceRoot -or $root.StartsWith($sourceRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'DeploymentRoot must be outside the checkout'
}
$manifestPath = Join-Path $root 'current-release.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Existing server release required' }
$release = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($release.project -ne $ServerProject) { throw 'Server project differs from current-release.json' }
if ($Action -in @('Validate','Start') -and ([bool]$release.https -or ($release.PSObject.Properties['lan'] -and [bool]$release.lan))) {
    throw 'Use the localhost web binding for a Cloudflare tunnel'
}
$tokenFile = Join-Path (Join-Path $root 'cloudflare') 'tunnel-token'
if ($Action -in @('Validate','Start') -and (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf) -or (Get-Item -LiteralPath $tokenFile).Length -eq 0)) {
    throw 'Create the external cloudflare/tunnel-token file first'
}
# Only the path is passed. Never read the token into PowerShell or command arguments.
$composeFile = Join-Path $sourceRoot 'compose.cloudflare.yaml'
$variables = @('KYO_CLOUDFLARED_IMAGE','KYO_TUNNEL_TOKEN_FILE','KYO_TUNNEL_NETWORK')
$previous = @{}
foreach ($name in $variables) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
function Invoke-TunnelDocker([string[]]$Arguments) {
    & $DockerExecutable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Tunnel Docker operation failed ($LASTEXITCODE)" }
}
try {
    $env:KYO_CLOUDFLARED_IMAGE = $Image
    $env:KYO_TUNNEL_TOKEN_FILE = $tokenFile
    $env:KYO_TUNNEL_NETWORK = $ServerProject + '_default'
    # Fixed, separate project prevents stop/up from changing the application stack.
    # Explicit empty env file prevents loading a personal .env from the checkout.
    $arguments = @('compose','--project-name','kyo-tunnel','--env-file','NUL','-f',$composeFile)
    if ($Action -eq 'Validate' -or $Action -eq 'Start') {
        Invoke-TunnelDocker ($arguments + @('config','--quiet'))
    }
    switch ($Action) {
        'Start' {
            # Refuse to attach to an unrelated Docker network with a matching name.
            $owner = & $DockerExecutable network inspect $env:KYO_TUNNEL_NETWORK --format '{{index .Labels "com.docker.compose.project"}}'
            if ($LASTEXITCODE -ne 0 -or "$owner".Trim() -ne $ServerProject) { throw 'Expected server Docker network unavailable' }
            Invoke-TunnelDocker ($arguments + @('up','-d','--no-build','--force-recreate','cloudflared'))
            Write-Output 'Connector started. Confirm Healthy in Cloudflare and verify public HTTPS separately.'
        }
        'Stop' { Invoke-TunnelDocker ($arguments + @('stop','cloudflared')) }
        'Status' { Invoke-TunnelDocker ($arguments + @('ps','cloudflared')) }
        'Validate' { Write-Output 'Tunnel configuration valid; no connection was started.' }
    }
} finally {
    foreach ($name in $variables) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
