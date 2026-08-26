' 学习助手 - 停止（显示结果）
Option Explicit
Dim sh, fso, root, scriptsDir, scriptPath, command
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
scriptsDir = fso.GetParentFolderName(root)
scriptPath = fso.BuildPath(scriptsDir, "runtime\auto_stop.ps1")
command = "powershell -NoProfile -ExecutionPolicy Bypass -File """ & scriptPath & """"
sh.Run command, 1, True
