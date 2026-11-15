#requires -Version 7.2
param(
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$SourceRoot,
    [Parameter(Mandatory)][string]$SettingsPath,
    [Parameter(Mandatory)][string]$RuntimeDirectory
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
# Credentials are inherited. This wrapper does not read/write token files or settings.
foreach($path in @($Python,$SourceRoot,$SettingsPath,$RuntimeDirectory)) {
    if(-not [IO.Path]::IsPathFullyQualified($path)){throw 'Absolute runtime paths are required'}
}
if(-not (Test-Path -LiteralPath $Python -PathType Leaf) -or
   -not (Test-Path -LiteralPath $SettingsPath -PathType Leaf) -or
   -not (Test-Path -LiteralPath (Join-Path $SourceRoot 'deploy/host_runtime/entry.py') -PathType Leaf)) {
    throw 'Interpreter, settings or versioned entry is missing'
}
New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null
# Hold through child exit so concurrent wrappers cannot overwrite PID/log state.
$runtimeLock=[IO.File]::Open((Join-Path $RuntimeDirectory '.launch.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
$statePath=Join-Path $RuntimeDirectory 'inference-state.json'
if(Test-Path -LiteralPath $statePath) {
    $record=Get-Content -LiteralPath $statePath -Raw|ConvertFrom-Json
    $existing=Get-Process -Id $record.process_id -ErrorAction SilentlyContinue
    if($existing -and $existing.StartTime.ToUniversalTime().Ticks -eq [long]$record.start_ticks) {
        throw 'Existing inference owner is still running'
    }
}
$info=[Diagnostics.ProcessStartInfo]::new()
$info.FileName=$Python
$info.WorkingDirectory=$SourceRoot
$info.UseShellExecute=$false
$info.CreateNoWindow=$true
$info.RedirectStandardOutput=$true
$info.RedirectStandardError=$true
# .NET argument list preserves Unicode, spaces and literal shell metacharacters.
foreach($argument in @('-u','-m','deploy.host_runtime.entry','--source',$SourceRoot,
    '--settings',$SettingsPath,'--stop-file',(Join-Path $RuntimeDirectory 'inference-stop.signal'))) {
    $info.ArgumentList.Add($argument)
}
$process=[Diagnostics.Process]::new()
$process.StartInfo=$info
$stdout=[IO.File]::Open((Join-Path $RuntimeDirectory 'inference.stdout.log'),[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::Read)
$stderr=[IO.File]::Open((Join-Path $RuntimeDirectory 'inference.stderr.log'),[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::Read)
$started=$false
try {
    if(-not $process.Start()){throw 'Inference child did not start'}
    $started=$true
    $outCopy=$process.StandardOutput.BaseStream.CopyToAsync($stdout)
    $errCopy=$process.StandardError.BaseStream.CopyToAsync($stderr)
    @{process_id=$process.Id;start_ticks=$process.StartTime.ToUniversalTime().Ticks;
      managed_by='KyoPpeInferenceVersioned'}|ConvertTo-Json|Set-Content -LiteralPath $statePath -Encoding utf8
    $process.WaitForExit()
    $childExit=$process.ExitCode
    $outCopy.GetAwaiter().GetResult()
    $errCopy.GetAwaiter().GetResult()
} finally {
    if($started -and -not $process.HasExited){$process.Kill($true);$process.WaitForExit()}
    $stdout.Dispose()
    $stderr.Dispose()
    $process.Dispose()
    $runtimeLock.Dispose()
}
exit $childExit
