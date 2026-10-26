[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ServerUrl,
    [ValidatePattern('^[A-Za-z0-9_-]{1,64}$')][string]$NodeId='remote_cam_pc_01',
    [ValidatePattern('^[A-Za-z0-9_-]{1,100}$')][string]$CameraId='REMOTE_CAM_01',
    [string]$AdminId='admin01',
    [string]$ConfigRoot=(Join-Path $env:USERPROFILE 'KyoCameraConfig')
)
$ErrorActionPreference='Stop'
$uri=$null
if(-not [Uri]::TryCreate($ServerUrl,[UriKind]::Absolute,[ref]$uri) -or
    $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/' -or
    ($uri.Scheme -ne 'https' -and -not ($uri.Scheme -eq 'http' -and $uri.Host -in @('localhost','127.0.0.1')))) {
    throw 'Use an HTTPS origin, or localhost HTTP for local development'
}
$ServerUrl=$ServerUrl.TrimEnd('/')
New-Item -ItemType Directory -Path $ConfigRoot -Force|Out-Null
$tokenPath=Join-Path $ConfigRoot 'node-token.xml'
$settingsPath=Join-Path $ConfigRoot 'settings.json'
if(Test-Path -LiteralPath $settingsPath) {
    $previous=Get-Content -LiteralPath $settingsPath -Raw|ConvertFrom-Json
    if($previous.node_id -ne $NodeId -or $previous.camera_id -ne $CameraId) {
        throw 'Existing configuration belongs to another node/camera; use a separate ConfigRoot'
    }
}
$password=Read-Host "$AdminId web password" -AsSecureString
$pointer=[IntPtr]::Zero
$tokenPointer=[IntPtr]::Zero
$nodeSecret=$null
try {
    $pointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
    $body=@{user_id=$AdminId;password=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)}|ConvertTo-Json -Compress
    $login=Invoke-RestMethod -Uri "$ServerUrl/api/auth/login" -Method Post -ContentType application/json -Body $body
    $headers=@{Authorization=('Bearer '+$login.access_token)}
    $body=$null; $login=$null
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer); $pointer=[IntPtr]::Zero
    $password.Dispose(); $password=$null
    if(-not (Test-Path -LiteralPath $tokenPath)) {
        $nodeBody=@{node_id=$NodeId;name='Remote camera PC'}|ConvertTo-Json -Compress
        $node=Invoke-RestMethod -Uri "$ServerUrl/api/nodes" -Method Post -Headers $headers -ContentType application/json -Body $nodeBody
        $nodeSecret=ConvertTo-SecureString -String $node.token -AsPlainText -Force
        $nodeSecret|Export-Clixml -LiteralPath $tokenPath
        $node=$null
    } else { $nodeSecret=Import-Clixml -LiteralPath $tokenPath }
    # Verify a reused token against the new address before changing camera bindings.
    $tokenPointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($nodeSecret)
    $nodeHeaders=@{Authorization=('Bearer '+[Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer))}
    $heartbeat=Invoke-RestMethod -Uri "$ServerUrl/api/field/heartbeat" -Method Post -Headers $nodeHeaders
    if($heartbeat.node_id -ne $NodeId) { throw 'Stored token belongs to another node' }
    $nodeHeaders=$null
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer); $tokenPointer=[IntPtr]::Zero
    $nodeSecret.Dispose(); $nodeSecret=$null
    $cameras=Invoke-RestMethod -Uri "$ServerUrl/api/cameras" -Headers $headers
    if(@($cameras.items|Where-Object camera_id -eq $CameraId).Count -eq 0) {
        $cameraBody=@{camera_id=$CameraId;camera_location='Remote camera'}|ConvertTo-Json -Compress
        $null=Invoke-RestMethod -Uri "$ServerUrl/api/cameras" -Method Post -Headers $headers -ContentType application/json -Body $cameraBody
    } else {
        $null=Invoke-RestMethod -Uri "$ServerUrl/api/cameras/$CameraId" -Method Patch -Headers $headers -ContentType application/json -Body '{"status":"active"}'
    }
    $link=@{source_node_id=$NodeId;output_node_id=$NodeId}|ConvertTo-Json -Compress
    $null=Invoke-RestMethod -Uri "$ServerUrl/api/cameras/$CameraId/node" -Method Put -Headers $headers -ContentType application/json -Body $link
    $settings=@{server_url=$ServerUrl;node_id=$NodeId;camera_id=$CameraId}|ConvertTo-Json
    [IO.File]::WriteAllText($settingsPath,$settings,(New-Object Text.UTF8Encoding($false)))
    Write-Host "Registered: $NodeId / $CameraId"
} finally {
    if($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    if($tokenPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer) }
    if($password) { $password.Dispose() }
    if($nodeSecret) { $nodeSecret.Dispose() }
    $body=$null; $headers=$null; $nodeHeaders=$null; $login=$null; $node=$null
}
