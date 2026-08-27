# 脚本目录

仓库根目录只保留 `start.bat` 与 `stop.bat` 两个稳定用户入口。其余脚本按职责收纳：

- `runtime/`：后台服务启动、停止和 PID 归属校验。
- `protocol/`：`study-assistant://open` 注册、处理与卸载；安装脚本会为当前用户注册协议并创建桌面网页快捷方式。
- `launchers/`：中文 BAT/VBS 和协议快捷方式；`.url` 使用协议唤醒，不直接访问尚未启动的 `127.0.0.1`。
- `maintenance/`：备份等日常维护工具。
- `legacy/`：旧入口兼容脚本，不作为新用户首选入口。
- 目录根部的 Python/PowerShell 文件：版面分析、Office/PowerPoint 渲染和工作区维护工具。

常用命令：

```powershell
scripts\maintenance\backup.bat
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\protocol\install.ps1
```

日常推荐直接双击桌面的“打开学习助手”，或在 Windows 运行框、网页链接中使用 `study-assistant://open`。启动器会串行完成“检查已有实例 → 选择 8000–8010 空闲端口 → 启动本地服务 → 健康检查 → 打开实际页面”，所以不要把固定的 `http://127.0.0.1:8000` 当作冷启动入口。整个唤醒过程只访问本机回环地址，不需要 VPN；服务未运行时不占用应用内存。
