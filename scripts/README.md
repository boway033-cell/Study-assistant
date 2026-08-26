# 脚本目录

仓库根目录只保留 `start.bat` 与 `stop.bat` 两个稳定用户入口。其余脚本按职责收纳：

- `runtime/`：后台服务启动、停止和 PID 归属校验。
- `protocol/`：`study-assistant://open` 注册、处理与卸载。
- `launchers/`：中文 BAT/VBS 和协议快捷方式，可复制到桌面使用。
- `maintenance/`：备份等日常维护工具。
- `legacy/`：旧入口兼容脚本，不作为新用户首选入口。
- 目录根部的 Python/PowerShell 文件：版面分析、Office/PowerPoint 渲染和工作区维护工具。

常用命令：

```powershell
scripts\maintenance\backup.bat
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\protocol\install.ps1
```
