# 项目交接文档 · 保研复习助手（Study Assistant）

> **用途**：供新对话/新协作者快速接管项目。阅读本文件 + 启动项目即可继续开发。
> 最后更新：白露主题完成之后

---

## 1. 项目一句话

本地部署的个人学习辅助软件：把专业课 PDF/Word 教材转化为「AI 问答 + 卡片背诵 + 自测刷题 + 掌握度统计」的完整学习闭环。核心思路：把「读资料」变成「主动回忆 + 自测 + 查漏补缺」。

## 2. 位置与环境

| 项 | 值 |
|---|---|
| 项目根目录 | `D:\86153\Documents\study-assistant` |
| 后端 | Python 3.14 + FastAPI + SQLAlchemy + SQLite（WAL） |
| 前端 | Vue3 + Vite + Element Plus + ECharts（hash 路由，构建产物由 FastAPI 托管） |
| 虚拟环境 | 项目内 `.venv`（已装全部依赖） |
| 启动 | 双击 `start.bat` 或 `cd 项目根 && .venv\Scripts\python -m uvicorn backend.app.main:app --port 8000`，访问 `http://127.0.0.1:8000` |
| 前端开发 | `cd frontend && npm run dev`（5173，代理 /api 到 8000）；改完 `npm run build` 后重启后端生效 |
| 数据 | `backend/data/`（study.db + uploads/ + chroma/ + models/），备份=复制该目录 |
| git | 已初始化为本地仓库，提交历史含各阶段里程碑 |

## 3. 已实现功能（全部可用）

### 3.1 资料管理
- PDF/DOCX/PPTX 上传、解析、**扫描件自动检测**（页均 <30 字符 → OCR 提示）
- **章节树**：书签 → 启发式「第X章」扫描 → LLM 三级兜底（真实教材 456 页无书签 → 17 章）
- **版面分析**：标题/正文/页眉页脚/表格/公式识别（字体字号坐标法，零模型内存）
- **文本清洗**：去页眉页脚、去重、重复字符压缩、中英文断行合并（标题保护）
- **智能分析**：定义句/定理/关键词提取 → 详情页展示（可点击关键词搜索）
- 中文全文搜索（jieba + SQLite FTS5，BM25 风格）

### 3.2 AI 问答（宽定位检索）
- **四层降级**：向量语义检索 → FTS 关键词 → LIKE 子串兜底 → 目录兜底（绝不"无法 fetch"）
- **章节级上下文**：命中 chunk 自动拉取同章节相邻 chunk，完整输出文献内容
- SSE 流式输出、答案带出处页码
- LLM 双模式：本地 Ollama ⇄ 云端 DeepSeek（**从数据库读配置**，设置页切换即时生效）

### 3.3 语义切块 + 向量检索
- **语义切块**（`semantic_chunker.py`）：段落边界切分，**每块保留页码映射**
- **向量检索**（`vector.py`）：fastembed（bge-small-zh-v1.5 ONNX，512 维，全离线）+ ChromaDB
- 混合检索：向量 + FTS + LIKE 去重合并（设置页 `vector_search` 开关，默认关）

### 3.4 卡片背诵
- FSRS 间隔重复（py-fsrs 6.x，Anki 同款算法）
- **规则式卡片生成**（无 LLM 也可用）：定义句/概念释义/重点句 + 噪声过滤
- LLM 生成接口保留（配好模型自动升级）
- 复习队列、每日新卡限量、评级（again/hard/good/easy）

### 3.5 刷题与统计
- 选择/填空/简答，自动判分 + 简答自评，错题本
- 掌握度热力图（卡片 + 答题加权）、复习曲线、薄弱章节排行

### 3.6 原文定位面板
- 搜索/问答结果 → 右侧抽屉：chunk 原文全文 + 页码区间 + PDF 原文 iframe（`#page=N` 定位）
- API：`/books/{id}/file`、`/books/{id}/chunk/{cid}`、`/books/{id}/page/{n}`

### 3.7 白露节气蓝白主题
- 天青主色 `#3e7fa3`、月白背景 `#f4f8fa`、凝露表头 `#eef5f8`、缥碧成功色 `#5f9b8f`
- 主题文件：`frontend/src/theme/bailu.css`（覆盖 Element Plus CSS 变量）
- 露珠 logo 动画、侧边栏晨雾渐变、"蒹葭苍苍·白露为霜"诗句、页头节气标语

## 4. 技术架构速览

