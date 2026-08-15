# 01 · 技术架构设计

- 版本：v0.4（DeepSeek 云端唯一 AI / 知识树 / 卡片已移除）
- 配套文档：[00-PRD.md](00-PRD.md) / [02-database.md](02-database.md) / [03-api.md](03-api.md)

---

## 1. 总体架构

```
┌────────────────────────────────────────────────────────────┐
│  浏览器 (http://127.0.0.1:8000)                             │
│  Vue3 + Vite + Element Plus + ECharts                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐  │
│  │ 资料库页 │ │ AI问答页 │ │ 知识树页 │ │ 刷题/统计/设置 │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────────┘  │
└────────────────────────────┬───────────────────────────────┘
                             │ HTTP REST + SSE（流式问答）
┌────────────────────────────▼───────────────────────────────┐
│  FastAPI 应用 (uvicorn, Python 3.14)                       │
│                                                             │
│  api/           路由层（books / chat / knowledge / quizzes /│
│                 stats / settings）                          │
│  services/      业务层（可替换，面向接口）                    │
│    parser/      文档解析：PDF / DOCX / PPTX / OCR           │
│    analyzer/    版面分析 + 文本清洗 + 关键信息提取           │
│    rag/         切片 + FTS5 检索 + 向量检索(可选)            │
│    llm/         DeepSeekProvider（flash/pro 档位）           │
│  worker/        后台任务（解析 / 向量化 / 批量生成）          │
│  models/        SQLAlchemy ORM 模型                         │
│  core/          配置、数据库、依赖注入                       │
└──────────────┬──────────────────────────────┬──────────────┘
               │                              │
      ┌────────▼────────┐            ┌────────▼────────┐
      │ SQLite          │            │ 本地文件        │
      │ + FTS5 全文索引  │            │ data/uploads    │
      │                 │            │ data/chroma(可选)│
      └─────────────────┘            │ data/models(可选)│
                                     └─────────────────┘
```

## 2. 技术选型

| 层 | 选型 | 版本建议 | 理由 |
|---|---|---|---|
| 后端框架 | FastAPI + Uvicorn | fastapi≥0.110 | 异步、SSE 流式、自动 OpenAPI 文档 |
| ORM | SQLAlchemy 2.0 | — | 类型安全 |
| 数据库 | SQLite（WAL 模式） | 内置 | 单机零配置，够用 |
| 全文检索 | SQLite FTS5 + jieba | — | 中文需 jieba 分词后建索引（零内存） |
| PDF 解析 | PyMuPDF (fitz) | ≥1.24 | 快、可提取目录/文字/页码 |
| Word / PPT | python-docx / python-pptx | — | 段落与标题层级 |
| OCR（可选） | PaddleOCR / tesseract | — | 扫描版 PDF；体积大，默认不安装，按需启用（P2） |
| 向量库（可选） | ChromaDB | ≥0.4 | 本地、纯 Python；`vector_search` 默认关 |
| 嵌入模型（可选） | fastembed + bge-small-zh-v1.5 | fastembed≥0.4 | ONNX Runtime，内存 <500MB，全离线 |
| **云端 LLM** | **DeepSeek API（deepseek-v4-flash / deepseek-v4-pro）** | httpx | **唯一 AI 后端（本地 Ollama 已取消）**；质量高、成本低 |
| 前端 | Vue3 + Vite + Element Plus | — | 组件全、开发快 |
| 图表 | ECharts | — | 趋势图、掌握度柱状图 |
| 部署 | start.bat 启动 uvicorn 并托管前端静态产物 | — | 免装 Node 即可用 |

## 3. 目录结构

```
study-assistant/
├── start.bat                 # 一键启动（Windows）
├── requirements.txt
├── .env                      # DEEPSEEK_API_KEY 等（不纳入 git）
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI 入口，挂载静态资源
│   │   ├── core/             # config（含 .env 加载）/ database
│   │   ├── models/           # SQLAlchemy 模型（见 02-database）
│   │   ├── api/              # books/chat/knowledge/quizzes/stats/settings
│   │   ├── services/
│   │   │   ├── parser/       # PDF/DOCX/PPTX + ocr.py
│   │   │   ├── analyzer/     # layout / textclean / keyinfo
│   │   │   ├── rag/          # chunker / semantic_chunker / fts / retriever / vector / toc_*
│   │   │   └── llm/          # DeepSeekProvider + load_llm_config
│   │   └── worker/           # tasks.py（后台线程+事件循环）/ import_task.py
│   └── data/                 # study.db / uploads/ / chroma/ / models/
├── frontend/
│   └── src/
│       ├── views/            # Library / Chat / Knowledge / Quiz / Stats / Settings
│       ├── components/       # OriginalViewer.vue
│       ├── api/              # axios 封装
│       └── router/           # hash 路由
└── docs/                     # 本文档系列
```

