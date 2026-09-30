$desktop = [Environment]::GetFolderPath('Desktop')
$wsh = New-Object -ComObject WScript.Shell

# Remove old shortcut if present
if (Test-Path "$desktop\Posture Guardian.lnk") {
    Remove-Item -Force "$desktop\Posture Guardian.lnk" -ErrorAction SilentlyContinue
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDir) { $scriptDir = (Get-Location).Path }
$exePath = Join-Path $scriptDir "dist\PosturFix\PosturFix.exe"
$workDir = Join-Path $scriptDir "dist\PosturFix"

$shortcut = $wsh.CreateShortcut("$desktop\PosturFix.lnk")
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = $workDir
$shortcut.Arguments = ""
$shortcut.Description = "PosturFix - 100% Offline Posture Monitor"
$shortcut.Save()

Write-Host "Desktop shortcut PosturFix.lnk now points directly to: $exePath"
