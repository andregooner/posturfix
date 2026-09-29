$desktop = [Environment]::GetFolderPath('Desktop')
$wsh = New-Object -ComObject WScript.Shell

# Remove old shortcut if present
if (Test-Path "$desktop\Posture Guardian.lnk") {
    Remove-Item -Force "$desktop\Posture Guardian.lnk" -ErrorAction SilentlyContinue
}

$exePath = "C:\Users\claim1\OneDrive - PT TRI DHARMA PROTEKSI\Documents\New folder\dist\PosturFix\PosturFix.exe"
$shortcut = $wsh.CreateShortcut("$desktop\PosturFix.lnk")
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = "C:\Users\claim1\OneDrive - PT TRI DHARMA PROTEKSI\Documents\New folder\dist\PosturFix"
$shortcut.Description = "PosturFix - 100% Offline Posture Monitor"
$shortcut.Save()

Write-Host "Desktop shortcut PosturFix.lnk now points directly to: $exePath"
