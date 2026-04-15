$WshShell = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "Crypto-Options.lnk"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard\启动程序.bat"
$Shortcut.WorkingDirectory = "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard"
$Shortcut.Description = "Crypto Options Aggregator Dashboard"
$Shortcut.IconLocation = "%SystemRoot%\System32\shell32.dll,13"
$Shortcut.Save()
Write-Host "Shortcut created on Desktop!" -ForegroundColor Green