## 4. 关键流程设计

### 4.1 文档导入与解析流程（100% 本地）

```
上传文件 → 存 data/uploads → 创建 Book(status=parsing)
  → 后台任务（不联网）：
      1. 类型分发：pdf/docx/pptx 解析器；扫描版（页均文本 <30 字符）自动触发 OCR 提示
      2. 版面分析（PDF）：按字体/字号/位置识别 标题/正文/页眉页脚/表格/公式
      3. 文本清洗：去页眉页脚重复、行去重、重复字符压缩、断行合并
      4. 关键信息提取：定义句/定理命题/关键词 → book_analysis 表
      5. 章节树（目录书签 → 启发式 → LLM 兜底）→ chapters 表
      6. 按章节语义切块 → chunks（保留页码映射）
      7. FTS5 索引（jieba 分词，BM25 风格，零内存）← P0 检索主力
      8. 【P1 可选】嵌入向量化（fastembed）→ ChromaDB（默认关闭）
  → Book.status = ready
```

### 4.2 AI 问答流程（RAG + SSE + 宽定位 + 右侧原文）

```
POST /api/chat {book_id, question, model: flash|pro}
  1. 选择书籍范围 → 取书 ID 过滤
  2. 宽定位检索（retriever.retrieve）：
     a. FTS5 关键词检索 top-k
     b. 命中不足 → LIKE 子串匹配兜底
     c. 章节级上下文：命中 chunk 拉取同章节相邻 chunk
     d. 全文兜底：仍无命中 → 返回目录结构
  3. 组装 prompt（context 完整内容，截断保护 12000 字符）
  4. 从 DB 读配置（key/模型档位）→ DeepSeekProvider 流式返回
  5. 前端 SSE 逐字渲染；完成后存 chat_logs
  6. 前端右侧原文面板自动展示首个出处的 chunk 原文（文本/PDF iframe）
```

### 4.3 知识树流程

```
GET  /api/knowledge/tree            # 全量嵌套树
POST /api/knowledge/nodes           # 建节点（parent_id + title）
PATCH /api/knowledge/nodes/{id}     # 改标题/笔记/关联书籍章节
POST /api/knowledge/nodes/{id}/move # 移动（防环校验）
DELETE /api/knowledge/nodes/{id}    # 删除子树
GET  /api/knowledge/nodes/{id}/source  # 关联章节原文（合并该章 chunks）
```

### 4.4 题目生成流程

```
POST /api/books/{id}/generate-quizzes
  → 取章节切片 → DeepSeek 生成 JSON 题目 → 入库
答题：POST /api/quizzes/{id}/attempt
  → 选择/填空：比对答案自动判分
  → 简答：显示参考答案，用户自评对错
```

## 5. LLM 抽象（仅 DeepSeek）

```python
# services/llm/__init__.py
DEEPSEEK_MODELS = {"flash": "deepseek-v4-flash", "pro": "deepseek-v4-pro"}

def load_llm_config(db) -> dict:
    # 配置优先级：数据库 settings 表（设置页写入）> .env / 内存默认
    return {
        "deepseek_api_key": ...,
        "deepseek_base_url": ...,
        "deepseek_model": ...,  # flash / pro
    }

class DeepSeekProvider(LLMProvider):
    name = "deepseek"
    async def stream_chat(self, messages): ...   # SSE 流式

class LLMRouter:
    @staticmethod
    def get(mode, cfg) -> LLMProvider:
        return DeepSeekProvider(api_key=cfg["deepseek_api_key"],
                                base_url=cfg["deepseek_base_url"],
                                model=cfg["deepseek_model"])
```

> 关键点：LLM 层**必须从 DB 读配置**（设置页改模型/填 Key 即时生效）。
> 文本解析/检索始终本地；只有 chat / 题目生成 / 章节 LLM 兜底会调用云端。

## 6. 任务与并发

- 后台任务用**独立后台线程 + 独立事件循环**（`worker/tasks.py`）：`run_coroutine_threadsafe` 提交 + `asyncio.wrap_future` 等待（sync 端点线程池陷阱的修复）
- 长任务（解析、批量生成）串行执行（FIFO 队列），避免并发耗尽内存

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
| 扫描版 PDF 无文本层 | 检测文本量阈值，提示启用 OCR（P1） |
| 云端 API 不可用/Key 失效 | 首次引导配置 + 设置页探测；错误提示清晰 |
| pro 模型响应慢 | flash/pro 可切换，日常问答用 flash |
| 向量化耗时 | 后台任务 + 进度条；默认关闭省内存 |
| FTS5 中文分词 | jieba 分词后建索引，搜索时同样分词 |
| 大 PDF 内存占用 | 流式读取、按页处理，控制 chunk 大小 |
| 知识树误删 | 删除需确认，子树级联删除有提示 |