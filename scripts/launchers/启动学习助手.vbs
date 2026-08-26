
' 学习助手 - 一键启动（无窗口）
' 双击即可：已在运行则打开浏览器；否则后台常驻启动
Option Explicit
Dim sh, fso, root, scriptsDir, scriptPath, command
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
scriptsDir = fso.GetParentFolderName(root)
scriptPath = fso.BuildPath(scriptsDir, "runtime\auto_start.ps1")
command = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & scriptPath & """"
sh.Run command, 0, False
