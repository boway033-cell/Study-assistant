# 保研复习助手（Study Assistant）

一个本地部署的**个人学习辅助软件**，帮助保研学生把大量专业课 PDF/Word 资料转化为
「AI 问答 + 卡片背诵 + 自测刷题 + 掌握度统计」的完整学习闭环。

> 核心思路：把「读资料」变成「主动回忆 + 自测 + 查漏补缺」。

## 🚀 快速启动

```bat
:: 双击 start.bat（首次运行自动装依赖）
```

然后浏览器访问 `http://127.0.0.1:8000`。

### 手动启动

```bash
# 后端
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# 前端（开发模式，可选）
cd frontend
npm install
npm run dev        # http://localhost:5173
npm run build      # 构建产物由后端自动托管
```

## ✅ 当前状态（v0.3 已可用的 MVP）

| 模块 | 状态 | 说明 |
|---|---|---|
| 资料管理 | ✅ 完成 | PDF/DOCX/PPTX 上传、章节树、中文全文搜索（jieba + FTS5） |
| AI 问答 | ✅ 完成 | RAG 检索 + LLM 流式回答，答案带出处页码；本地 Ollama ⇄ 云端 DeepSeek 可切换 |
| 卡片背诵 | ✅ 完成 | 手动/自动建卡，FSRS 间隔重复算法自动排期，每日复习队列 |
| 刷题自测 | ✅ 完成 | 选择/填空/简答，自动判分 + 自评，错题本 |
| 学习统计 | ✅ 完成 | 总览、章节掌握度、复习趋势、薄弱章节排行 |
| 设置 | ✅ 完成 | LLM 双模式切换、API Key、连接探测 |

**测试情况**：单元测试 12/12 通过 · Playwright 浏览器 UI 测试 12/12 通过（真实 Chromium）。

## 📚 文档

| 文档 | 内容 |
|---|---|
| [docs/00-PRD.md](docs/00-PRD.md) | 产品需求 |
| [docs/01-architecture.md](docs/01-architecture.md) | 技术架构 |
| [docs/02-database.md](docs/02-database.md) | 数据库设计 |
| [docs/03-api.md](docs/03-api.md) | API 接口 |
| [docs/04-roadmap.md](docs/04-roadmap.md) | 开发路线图 |

## 🧠 AI 配置（二选一）

1. **本地模式（免费离线）**：安装 [Ollama](https://ollama.com)，运行 `ollama pull qwen2.5:3b`，设置页保持"本地"
2. **云端模式（高质量）**：[DeepSeek 开放平台](https://platform.deepseek.com) 注册获取 API Key，填入设置页并切"云端"

> 低内存设计：P0 检索使用纯 FTS5 关键词（零模型内存）；向量检索为 P1 可选开关；本地 LLM 默认 3B 量化版（约 2GB 内存），可按需切换。

## 📁 数据

- 所有数据在 `backend/data/`（SQLite + 上传文件），备份 = 复制该目录
- 专业术语词典：`backend/data/userdict.txt`（每行一个词，重启后生效）

## 🧪 测试

```bash
# 单元测试
.venv\Scripts\python -m pytest backend\tests\test_unit.py -q

# 浏览器 UI 测试（需先启动后端）
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python backend\tests\ui_test.py
```

---
*版本 v0.3（MVP 完成，2026-08）*
