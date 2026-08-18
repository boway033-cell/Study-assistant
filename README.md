# 学习助手（Study assistant）

![Python](https://img.shields.io/badge/Python-3.12%2B-blue)
![Vue](https://img.shields.io/badge/Vue-3-42b883)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
[![CI](https://github.com/boway033-cell/Study-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/boway033-cell/Study-assistant/actions)

一个本地部署的**个人学习辅助软件**，把 PDF / Word / PPT 教材转化为
「AI 问答 + 知识树 + 知识图谱 + AI 绘图 + 自测刷题 + 深度分析 + AI 研读 + 学习计划」的完整学习闭环。

> 核心思路：把「读资料」变成「主动回忆 + 自测 + 查漏补缺」。
>
> **AI 策略**：文本解析/切块/检索全部在本地完成（不上传资料）；仅将「提问 + 检索片段」发送到 **DeepSeek 云端**生成回答。

## 🚀 快速启动（从源码）

**前置**：[Python 3.12+](https://www.python.org/downloads/)（安装时勾选 Add to PATH）。

```bat
:: ① 首次：构建前端（需 Node.js 18+；已有 dist/ 可跳过）
cd frontend && npm install && npm run build && cd ..

:: ② 双击 start.bat（首次自动装 Python 依赖并启动）
```

然后浏览器访问 `http://127.0.0.1:8000`，停止用 `stop.bat`。

> 不想装 Node 的普通用户，可下载 GitHub Release 的预构建包（含前端产物）。

### 手动启动

```bash
# 后端
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# 前端（开发模式，可选）
cd frontend
npm install
npm run dev        # http://localhost:5173
npm run build      # 构建产物由后端自动托管
```

### 首次使用

1. 到 [DeepSeek 开放平台](https://platform.deepseek.com) 注册并创建 **API Key**
2. 打开应用，按引导弹窗前往 **设置** 页粘贴 Key 并保存
3. 设置页点击「重新检测」确认连接成功，即可开始 AI 问答
4. 模型档位：**flash**（deepseek-v4-flash，快速）/ **pro**（deepseek-v4-pro，深度推理），设置页或问答页随时切换

> 开发环境也可在项目根 `.env` 中配置 `DEEPSEEK_API_KEY`（不纳入版本控制）。
>
> **PDF 原文阅读**：内置 pdf.js 阅读器（Mozilla 开源，Firefox 同内核），直接在页面内渲染教材 PDF，无需下载。

## ✅ 当前状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 资料管理 | ✅ 完成 | PDF/DOCX/PPTX 上传、章节树、版面分析、智能分析、中文全文搜索（全部本地） |
| **PDF 阅读器** | ✅ 完成 | 连续/单页/双页三种模式、目录/深色/位置记忆、四色高亮+批注卡片+导出（本地）；选中解释翻译/章节总结/视觉解读（AI 可选）；**Markdown 精读版** |
| **深度分析** | ✅ 完成 | 导入后自动提取**三级标题目录**（章/节/小节）→ 核对缺失 → AI 补全 → **逐章 AI 精读总结** → 转 **Markdown** 存库（阅读器可切换查看） |
| **AI 研读** | ✅ 完成 | **综合阅读报告**（主题脉络/文献定位/交叉知识点/学习路径）+ **思维训练**（出题批改追问/自由陪练，多轮） |
| **文献分类** | ✅ 完成 | AI 自动分类（数学/管理学/…）存库，资料库分组展示、可手动改 |
| AI 问答 | ✅ 完成 | RAG 检索 + DeepSeek 云端流式回答，答案带出处页码；右侧原文面板（pdf.js 阅读器）；flash/pro 切换 |
| 知识树 | ✅ 完成 | 大纲/导图双视图、手动+章节导入+AI 生成框架、关联教材章节右侧看原文、节点笔记 |
| **知识图谱** | ✅ 完成 | 自动抽概念 → 全局力导向图谱 → 点概念反查所有出处；批注自动回流知识树（全本地） |
| 刷题自测 | ✅ 完成 | **AI 分析教材自动生成题目**（选书/章即可），自动判分 + 自评，错题本 |
| 学习统计 | ✅ 完成 | 总览、章节掌握度（基于作答）、作答趋势、薄弱章节排行 |
| **学习计划** | ✅ 完成 | 设定考试日期 → 按掌握度倒推每日任务 → 打卡日历（全本地） |
| **AI 绘图** | ✅ 完成 | 自然语言描述 → DeepSeek 生成 draw.io 兼容 XML → SVG 实时预览 → 多轮对话修改图表 → 导出 XML/SVG |
| **架构加固** | ✅ 完成 | 数据层版本管理/损坏检测/自动备份、任务重试与断点恢复、知识事实层统一缓存、检索重排与引用核验、服务注册表解耦 |
| 设置 | ✅ 完成 | DeepSeek API Key、flash/pro 模型切换、连接探测、数据健康状态 |

**测试情况**：单元测试 40/40 通过（含架构加固模块测试）· Playwright 浏览器 UI 测试 18/18 通过。

## 📚 文档

| 文档 | 内容 |
|---|---|
| [docs/00-PRD.md](docs/00-PRD.md) | 产品需求 |
| [docs/01-architecture.md](docs/01-architecture.md) | 技术架构 |
| [docs/02-database.md](docs/02-database.md) | 数据库设计 |
| [docs/03-api.md](docs/03-api.md) | API 接口 |
| [docs/04-roadmap.md](docs/04-roadmap.md) | 开发路线图 |
| [docs/PROJECT_HANDOVER.md](docs/PROJECT_HANDOVER.md) | 项目交接（含踩坑记录） |
| [docs/experience-pdf-analysis.md](docs/experience-pdf-analysis.md) | 教材 PDF 分析方法论 |
| [PRIVACY.md](PRIVACY.md) | 隐私说明（数据流向） |
| [SECURITY.md](SECURITY.md) | 安全策略 |
| [CHANGELOG.md](CHANGELOG.md) | 变更日志 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 |

## 🔒 隐私与数据

- **原始文件 100% 保存在本地**：上传的 PDF/Word/PPT 只在本地解析、切块、建索引，文件本身从不上传到云端。
- **本地处理**：文本解析、切块、全文检索（FTS5）全部在本地完成，不联网。
- **云端传输（仅在启用 AI 功能且已配置 API Key 时发生）**，会发送的内容如下：
  - AI 问答：你的提问 + 检索到的教材片段 → DeepSeek
  - 深度分析：逐章正文（每章最多约 1.2 万字）→ DeepSeek
  - 自动出题：教材片段 → DeepSeek
  - 综合研读 / 思维训练：多份资料内容 → DeepSeek
  - 知识树 AI 生成：章节目录 + 关键词 → DeepSeek
  - 视觉分析：页面图像 → Qwen-VL（阿里百炼）
  - 未配置对应 Key 时，这些云端功能不会触发，应用完全离线可用。
- 所有数据在 `backend/data/`（SQLite + 上传文件 + 可选向量库），备份 = 复制该目录
- API Key 保存在本地（`.env` 或数据库设置），设置页只显示脱敏值
- 专业术语词典：`backend/data/userdict.txt`（每行一个词，重启后生效）

## 🧪 测试

```bash
# 单元测试
.venv/Scripts/python -m pytest backend/tests/ -q

# 浏览器 UI 测试（需先启动后端）
.venv/Scripts/python -m playwright install chromium
.venv/Scripts/python backend/tests/ui_test.py
```

## 📦 备份与卸载

- **备份**：复制 `backend/data/` 目录，或运行 `backup.bat` 一键打包为 zip。
- **卸载**：停止服务后直接删除项目目录即可（应用数据只写在项目目录内，不修改系统其它文件）。

---
*版本 v1.0.0（见 CHANGELOG.md）*