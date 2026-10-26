$ErrorActionPreference='Stop'
$repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$root=Join-Path ([IO.Path]::GetTempPath()) ('kyo-camera-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root|Out-Null
$global:KyoCameraTestCalls=New-Object Collections.Generic.List[string]
$global:KyoCameraTestNode='test_node'
function Assert([bool]$Condition,[string]$Message) { if(-not $Condition) { throw $Message } }
function Read-Host { param([string]$Prompt,[switch]$AsSecureString); ConvertTo-SecureString 'isolated-test-password' -AsPlainText -Force }
function Invoke-RestMethod {
    param([string]$Uri,[string]$Method='Get',$Headers,[string]$ContentType,[string]$Body)
    $global:KyoCameraTestCalls.Add("$Method $Uri")
    $path=([Uri]$Uri).AbsolutePath
    switch($path) {
        '/api/auth/login' { return @{access_token='isolated-test-admin-token'} }
        '/api/nodes' { return @{token='isolated-test-node-token'} }
        '/api/field/heartbeat' { return @{node_id=$global:KyoCameraTestNode} }
        '/api/cameras' { if($Method -eq 'Get') { return @{items=@()} }; return @{} }
        '/api/cameras/TEST_CAM/node' { return @{} }
        default { throw "Unexpected fake request: $path" }
    }
}
$previousOutput=$env:KYO_CAMERA_TEST_OUTPUT
$previousExit=$env:KYO_CAMERA_TEST_EXIT
$previousNode=$env:VIDEO_NODE_TOKEN
$previousPath=$env:PATH
try {
    $config=Join-Path $root 'config'
    $register=Join-Path $repo 'video_sender/Register-Camera.ps1'
    & $register -ServerUrl https://first.example.test -NodeId test_node -CameraId TEST_CAM -ConfigRoot $config
    $tokenPath=Join-Path $config 'node-token.xml'
    Assert (Test-Path -LiteralPath $tokenPath) 'Encrypted token not saved'
    Assert (-not ([IO.File]::ReadAllText($tokenPath).Contains('isolated-test-node-token'))) 'Plaintext token persisted'
    $nodesBefore=@($global:KyoCameraTestCalls|Where-Object { $_ -eq 'Post https://first.example.test/api/nodes' }).Count
    & $register -ServerUrl https://second.example.test -NodeId test_node -CameraId TEST_CAM -ConfigRoot $config
    $settings=Get-Content -LiteralPath (Join-Path $config 'settings.json') -Raw|ConvertFrom-Json
    Assert ($settings.server_url -eq 'https://second.example.test') 'New URL not saved'
    Assert ($nodesBefore -eq 1 -and @($global:KyoCameraTestCalls|Where-Object { $_ -match '^Post .*/api/nodes$' }).Count -eq 1) 'Node recreated during URL change'
    foreach($bad in @('http://outside.example.test','https://user:password@example.test','https://example.test/api','https://example.test/?token=bad')) {
        $count=$global:KyoCameraTestCalls.Count; $failed=$false
        try { & $register -ServerUrl $bad -NodeId test_node -CameraId TEST_CAM -ConfigRoot $config } catch { $failed=$true }
        Assert ($failed -and $global:KyoCameraTestCalls.Count -eq $count) 'Unsafe server URL reached HTTP'
    }
    $global:KyoCameraTestNode='another_node'
    $count=$global:KyoCameraTestCalls.Count; $failed=$false
    try { & $register -ServerUrl https://third.example.test -NodeId test_node -CameraId TEST_CAM -ConfigRoot $config } catch { $failed=$true }
    Assert $failed 'Mismatched node token accepted'
    Assert (-not (@($global:KyoCameraTestCalls|Select-Object -Skip $count) -match '/api/cameras')) 'Camera changed before token identity check'
    $settings=Get-Content -LiteralPath (Join-Path $config 'settings.json') -Raw|ConvertFrom-Json
    Assert ($settings.server_url -eq 'https://second.example.test') 'Failed URL update changed saved settings'
    $failed=$false; $count=$global:KyoCameraTestCalls.Count
    try { & $register -ServerUrl https://first.example.test -NodeId different -CameraId TEST_CAM -ConfigRoot $config } catch { $failed=$true }
    Assert ($failed -and $global:KyoCameraTestCalls.Count -eq $count) 'Another node overwrote configuration'
    Write-Host 'PASS: register, encrypted token, URL reuse, unsafe origin and node identity guards'

    # The fake interpreter records launch settings; no Python, camera, network or FFmpeg runs.
    $bin=Join-Path $root 'bin'; New-Item -ItemType Directory -Path $bin|Out-Null
    foreach($tool in @('ffmpeg','ffprobe')) { [IO.File]::WriteAllText((Join-Path $bin ($tool+'.cmd')),"@exit /b 0`r`n") }
    $fakeScript=Join-Path $root 'fake-python.ps1'
    @'
@{token_present=[bool]$env:VIDEO_NODE_TOKEN;server=$env:VIDEO_SERVER_URL;camera=$env:VIDEO_CAMERA_ID;index=$env:VIDEO_CAMERA_INDEX;output=$env:VIDEO_OUTPUT_DIR;program=$args[0]}|ConvertTo-Json|Set-Content -LiteralPath $env:KYO_CAMERA_TEST_OUTPUT
if($env:KYO_CAMERA_TEST_EXIT) { exit ([int]$env:KYO_CAMERA_TEST_EXIT) }
exit 0
'@|Set-Content -LiteralPath $fakeScript -Encoding utf8
    $psExecutable=(Get-Process -Id $PID).Path
    $fakePython=Join-Path $bin 'python-test.cmd'
    [IO.File]::WriteAllText($fakePython,('@echo off'+"`r`n"+'"'+$psExecutable+'" -NoProfile -File "'+$fakeScript+'" %*'+"`r`n"+'exit /b %errorlevel%'+"`r`n"))
    $env:KYO_CAMERA_TEST_OUTPUT=Join-Path $root 'launch.json'
    $env:VIDEO_NODE_TOKEN='original-environment-token'
    $start=Join-Path $repo 'video_sender/Start-Camera.ps1'
    & $start -Mode Capture -CameraIndex 2 -FFmpegBin $bin -Python $fakePython -OutputDirectory (Join-Path $root 'out')
    $launch=Get-Content -LiteralPath $env:KYO_CAMERA_TEST_OUTPUT -Raw|ConvertFrom-Json
    Assert ($launch.index -eq '2' -and -not $launch.token_present -and $launch.program.EndsWith('capture.py')) 'Capture launch settings incorrect'
    Assert ($env:VIDEO_NODE_TOKEN -eq 'original-environment-token' -and $env:PATH -eq $previousPath) 'Capture changed caller environment'
    & $start -Mode Send -ConfigRoot $config -FFmpegBin $bin -Python $fakePython
    $launch=Get-Content -LiteralPath $env:KYO_CAMERA_TEST_OUTPUT -Raw|ConvertFrom-Json
    Assert ($launch.token_present -and $launch.server -eq 'https://second.example.test' -and $launch.camera -eq 'TEST_CAM' -and $launch.program.EndsWith('live_sender.py')) 'Sender launch settings incorrect'
    $env:KYO_CAMERA_TEST_EXIT='7'; $failed=$false
    try { & $start -Mode Send -ConfigRoot $config -FFmpegBin $bin -Python $fakePython } catch { $failed=$true }
    Assert ($failed -and $env:VIDEO_NODE_TOKEN -eq 'original-environment-token' -and $env:PATH -eq $previousPath) 'Failed child leaked environment changes'
    $global:LASTEXITCODE=0
    Write-Host 'PASS: capture and sender launch, foreground child failure, caller environment restoration'
} finally {
    $env:KYO_CAMERA_TEST_OUTPUT=$previousOutput
    $env:KYO_CAMERA_TEST_EXIT=$previousExit
    $env:VIDEO_NODE_TOKEN=$previousNode
    $env:PATH=$previousPath
    Remove-Variable KyoCameraTestCalls,KyoCameraTestNode -Scope Global -ErrorAction SilentlyContinue
    # Only remove the unique directory created at the start of this test.
    $resolved=[IO.Path]::GetFullPath($root)
    $temporary=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')+'\'
    if($resolved.StartsWith($temporary,[StringComparison]::OrdinalIgnoreCase) -and (Split-Path $resolved -Leaf) -match '^kyo-camera-[0-9a-f]{32}$') {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
