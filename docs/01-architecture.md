# 01 · 技术架构设计

- 版本：v0.2（已按「低内存占用 / Python 3.14 / 不触碰系统原有内容」修正）
- 配套文档：[00-PRD.md](00-PRD.md) / [02-database.md](02-database.md) / [03-api.md](03-api.md)

---

## 1. 总体架构

```
┌────────────────────────────────────────────────────────────┐
│  浏览器 (http://127.0.0.1:8000)                             │
│  Vue3 + Vite + Element Plus + pdf.js + ECharts              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐  │
│  │ 资料库页 │ │ AI问答页 │ │ 复习页   │ │ 刷题/统计页   │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────────┘  │
└────────────────────────────┬───────────────────────────────┘
                             │ HTTP REST + SSE（流式问答）
┌────────────────────────────▼───────────────────────────────┐
│  FastAPI 应用 (uvicorn, Python 3.11+)                       │
│                                                             │
│  api/           路由层（books / chat / cards / quizzes /    │
│                 stats / settings / interview）              │
│  services/      业务层（可替换，面向接口）                    │
│    parser/      文档解析：PDF / DOCX / PPTX / OCR           │
│    rag/         切片 + 向量化 + 检索                         │
│    llm/         LLM 路由：Ollama ⇄ DeepSeek                 │
│    srs/         FSRS 复习调度                                │
│    quiz_gen/    题目生成与判分                               │
│    stats/       掌握度计算                                   │
│  worker/        后台任务（解析 / 向量化 / 批量生成）          │
│  models/        SQLAlchemy ORM 模型                         │
│  core/          配置、日志、依赖注入                         │
└──────────────┬──────────────────────────────┬──────────────┘
               │                              │
      ┌────────▼────────┐            ┌────────▼────────┐
      │ SQLite (aiosqlite)│           │ 本地文件/向量    │
      │ + FTS5 全文索引   │           │ data/uploads    │
      │                 │            │ data/chroma     │
      └─────────────────┘            │ data/models     │
                                     └─────────────────┘
```

## 2. 技术选型

| 层 | 选型 | 版本建议 | 理由 |
|---|---|---|---|
| 后端框架 | FastAPI + Uvicorn | fastapi≥0.110 | 异步、SSE 流式、自动 OpenAPI 文档 |
| ORM | SQLAlchemy 2.0 + aiosqlite | — | 类型安全、异步兼容 |
| 数据库 | SQLite（WAL 模式） | 内置 | 单机零配置，够用 |
| 全文检索 | SQLite FTS5 + jieba | — | 中文需 jieba 分词后建索引 |
| PDF 解析 | PyMuPDF (fitz) | ≥1.24 | 快、可提取目录/文字/页码 |
| 表格/复杂版式 | pdfplumber | — | 辅助解析表格 |
| Word | python-docx | — | 段落与标题层级 |
| PPT | python-pptx | — | 文本与备注提取 |
| OCR（可选） | PaddleOCR | — | 扫描版 PDF；**体积大（paddle 依赖 1GB+）、内存占用高，默认不安装**，仅按需启用（P2） |
| 向量库 | ChromaDB | ≥0.4 | 本地、纯 Python、持久化简单 |
| 嵌入模型 | **fastembed** + bge-small-zh-v1.5 | fastembed≥0.4 | **不用 sentence-transformers（其依赖 torch，常驻内存 1.5GB+）**；fastembed 基于 ONNX Runtime，内存 <500MB，CPU 即可 |
| 本地 LLM | Ollama + **qwen2.5:3b-instruct**（量化版约 2GB） | — | 免费离线；**不用 7b**（需 4-6GB 内存，超出本机空闲预算）；qwen2.5:1.5b 可作为更低配选项 |
| 云端 LLM | DeepSeek API（deepseek-chat） | openai SDK | 质量高、成本低 |
| 记忆算法 | py-fsrs | — | Anki 同款 FSRS-6 |
| 前端 | Vue3 + Vite + TS + Element Plus | — | 组件全、开发快 |
| PDF 阅读器 | pdf.js | — | 浏览器内渲染、支持标注扩展 |
| 图表 | ECharts | — | 热力图、曲线 |
| 部署 | start.bat 启动 uvicorn 并托管前端静态产物 | — | 免装 Node 即可用 |

## 3. 目录结构（规划）

