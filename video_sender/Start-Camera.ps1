[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Capture','Send')][string]$Mode,
    [ValidateRange(0,99)][int]$CameraIndex=0,
    [string]$ConfigRoot=(Join-Path $env:USERPROFILE 'KyoCameraConfig'),
    [string]$FFmpegBin,
    [string]$Python='python',
    [string]$OutputDirectory=(Join-Path $PSScriptRoot 'output')
)
$ErrorActionPreference='Stop'
if(-not $FFmpegBin -and -not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    $tools=Join-Path $env:USERPROFILE 'KyoTools\ffmpeg'
    $probes=@(Get-ChildItem -LiteralPath $tools -Recurse -Filter ffprobe.exe -ErrorAction SilentlyContinue)
    if($probes.Count -ne 1) { throw 'Specify FFmpegBin, or install one FFmpeg build under KyoTools/ffmpeg' }
    $FFmpegBin=$probes[0].Directory.FullName
}
$names=@('PATH','VIDEO_NODE_TOKEN','VIDEO_SERVER_URL','VIDEO_CAMERA_ID','VIDEO_OUTPUT_DIR','VIDEO_CAMERA_INDEX')
$saved=@{}
foreach($name in $names) { $saved[$name]=[Environment]::GetEnvironmentVariable($name,'Process') }
$pointer=[IntPtr]::Zero
$secret=$null
try {
    if($FFmpegBin) { $env:PATH=$FFmpegBin+[IO.Path]::PathSeparator+$env:PATH }
    foreach($tool in @('ffmpeg','ffprobe')) { $null=Get-Command $tool -ErrorAction Stop }
    $pythonExecutable=(Get-Command $Python -ErrorAction Stop).Source
    $env:VIDEO_OUTPUT_DIR=[IO.Path]::GetFullPath($OutputDirectory)
    $env:VIDEO_CAMERA_INDEX=[string]$CameraIndex
    if($Mode -eq 'Send') {
        $settings=Get-Content -LiteralPath (Join-Path $ConfigRoot 'settings.json') -Raw|ConvertFrom-Json
        $uri=$null
        if(-not [Uri]::TryCreate($settings.server_url,[UriKind]::Absolute,[ref]$uri) -or
            $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/' -or
            ($uri.Scheme -ne 'https' -and -not ($uri.Scheme -eq 'http' -and $uri.Host -in @('localhost','127.0.0.1')))) { throw 'Invalid configured server origin' }
        if($settings.camera_id -notmatch '^[A-Za-z0-9_-]{1,100}$') { throw 'Invalid camera ID' }
        $secret=Import-Clixml -LiteralPath (Join-Path $ConfigRoot 'node-token.xml')
        $pointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        $env:VIDEO_NODE_TOKEN=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        $env:VIDEO_SERVER_URL=$settings.server_url
        $env:VIDEO_CAMERA_ID=$settings.camera_id
        $scriptPath=Join-Path $PSScriptRoot 'live_sender.py'
    } else {
        $env:VIDEO_NODE_TOKEN=$null
        $scriptPath=Join-Path $PSScriptRoot 'capture.py'
    }
    # Existing programs remain foreground processes; Ctrl+C stops each window.
    & $pythonExecutable $scriptPath
    if($LASTEXITCODE -ne 0) { throw "Camera $Mode exited with code $LASTEXITCODE" }
} finally {
    if($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    if($secret) { $secret.Dispose() }
    foreach($name in $names) { [Environment]::SetEnvironmentVariable($name,$saved[$name],'Process') }
}
