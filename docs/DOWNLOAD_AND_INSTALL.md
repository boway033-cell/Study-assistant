# Study Assistant 2.1 下载、安装与更新

本文面向 Windows 10/11。当前发布形态是本机运行的 Web 应用，不是桌面 EXE：双击启动器后由本地服务打开浏览器，资料不会上传到项目作者的服务器。

## 先选择下载方式

### A. 下载 Release（普通用户推荐）

1. 打开 [最新 Release](https://github.com/boway033-cell/Study-assistant/releases/latest)。
2. 下载 `study-assistant-v2.1.0.zip`。
3. 完整解压到一个长期保留、路径较短的目录，例如 `D:\Apps\Study-assistant`。

Release 已包含构建后的前端，因此不需要 Node.js；仍需安装 Python 与 Python 依赖。不要直接在压缩包预览窗口中运行 `start.bat`。

### B. Git 克隆（开发者推荐）

```powershell
git clone https://github.com/boway033-cell/Study-assistant.git
cd Study-assistant
git checkout v2.1.0
```

源码方式需要 Node.js 22+ 来构建前端。

## 首次安装

### 1. 安装 Python

安装 64 位 Python 3.12 或更高版本，并勾选“Add Python to PATH”。确认：

```powershell
python --version
```

### 2. 创建隔离环境并安装依赖

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果使用 Git 克隆或修改过前端，再执行：

```powershell
cd frontend
npm ci
npm run build
cd ..
```

### 3. 启动与停止

- 双击 `start.bat`：服务会在 `127.0.0.1:8000–8010` 中选择可用端口，健康检查通过后打开页面。
- 双击 `stop.bat`：只停止本项目确认拥有的本地进程。
- 关闭浏览器标签页不会自动停止后端；需要释放内存时请运行 `stop.bat`。

首次启动会创建 `backend/data/`。原始文献、数据库、索引、笔记和输出都在此目录中。

### 4. 可选：建立桌面入口

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\protocol\install.ps1
```

脚本会为当前 Windows 用户注册 `study-assistant://open`，并创建“打开学习助手”快捷方式。它不是开机自启；只有你打开入口时才启动服务。

## 配置 AI 模型

进入“可选工具与设置 → 设置 → 模型连接”：

1. 新建或编辑供应商连接，填写 API Key、Base URL 和模型名。
2. 运行连接检测。连接可用不代表所有任务都自动使用它。
3. 在任务路由中分别选择问答、研究、写作、PPTX 和通用生成的首选连接及回退顺序。
4. 视觉模型单独配置；未配置时只影响页面图像解读。

支持 DeepSeek、Kimi、智谱 GLM、通义、OpenAI、Anthropic、Gemini 与自定义接口。不同厂商的鉴权和请求格式由各适配器处理，不要求所有模型兼容 OpenAI。

API Key 只保存在本机并加密显示。调用云端模型时，所选范围的必要文本或图像会发送给对应供应商；完整边界见 [PRIVACY.md](../PRIVACY.md)。

## 更新到新版本

更新前必须先备份：

```powershell
.\scripts\maintenance\backup.bat
```

### Release 用户

1. 运行 `stop.bat`。
2. 下载并解压新版本到新目录。
3. 把旧目录中的 `backend/data/` 完整复制到新目录的同一位置。
4. 在新目录重新创建 `.venv` 并安装依赖。
5. 启动新版本；应用会自动执行幂等数据库迁移。
6. 确认文献、笔记和设置正常后，再决定是否删除旧目录。

不要覆盖唯一一份旧目录，也不要只复制 `study.db`；上传原文、写作输出和缓存索引也位于 `backend/data/`。

### Git 用户

```powershell
.\stop.bat
.\scripts\maintenance\backup.bat
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..
.\start.bat
```

如果本地修改与上游冲突，请先提交或另行备份，不要用 `git reset --hard` 覆盖自己的改动。

## 备份、迁移与卸载

- 推荐备份：运行 `scripts\maintenance\backup.bat`。
- 手工备份：停止服务后复制整个 `backend/data/`。
- 迁移电脑：把项目目录或新的发布目录与备份的 `backend/data/` 一起复制，在新电脑重建 `.venv`。
- 卸载：先备份，运行 `stop.bat`，再删除项目目录；如注册过协议入口，可运行 `scripts\protocol\uninstall.ps1`。

## 常见问题

### 双击后没有打开页面

查看：

- `backend/data/runtime/launcher.log`
- `server.err.log`

启动器最多等待 75 秒。首次建库或依赖缺失时，日志会给出具体原因。

### 页面显示连接被拒绝

固定的 `http://127.0.0.1:8000` 不是冷启动入口。先运行 `start.bat`，让启动器选择实际端口。

### OCR 很慢或长时间没有变化

在任务中心查看页级进度。OCR 可以取消，并会在无进展超过配置时间后失败退出；原文件不会因任务取消而删除。

### Office 文档或 PPTX 无法真实预览

- DOCX/PPTX 的高保真原版渲染依赖本机 Microsoft Office 或可用的兼容渲染环境。
- PPTX 生成不依赖本机 PowerPoint，但逐页真实视觉验收依赖 PowerPoint 自动化。
- 不可自动化时系统会保留文件并明确标记降级验收，需要用户在 PowerPoint 中打开核对。

### 如何确认安装正常

浏览器打开后进入：

1. “资料库”导入一份无敏感内容的测试 PDF。
2. 在任务中心确认解析完成。
3. 打开阅读器并测试目录跳转。
4. 进入“查证与维护 → 知识库健康”确认审计能运行。
5. 如已配置模型，再分别检测连接和任务路由。

开发者可运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm run test:unit
npm run build
```
