Set WshShell = CreateObject("WScript.Shell")
strDesktop = WshShell.SpecialFolders("Desktop")
strShortcut = strDesktop & "\启动期权分析程序.lnk"
Set oShellLink = WshShell.CreateShortcut(strShortcut)
oShellLink.TargetPath = "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard\启动程序.bat"
oShellLink.WorkingDirectory = "C:\Users\roywa\Documents\trae_projects\BRuce\crypto-options-aggregator\dashboard"
oShellLink.Description = "启动加密货币期权聚合分析程序"
oShellLink.IconLocation = "%SystemRoot%\System32\shell32.dll,13"
oShellLink.Save
MsgBox "快捷方式已创建到桌面！"