```
浏览器 ← HTTP/SSE → FastAPI
  ├─ api/        books/chat/cards/quizzes/stats/settings 路由
  ├─ services/
  │   ├─ parser/       PDF/DOCX/PPTX 解析 + OCR 可插拔(tesseract/paddle)
  │   ├─ analyzer/     版面分析 layout / 关键信息 keyinfo / 文本清洗 textclean
  │   ├─ rag/          chunker(分词) / semantic_chunker(语义切块) / fts(FTS5)
  │   │                retriever(宽定位) / vector(fastembed+ChromaDB) / toc_heuristic / toc_llm
  │   ├─ llm/          OllamaProvider ⇄ DeepSeekProvider + load_llm_config(DB配置)
  │   ├─ srs/          fsrs_service(FSRS)
  │   └─ cards/        rule_cards(规则式卡片)
  ├─ worker/           tasks.py(独立后台线程+事件循环) / import_task.py(导入流水线)
  └─ models/           SQLAlchemy ORM(含 book_analysis 表)
```

## 5. 测试情况

| 测试 | 位置 | 结果 |
|---|---|---|
| 单元测试 | `backend/tests/test_unit.py` + `test_enhance.py` + `test_semantic.py` + `test_toc_heuristic.py` | 37 项全过 |
| UI 测试 | `backend/tests/ui_test.py`（Playwright 真实 Chromium） | 12 项全过（"错误提示显示"偶发超时=时序问题，重跑即过） |

运行：`.venv\Scripts\python -m pytest backend/tests/ -q`；UI 需先起后端。

## 6. 真实教材验证结果（《公共管理学》456 页）

- 17 章正确切分（无书签，启发式提取，标题精化干净）
- 797 个语义块（带页码映射）
- 301 张规则式卡片（噪声 <5%）
- 向量检索语义命中：问"数字政府的含义"→ 第14章数字政府；"绩效管理"→ 第12章
- 全流程耗时 ~75s（含向量化 60s，一次性）

## 7. 关键踩坑记录（新对话必读）

1. **FastAPI sync 端点在线程池**：`asyncio.get_event_loop()` 崩溃 → 任务系统用独立后台线程 + `run_coroutine_threadsafe` + `asyncio.wrap_future`
2. **SQLite 锁冲突**：FTS 写入与 ORM 同事务冲突 → 先批量 commit 再写索引
3. **中文分词**：jieba 切碎"拉格朗日中值定理" → 索引用 `cut_for_search` + 查询 OR 连接 + 专业术语词典
4. **PDF 标题混入正文**：`merge_broken_chinese` 会黏连标题行 → 合并前检查上一行是否像标题
5. **章节提取排版变体**：`第 3 章 | 公共管理的价值`（空格+竖线）、`第 4 章 公 共 管 理`（字距）→ 正则放宽
6. **目录页误判**：目录项带点线 `......` → 正则过滤 `[.．·]{4,}`
7. **模型下载被墙**：HuggingFace 不可达 → 多线程 Range 下载 GCS URL（`storage.googleapis.com/qdrant-fastembed/fast-bge-small-zh-v1.5.tar.gz`），解压到 `backend/data/models/fast-bge-small-zh-v1.5/`，设 `HF_HUB_OFFLINE=1`
8. **删除书籍外键失败**：需级联清理 book_analysis / 向量库 / FTS
9. **Element Plus el-tag 拦截 @click**：关键词芯片改用原生 `<span>`
10. **Vue 事件对象污染**：`@click="doSearch"` 会把 PointerEvent 传入 → 显式 `doSearch()`

## 8. 已知边界（未完成/待办）

- **LLM 未配置**：本地 Ollama 未安装、云端 DeepSeek Key 未填 → AI 问答返回引导提示、卡片走规则式。配置方法：设置页（DeepSeek 需注册 platform.deepseek.com 拿 Key；本地需装 Ollama 并 `ollama pull qwen2.5:3b`）
- **OCR 未安装后端**：扫描版 PDF 会提示安装 tesseract/paddle（代码已就绪）
- 章节提取对封面式排版（无"第X章"字样）的章节页仍会漏（真实教材漏第 18 章，因该章标题格式特殊）
- 向量检索默认关闭（省内存），需设置页手动开启
- UI 测试的"错误提示显示"偶发超时（时序问题）

## 9. 展望 / 下一步建议

1. **配置 AI**（最高优先）：填 DeepSeek Key 或装 Ollama → 问答真正可用、卡片升级 LLM 生成
2. **面试专项**：保研面试高频题库 + 模拟面试（AI 追问）
3. **OCR 落地**：装 tesseract 中文包或 PaddleOCR，扫描版教材可用
4. **卡片增强**：AI 生成卡片质量优化（去重、知识点覆盖评估）
5. **多书联动**：跨书综合提问、知识点图谱
6. **体验**：暗色模式、移动端适配、数据导出（Anki 兼容）

## 10. 快速上手命令

```bash
# 启动
cd D:\86153\Documents\study-assistant
.venv\Scripts\python -m uvicorn backend.app.main:app --port 8000

# 前端构建（改完 vue 后）
cd frontend && npm run build

# 测试
.venv\Scripts\python -m pytest backend/tests/ -q

# 关键文档
docs/00-PRD.md  docs/01-architecture.md  docs/02-database.md
docs/03-api.md  docs/04-roadmap.md  docs/experience-pdf-analysis.md（方法论经验）
```
