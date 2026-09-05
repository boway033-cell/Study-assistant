# 脚本目录

仓库根目录只保留 `start.bat` 与 `stop.bat` 两个稳定用户入口。其余脚本按职责收纳：

- `runtime/`：后台服务启动、停止和 PID 归属校验。
- `protocol/`：`study-assistant://open` 注册、处理与卸载；安装脚本会为当前用户注册协议并创建桌面网页快捷方式。
- `launchers/`：中文 BAT/VBS 和协议快捷方式；`.url` 使用协议唤醒，不直接访问尚未启动的 `127.0.0.1`。
- `maintenance/`：备份等日常维护工具。
- `legacy/`：旧入口兼容脚本，不作为新用户首选入口。
- 目录根部的 Python/PowerShell 文件：版面分析、Office/PowerPoint 渲染和工作区维护工具。
- `benchmark_large_library.py`：在临时 SQLite 中运行合成容量基准，不接触用户数据库。
- `evaluate_retrieval.py`：运行版本化固定检索集并输出 Recall、MRR、引用与拒答指标。
- `ui_route_smoke.py`：对独立测试实例执行桌面与移动端关键路由只读巡检。
- `library_ui_smoke.cjs`：拦截 API 并使用虚构数据，检查 24 组资料库布局、菜单、机械按键及界面配置，不访问用户知识库。需要 Node.js、Playwright 和浏览器；Windows 使用 Microsoft Edge，其他平台使用 Playwright Chromium。

常用命令：

```powershell
scripts\maintenance\backup.bat
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\protocol\install.ps1
.venv\Scripts\python scripts\evaluate_retrieval.py
.venv\Scripts\python scripts\benchmark_large_library.py --books 1000 --chunks-per-book 100
```

资料库 UI 回归：先启动 `frontend` 开发服务，再从项目根目录运行 `node scripts/library_ui_smoke.cjs http://127.0.0.1:5173`。若 Playwright 安装在独立工具目录，将 `NODE_PATH` 指向其 `node_modules`；可传入第二个参数，将截图保存到仓库以外的目录。

日常推荐直接双击桌面的“打开学习助手”，或在 Windows 运行框、网页链接中使用 `study-assistant://open`。启动器会串行完成“检查已有实例 → 选择 8000–8010 空闲端口 → 启动本地服务 → 健康检查 → 打开实际页面”，所以不要把固定的 `http://127.0.0.1:8000` 当作冷启动入口。整个唤醒过程只访问本机回环地址，不需要 VPN；服务未运行时不占用应用内存。