```
study-assistant/
├── start.bat                 # 一键启动（Windows）
├── requirements.txt
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI 入口，挂载静态资源
│   │   ├── core/             # config / logging / deps
│   │   ├── models/           # SQLAlchemy 模型（见 02-database）
│   │   ├── api/              # 路由（每模块一个文件）
│   │   ├── services/
│   │   │   ├── parser/       # pdf_parser.py / docx_parser.py / pptx_parser.py / ocr.py
│   │   │   ├── rag/          # chunker.py / embedder.py / retriever.py
│   │   │   ├── llm/          # base.py / ollama.py / deepseek.py / router.py
│   │   │   ├── srs/          # fsrs_service.py
│   │   │   ├── quiz_gen/     # generator.py / grader.py
│   │   │   └── stats/        # mastery.py
│   │   └── worker/           # background_tasks.py（asyncio 任务 + 进度状态）
│   └── data/                 # study.db / uploads/ / chroma/ / models/
├── frontend/
│   ├── src/
│   │   ├── views/            # LibraryView / ChatView / ReviewView / QuizView / StatsView / SettingsView
│   │   ├── components/       # PdfViewer.vue / ChapterTree.vue / CardDeck.vue / ...
│   │   ├── api/              # axios 封装
│   │   ├── stores/           # Pinia
│   │   └── router/
│   └── dist/                 # 构建产物，由 FastAPI 托管
└── docs/                     # 本文档系列
```

## 4. 关键流程设计

### 4.1 文档导入与解析流程

```
上传文件 → 存 data/uploads → 创建 Book(status=parsing)
  → 后台任务：
      1. 类型分发：pdf/docx/pptx 解析器
      2. 提取目录 → 写入 chapters（多级，parent_id）
      3. 按章节切块（chunk，保留页码区间）→ 写 chunks
      4. 全文字段写入 FTS5 索引（jieba 分词）← P0 检索主力（BM25 风格，零内存）
      5. 【P1 可选】嵌入向量化（fastembed + bge-small-zh）→ 写 ChromaDB
         （设置中开关 vector_search，默认关闭，避免模型下载与内存占用）
  → Book.status = ready（失败则 failed + 错误信息）
进度通过内存任务表 + 轮询接口暴露（/api/tasks/{id}）
```

### 4.2 AI 问答流程（RAG + SSE）

```
POST /api/chat {book_id, question, mode}
  1. 选择书籍范围 → 取书 ID 过滤
  2. retriever: 问题向量化 → ChromaDB top-k(默认 5) → 返回片段(带页码)
  3. 组装 prompt：系统提示 + 检索片段 + 用户问题
  4. llm router 按 mode 调用 Ollama / DeepSeek，流式返回
  5. 前端 SSE 逐字渲染；完成后整条记录存 chat_logs
答案引用：在流式结束后返回 sources 列表，前端渲染为可点击页码
```

### 4.3 卡片复习流程（FSRS）

```
生成卡片（自动/手动）→ state=New
  复习时：
    GET /api/cards/review-queue → 到期卡片（due <= now，按 due 排序）
    展示正面 → 用户自答 → 翻面 → 评级（again/hard/good/easy）
    POST /api/cards/{id}/review {rating}
    → py-fsrs 更新 stability/difficulty/due → 写 review_logs
  新卡每日限量（默认 20）防爆库；到期卡不限量
```

### 4.4 题目生成流程

```
POST /api/books/{id}/generate-quizzes {chapter_ids, types, count}
  → 取章节切片 → LLM 生成 JSON 数组题目
  → 前端预览（可编辑）→ 确认后批量入库 quizzes
答题：POST /api/quizzes/{id}/attempt
  → 选择/填空：比对答案自动判分
  → 简答：显示参考答案，用户自评对错
```

## 5. LLM 双模式抽象

```python
# services/llm/base.py
class LLMProvider(Protocol):
    name: str
    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]: ...

# services/llm/ollama.py
class OllamaProvider:  # 调 http://localhost:11434/api/chat
    ...

# services/llm/deepseek.py
class DeepSeekProvider:  # openai SDK，base_url=https://api.deepseek.com
    ...

# services/llm/router.py
def get_provider(mode: str) -> LLMProvider:
    # mode 来自 settings 表，或请求参数覆盖
    return OllamaProvider() if mode == "local" else DeepSeekProvider()
```

> 嵌入模型始终本地（bge-small-zh），向量化只做一次，不重复产生云端费用。

## 6. 任务与并发

- 后台任务用 `asyncio.create_task` + 内存任务注册表（简单可靠，单人单机足够）
- 长任务（解析、批量向量化）串行执行（单机 CPU 有限），任务队列 FIFO
- 向量化在后台批量做，避免阻塞上传响应

## 7. 部署与启动

```bat
:: start.bat
@echo off
cd /d %~dp0
:: 首次运行：pip install -r requirements.txt
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

- 前端 `dist/` 由 FastAPI `StaticFiles` 托管，`/` 返回 index.html
- 数据全部在 `backend/data/`，备份 = 复制该目录

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 扫描版 PDF 无文本层 | 检测文本量阈值，提示启用 PaddleOCR（P1） |
| 目录缺失/乱 | 按字体大小/页序启发式分级，可手动编辑章节 |
| 向量化耗时 | 后台任务 + 进度条；模型量化版加速 |
| 本地 LLM 质量不足 | 双模式切换，关键复习用云端 |
| FTS5 中文分词 | jieba 分词后建索引，搜索时同样分词 |
| 大 PDF 内存占用 | 流式读取、按页处理，控制 chunk 大小 |
