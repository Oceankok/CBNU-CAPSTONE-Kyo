param([switch]$Check,[switch]$ResolveOnly)
$ErrorActionPreference='Stop'
$package=Split-Path $PSScriptRoot -Parent
$legacy=Join-Path $env:USERPROFILE 'KyoCameraDual-PR241'
$candidates=New-Object 'System.Collections.Generic.List[string]'
function Add-Python([string]$value){
 if(-not $value){return}
 $value=[Environment]::ExpandEnvironmentVariables($value.Replace('%~dp0',($legacy+'\')))
 if($value -match 'pythonw\.exe$'){$value=$value.Substring(0,$value.Length-11)+'python.exe'}
 if([IO.Path]::IsPathRooted($value) -and $value -notlike '*\WindowsApps\*' -and (Test-Path -LiteralPath $value -PathType Leaf) -and [IO.Path]::GetFileName($value) -eq 'python.exe'){
  $full=[IO.Path]::GetFullPath($value)
  if(-not $candidates.Contains($full)){$candidates.Add($full)}
 }
}
# Prefer the original launch command and its virtual environments. No settings
# file, camera registration, credential, or global Python installation is edited.
$oldCommand=Join-Path $legacy 'Open-Dual-Camera.cmd'
if(Test-Path -LiteralPath $oldCommand){
 $text=Get-Content -LiteralPath $oldCommand -Raw
 foreach($match in [regex]::Matches($text,'"([^"\r\n]*pythonw?\.exe)"')){Add-Python $match.Groups[1].Value}
}
foreach($folder in @('.venv','venv','env')){Add-Python (Join-Path $legacy ($folder+'\Scripts\python.exe'))}
foreach($hive in @('HKCU:\Software\Python\PythonCore','HKLM:\Software\Python\PythonCore')){
 try{if(Test-Path $hive){foreach($version in Get-ChildItem $hive){
  $install=Join-Path $version.PSPath 'InstallPath'
  if(Test-Path $install){$key=Get-Item $install;Add-Python ($key.GetValue('ExecutablePath'));$base=$key.GetValue('');if($base){Add-Python (Join-Path $base 'python.exe')}}
 }}}catch{continue}
}
foreach($file in Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python\*\python.exe') -ErrorAction SilentlyContinue){Add-Python $file.FullName}
$py=Get-Command py.exe -ErrorAction SilentlyContinue
if($py){try{foreach($line in (& $py.Source -0p 2>$null)){if($line -match '([A-Za-z]:\\.*python\.exe)\s*$'){Add-Python $Matches[1].Trim()}}}catch{}}
$normal=Get-Command python.exe -ErrorAction SilentlyContinue
if($normal){Add-Python $normal.Source}
$selected=$null
foreach($candidate in $candidates){
 $info=New-Object System.Diagnostics.ProcessStartInfo
 $info.FileName=$candidate;$info.Arguments='-c "import requests, cv2, tkinter"'
 $info.UseShellExecute=$false;$info.CreateNoWindow=$true
 $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
 $probe=New-Object System.Diagnostics.Process;$probe.StartInfo=$info
 try{
  if(-not $probe.Start()){continue}
  $out=$probe.StandardOutput.ReadToEndAsync();$err=$probe.StandardError.ReadToEndAsync()
  if(-not $probe.WaitForExit(8000)){$probe.Kill();$probe.WaitForExit();continue}
  if($probe.ExitCode -eq 0){$selected=$candidate;break}
 }catch{continue}finally{$probe.Dispose()}
}
if(-not $selected){
 Write-Host 'existing_python_dependencies_not_found'
 Write-Host 'No packages were installed. Keep the original program unchanged.'
 exit 2
}
if($ResolveOnly){Write-Host 'existing_python_environment_found';exit 0}
Push-Location $package
try{
 $arguments=@('-m','video_sender.automatic_dual','--settings',(Join-Path $legacy 'dual-settings.json'),
  '--launcher',(Join-Path $legacy 'camera_launcher.py'),'--policy',(Join-Path $package 'source-policy.json'))
 if($Check){$arguments+='--check'}
 & $selected @arguments
 exit $LASTEXITCODE
}finally{Pop-Location}
