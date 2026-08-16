# 项目交接文档 · Study assistant（学习助手）

> **用途**：供新对话/新协作者快速接管项目。阅读本文件 + 启动项目即可继续开发。
> 最后更新：古籍学术风主题 + 知识树完整版 + 深度分析 + AI 研读 之后

---

## 1. 项目一句话

本地部署的个人学习辅助软件：把 PDF/Word/PPT 教材转化为「AI 问答 + 知识树 + 自测刷题 + 深度分析 + AI 研读」的完整学习闭环。
**文本解析/切块/检索 100% 本地完成；仅「提问 + 检索片段」发送到 DeepSeek/Qwen 云端生成回答。**

## 2. 位置与环境

| 项 | 值 |
|---|---|
| 项目根目录 | `D:/86153/Documents/study-assistant` |
| 后端 | Python 3.14 + FastAPI + SQLAlchemy + SQLite（WAL） |
| 前端 | Vue3 + Vite + Element Plus + ECharts + pdf.js + marked（hash 路由，构建产物由 FastAPI 托管） |
| 虚拟环境 | 项目内 `.venv`（已装全部依赖） |
| 启动 | 双击 `start.bat` 或 `python launcher.py [端口]`；停止 `stop.bat` |
| 前端开发 | `cd frontend && npm run dev`（5173，代理 /api 到 8000）；改完 `npm run build` 后重启后端 |
| 数据 | `backend/data/`（study.db + uploads/ + chroma/ + models/），备份=复制该目录 |
| API Key | `.env`（DeepSeek + Qwen-VL，git 已忽略，仅本机） |
| git | 已初始化本地仓库，提交历史含各阶段里程碑 |

## 3. 已实现功能（全部可用）

### 3.1 资料管理（全本地分析）
- PDF/DOCX/PPTX 上传、解析、扫描件自动检测（仅 PDF 触发 OCR）
- 章节树：书签 → 启发式「第X章」→ LLM 三级兜底（456 页教材无书签 → 18 章）
- 版面分析（字体字号坐标法，零模型内存）、文本清洗（页眉页脚/去重/断行合并）
- 智能分析：定义句/定理/关键词提取 → 详情页展示
- 中文全文搜索（jieba + FTS5，BM25）
- **文献自动分类**：AI 分析主题归类（数学/管理学/…），资料库分组、可手动改

### 3.2 AI 问答（DeepSeek 云端）
- 模型档位：flash（deepseek-v4-flash）/ pro（deepseek-v4-pro），设置页+问答页切换
- 四层降级检索：向量 → FTS → LIKE → 目录兜底
- SSE 流式输出、答案带出处页码、右侧原文面板（pdf.js）
- 首次使用未配置 Key 时弹窗引导

### 3.3 知识树（大纲 + 思维导图双视图，完整版）
- 双视图：大纲树（编辑/拖拽/多选）⇄ 思维导图（SVG，缩放/平移/滚轮/导出图片）
- 建树：手动 / 从章节导入 / AI 生成框架 / **AI 展开子节点**
- 节点：类型标记（概念📘/定理📐/考点🎯/例题📝/疑问❓）、掌握度（🟢🟡🔴）、Markdown 笔记、关联教材章节、**跨树引用**
- **AI 批改节点笔记**（评价/建议/优化笔记）、批量删除（多选）、导图按掌握度着色、节点搜索、掌握度统计条
- 右侧展示关联原文（文本/pdf.js）+ 关联批注

### 3.4 深度分析管线（标题目录 + 精读 + Markdown）
- 导入后自动异步执行：三级标题目录提取（章/节/小节）→ 核对编号连续性 → AI 补全缺失 → AI 逐章精读总结 → 转 Markdown 存库
- 阅读器可切「📝 Markdown 精读版」；可手动重跑

### 3.5 AI 研读（综合阅读 + 思维训练）
- **综合阅读报告**：通读所选文献 → 主题脉络/文献定位/交叉知识点/思维题/学习路径（Markdown 渲染，可存档/删除）
- **思维训练**：出题训练（苏格拉底式批改+追问）或自由陪练，6 轮后总结

### 3.6 PDF 阅读器（本地渲染 + AI 可选）
- 连续/单页/双页三模式、自动适应宽度、目录跳转、深色、位置记忆、键盘翻页
- 四色高亮 + 批注卡片（笔记/挂知识树/导出 Markdown）
- AI：选中解释/翻译（可存为批注）、总结本章、**解读本页（Qwen-VL 视觉分析）**

### 3.7 Word/PPT 阅读器（DocReader）
- 章节树 + 正文渲染、字号/深色、页内搜索
- **批注**（选中→标注）、**目录编辑**（改名）、**选中 AI 翻译/询问**

### 3.8 刷题与统计
- AI 生成题目（选书/选章）、自动判分 + 简答自评、错题本、清除本书题目
- 掌握度基于作答、薄弱章节排行、作答趋势

### 3.9 主题（古籍学术风）
- 深青灰绿底 `#2A3B3D`、纸色卡片 `#F5F0E8`、棕褐强调 `#8B5A2B`、暗青侧边栏 `#2E4042`
- 背景参考图 `frontend/src/assets/bg.jpg`（古籍做旧纹理）
- 24 节气 + 三候 + 诗词（`frontend/src/utils/solarTerm.js`），侧边栏/页头动态展示节气与年月日

## 4. 技术架构速览

```
浏览器 ← HTTP/SSE → FastAPI
  ├─ api/        books/chat/knowledge/study/deep/quizzes/stats/settings/annotations/ai
  ├─ services/
  │   ├─ parser/       PDF/DOCX/PPTX + OCR
  │   ├─ analyzer/     layout/keyinfo/textclean
  │   ├─ rag/          chunker/semantic_chunker/fts/retriever/vector/toc_*
  │   ├─ llm/          DeepSeekProvider(flash/pro) + parse_json_response
  │   ├─ vision.py     QwenVLProvider(视觉分析)
  │   └─ deep_analysis.py  三级标题/核对/补全/逐章总结/Markdown
  ├─ worker/           tasks.py + import_task.py(导入流水线,自动触发深度分析)
  └─ models/           books/chapters/chunks/quizzes/annotations/knowledge_nodes/book_deep/study_reports/...
```

## 5. 测试情况

| 测试 | 位置 | 结果 |
|---|---|---|
| 单元测试 | `backend/tests/`（test_unit/test_enhance/test_semantic/test_toc_heuristic） | 34 项全过 |
| UI 测试 | `backend/tests/ui_test.py`（Playwright） | 18 项全过 |

运行：`.venv/Scripts/python -m pytest backend/tests/ -q`；UI 需先起后端。

## 6. 关键踩坑记录（新对话必读）

1. **FastAPI sync 端点线程池**：`asyncio.get_event_loop()` 崩溃 → 独立后台线程 + `run_coroutine_threadsafe`
2. **SQLite 锁冲突**：FTS 写入与 ORM 同事务冲突 → 先批量 commit 再写索引
3. **中文分词**：jieba 切碎术语 → `cut_for_search` + 查询 OR 连接 + 用户词典
4. **PDF 标题混入正文**：`merge_broken_chinese` 黏连标题 → 合并前检查上一行是否像标题
5. **章节提取排版变体**：`第 3 章 | 价值`、`第 4 章 公 共 管 理` → 正则放宽
6. **目录页误判**：点线 `......` → 正则过滤 `[.．·]{4,}`
7. **模型下载被墙**：HuggingFace 不可达 → GCS URL 多线程 Range 下载 fastembed，`HF_HUB_OFFLINE=1`
8. **删除书籍外键失败**：需显式级联清理 annotations/book_deep/chat_logs/notes/knowledge_nodes/book_analysis
9. **Element Plus el-tag 拦截 @click**：关键词芯片用原生 `<span>`
10. **Vue 事件对象污染**：`@click="doSearch"` 传 PointerEvent → 显式 `doSearch()`
11. **SQLite 无 `iif` 误写 `iiif`**：stats 聚合报错 → 子查询 + 两个 count
12. **FastAPI 0.141 `_IncludedRouter`**：`app.routes` 显示 `_IncludedRouter` 无 .path，属正常
13. **AI prompt 元组 bug**：content 括号内字符串行尾误加逗号 → 变成 tuple → DeepSeek 400 invalid type，知识树 AI 框架生成必失败
14. **自引用外键歧义**：knowledge_nodes 加 ref_node_id 后 parent/children 关系需显式 `foreign_keys="KnowledgeNode.parent_id"`，否则 `AmbiguousForeignKeysError`
15. **docx/pptx 误判扫描件**：`detect_scanned` 对 docx 短文本误判 → 仅 PDF 触发 OCR
16. **Vue 路由参数复用**：`/reader/7`→`/reader/1` 组件不重挂载 → watch route.params.bookId
17. **svgH 只按根节点算**：思维导图子树被裁切 → 用整棵树 totalH
18. **位置记忆覆盖跳转页**：知识树「去阅读 ?page=N」被 localStorage 覆盖 → `use-saved-pos` 属性

## 7. 已知边界

- OCR 后端（tesseract/paddle）代码就绪未安装，扫描版 PDF 会提示
- 向量检索默认关（省内存），开启需加载 fastembed 模型
- 思维训练会话为内存态（重启后端后会话丢失）
- 深度分析/综合阅读依赖 DeepSeek Key（.env 已配置）

## 8. 快速上手命令

```bash
cd D:/86153/Documents/study-assistant
.venv/Scripts/python launcher.py          # 启动（自动开浏览器）
.venv/Scripts/python -m uvicorn backend.app.main:app --port 8000  # 直接启动

cd frontend && npm run build               # 改完前端构建
.venv/Scripts/python -m pytest backend/tests/ -q  # 测试

# 关键文档
docs/00-PRD.md  docs/01-architecture.md  docs/02-database.md
docs/03-api.md  docs/04-roadmap.md  docs/experience-pdf-analysis.md
docs/PROJECT_HANDOVER.md  docs/产品文档.md
```