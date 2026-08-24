# 项目交接文档 · Study assistant（学习助手）

> **用途**：供新对话/新协作者快速接管项目。阅读本文件 + 启动项目即可继续开发。
> **最后更新**：Nature 文献工作流本地化（结构补缺/source map/证据卡片/归档阅读/中文 PPTX/合法全文入口）+ 阅读工作台 UI 重构之后

---

## 1. 项目一句话

本地部署的个人学习辅助软件：把 PDF/Word/PPT 教材转化为「AI 问答 + 知识树 + 知识图谱 + AI 绘图 + 自测刷题 + 深度分析 + AI 研读 + 学习计划」的完整学习闭环。

**原始文件本地保存；启用 AI 功能时，问答/深度分析/出题/研读/知识树/视觉分析会向 DeepSeek / Qwen-VL 发送相关片段或图像（详见 PRIVACY.md）。** 知识图谱、学习计划、批注回流均为**纯本地**，不联网。

## 1.5 产品定位与开发主线（新协作者必读）

**一句话定位**：本地优先的个人知识库 + AI 助手。用户把教材/资料（PDF/Word/PPT）导入后，系统自动建立**可检索、可问答、可复习**的个人知识库，AI 基于库内资料回答问题，所有数据留在本机。

**核心主线（按优先级）**：
1. **知识库构建**（地基）：资料导入 → 解析 → 清洗 → 目录识别 → 切块 → 索引（FTS+向量）→ 分类/标签 → 去重
2. **知识库检索**（能力）：跨资料全文搜索 + 语义召回 + 重排 + 出处定位（文件/章节/页码）
3. **AI 接入**（增值）：问答（RAG）、深度分析（精读总结）、出题、研读报告、AI 绘图——全部基于知识库内容，云端只收提问+检索片段
4. **复习闭环**（留存）：知识树、知识图谱、刷题、学习计划、统计——把知识库变成主动复习工具

**当前完成度**：1/2 已扎实（OCR 支持扫描版、目录识别含中英文样式、FTS+RRF 混合检索、页码定位精确）；3 已打通（DeepSeek 问答/总结/出题/绘图 + Qwen-VL 视觉）；4 已具备基础（知识树/图谱/刷题/计划/统计）；文献工作台已支持合法全文接入与中文 PPTX 汇报。

**后续优化方向**：检索效果评测（引用核验已埋点）；增量更新与失败重试（任务已持久化）；大库性能（SQLite 迁移策略）；Element Plus 进一步按需导入。

### 第一阶段可靠性收口（2026-08-24）

- 删除资料改为显式外键顺序的集合操作，覆盖导入任务、答题记录、知识节点跨书引用、汇报文件和标签关系；数据库提交后才删除磁盘文件。
- 重解析只清除可再生内容，保留笔记、题目和知识节点并解除旧章节 ID，避免外键失败和用户内容误删。
- 版本状态迁入独立 `app_metadata` 键值表，FTS 版本可稳定回读；FTS 重建按 250 条流式读取、批量写入，不再一次加载全部 chunks。
- SQLite 备份改用在线 Backup API（每批 256 页）并执行完整性检查，兼容 WAL；恢复采用校验后的临时库原子替换。
- 上传文件以 1 MB 固定缓冲边读边写并同步计算 SHA-256，不再拼接完整文件；3 MB 回归样本的新增峰值被约束在 4 MB 内。
- AI 绘图 SVG 同时采用颜色白名单、XML 完整转义和 DOMPurify SVG profile；测试/CI 已统一为标准命令，并覆盖 Windows/Python 3.14 与 Node 22。

### 第二阶段知识库体验重构（2026-08-24）

- 产品信息架构改为“文献知识库优先”：文献知识库、文献工作台、知识库问答、知识树、知识图谱和综合研读构成主导航，刷题/绘图/计划/统计收纳为更多工具。
- 应用侧栏支持折叠和移动端抽屉，页头显示当前知识工作语境；古籍节气色彩、字体和背景风格保持不变。
- 新增持久化全局任务中心，统一显示文献导入/OCR、重新解析、结构精读和 PPTX 生成；任务按 FIFO 串行，页面隐藏时停止前端轮询。
- 上传、精读和 PPTX 提交后不再锁定页面等待，可继续阅读与检索；已结束任务会释放执行闭包，内存仅保留最近 128 条，历史从 SQLite 读取。
- 资料详情改为抽屉，移动端使用文献卡片；阅读器新增“围绕本文提问”，选段可直接带入 PPTX 工作台。
- PPTX 工作台形成“选择证据—设置语境—后台生成”三步路径，开放获取导入也进入统一任务中心。
- 全部页面改为路由懒加载，并拆分 Element Plus、ECharts、PDF.js 和内容渲染依赖；浏览器实测知识库首屏 JavaScript 由约 2.94 MB 降至 1.21 MB，PDF.js 仅在打开原文时加载。
- 卡片视觉令牌统一为 `border-gray-200`（`#E5E7EB`）与 shadow-sm；覆盖 Element Plus 卡片以及任务、来源、批注、设置等自定义卡片。按钮统一提供 hover 上移、active 回落缩放、disabled 静止反馈，并支持 `prefers-reduced-motion`。

**体验目标**：本地部署（无网可用核心功能）、流畅（解析/检索毫秒级、大任务后台化）、功能完备（学-练-测-复盘全流程）。

**启动方式（2026-08 更新）**：桌面「启动学习助手.vbs」一键启动（无窗口、后台常驻、自动开浏览器）；auto_start.ps1 / auto_stop.ps1 为底层脚本。
> 注意：**不设开机自启**（用户要求手动启动）。

---
## 2. 位置与环境

| 项 | 值 |
|---|---|
| 项目根目录 | `D:/86153/Documents/study-assistant` |
| 后端 | Python 3.14 + FastAPI + SQLAlchemy + SQLite（WAL）+ cryptography（Key 加密） |
| 前端 | Vue3 + Vite 6 + Element Plus + ECharts 6 + pdf.js + marked + DOMPurify（hash 路由，构建产物由 FastAPI 托管） |
| 虚拟环境 | 项目内 `.venv`（已装全部依赖，含 cryptography） |
| 启动 | 双击 `start.bat` 或 `python launcher.py [端口]`；停止 `stop.bat`（PID 文件 + 归属校验） |
| 前端开发 | `cd frontend && npm run dev`（5173，代理 /api 到 8000）；改完 `npm run build` 后重启后端 |
| 数据 | `backend/data/`（study.db + uploads/ + chroma/ + models/ + .secret_key），备份=复制该目录或 `backup.bat` |
| API Key | `.env`（DeepSeek + Qwen-VL，git 忽略）；设置页写入的 Key 加密存 DB（Fernet） |
| git / GitHub | 分支 `main`，远程 `git@github.com:boway033-cell/Study-assistant.git`，已开源（MIT） |

## 3. 已实现功能（全部可用）

### 3.1 资料管理（全本地分析）
- PDF/DOCX/PPTX 上传、解析、扫描件自动检测（仅 PDF 触发 OCR）
- **上传安全**：流式读取限 200MB、PDF 签名 / Office ZIP 魔数校验、压缩炸弹检查（解压后 ≤500MB）
- 章节树：书签 → 启发式「第X章」（不再导入时自动调云端 LLM，隐私）
- 版面分析（字体字号坐标法，零模型内存）、文本清洗、智能分析（定义句/定理/关键词）
- 中文全文搜索（jieba + FTS5，BM25）、文献自动分类（AI 归类，可手动改）

### 3.2 AI 问答（DeepSeek 云端）
- flash（deepseek-v4-flash）/ pro（deepseek-v4-pro）切换；四层降级检索（向量→FTS→LIKE→目录）
- SSE 流式、答案带出处页码、右侧原文面板（pdf.js）、首次使用引导

### 3.3 知识树（大纲 + 思维导图双视图）
- 双视图（大纲编辑/拖拽 ⇄ SVG 导图/缩放/导出图片）、手动/章节导入/AI 生成/AI 展开
- 节点：类型标记、掌握度（🟢🟡🔴）、Markdown 笔记、关联教材章节、跨树引用、AI 批改笔记、批量删除
- 右侧展示关联原文 + 关联批注

### 3.4 知识图谱 / 双向链接（**纯本地**）
- 自动抽概念（关键词 + 定理名，过滤停用词）→ ECharts 力导向全局图谱 → 点概念反查所有出处（本地 FTS5）
- 批注自动回流知识树：创建批注时自动建知识树节点并关联（`knowledge_node_id` 回填）
- 后端 `api/graph.py`（`GET /api/graph`、`GET /api/graph/concept/{name}/sources`），前端 `GraphView.vue`

### 3.5 深度分析管线（标题目录 + 精读 + Markdown）
- 三级标题目录提取 → 核对编号 → AI 补全 → 逐章精读 → Markdown 存库
- **由用户手动触发**（导入不再自动云端分析；LLM 目录补全在深度分析内手动触发）

### 3.6 AI 研读（综合阅读 + 思维训练）
- 综合阅读报告（主题脉络/文献定位/交叉知识点/思维题/学习路径，可存档删除）
- 思维训练：出题训练（苏格拉底式批改追问）或自由陪练（**轮数不限**，用户主动结束）

### 3.7 学习计划与目标（**纯本地**）
- 设定考试日期 → 按掌握度（弱→强）倒推每日任务 → 打卡日历
- 后端 `api/plan.py` + `StudyPlan`/`CheckIn` 模型，前端 `PlanView.vue`

### 3.8 PDF 阅读器（本地渲染 + AI 可选）
- 连续/单页/双页、适应宽度、目录跳转、深色、位置记忆、键盘翻页
- 四色高亮 + 批注卡片（笔记/挂知识树/导出）、AI 选中解释翻译/总结本章/Qwen-VL 视觉解读

### 3.9 Word/PPT 阅读器（DocReader）
- 章节树 + 正文渲染、批注、目录编辑、选中 AI 翻译/询问

### 3.10 刷题与统计
- AI 生成题目、自动判分 + 简答自评、错题本、掌握度、薄弱章节排行、作答趋势

### 3.11 文献归档与证据型阅读（2026-08 新增）
- 本地提取作者、期刊、年份、DOI、arXiv ID 和语言；保存阅读状态、收藏、评分与阅读进度
- 为每个 chunk 建立稳定 source ID，并映射章节与 PDF 页码
- 目录融合 PDF 书签、全文编号与版面字号三类来源，支持同页多标题及“一、/（一）/1.1/1.1.1/英文 section”
- 深度分析对缺号和扁平目录执行 AI 复核，只允许从带页码的原文候选补缺
- 固定 01–16 节 Paper Card，区分作者陈述、AI 分析和研究假设，并执行来源审计
- 阅读器统一为“原版阅读 / 结构精读 / 证据卡片”，资料库增加归档筛选和状态管理
- 设计说明：`docs/nature-literature-workflow.md`

### 3.12 文献工作台（2026-08 新增）
- 可选整篇、章节或用户选段，先按发现/方法/资源/临床/材料/综述六类路由，再按“主张—证据—边界”生成中文可编辑 PPTX
- PPTX 默认 16:9，事实页写入 chunk/page 来源及演讲者备注；可提取来源页主要内嵌图像，并执行页数、越界、文字密度和来源审计
- 无 DeepSeek Key 时使用本地证据提纲，不虚构研究结果；有 Key 时只发送选中来源片段生成证据链
- 支持开放获取地址、arXiv、Unpaywall DOI 解析，以及学校图书馆/CARSI 的当前 Chrome 交接入口
- 补充材料必须显式选择；不绕过付费墙/DRM/2FA，不读取 Cookie、密码、localStorage 或 Chrome 会话文件
- Provider registry、来源 manifest、PaperProfile provenance 和独立获取记录表为知识库来源扩展预留稳定接口

### 3.13 主题（古籍学术风）
- 深青灰绿底 `#2A3B3D`、纸色卡片 `#F5F0E8`、棕褐强调 `#8B5A2B`、暗青侧边栏 `#2E4042`
- 侧边栏/页头动态展示「节气名 + 年月日」（`frontend/src/utils/solarTerm.js`）

### 3.14 轻量文档解析链（2026-08-24 新增）
- PDF 优先保留块、行、坐标和字体证据（PDFText，失败自动回退 PyMuPDF），原子保存为 `data/structured/<file_hash>.json`
- RapidOCR/ONNX 仅识别弱文本页，缓存判断先于页面渲染，PDF 位图按页生成并立即释放，不再整本驻留内存
- Markdown 从结构证据恢复段落、跨页断句、标题、列表、表格与图注，不能把 OCR/PDF 每行直接输出为一段
- 目录候选按书签、编号语义、版面、句式负证据评分；AI 只能引用候选 ID 并调整层级，不能新增或改写标题
- PP-DocLayout-M 为显式可选增强：直接调用 ONNX `LayoutDetection` 单模块，子进程临时启动、至少 3 GB 可用内存，只给原文块增加角色；不启动 PP-StructureV3，不可用时回退内置分析
- MinerU 不进入标准安装和默认导入链；部署决策和资源边界见 `docs/LIGHTWEIGHT_DOCUMENT_PIPELINE.md`

## 4. 技术架构速览

```
浏览器 ← HTTP/SSE → FastAPI
  ├─ api/        books/chat/knowledge/graph/plan/study/deep/quizzes/stats/settings/annotations/ai
  ├─ services/
  │   ├─ parser/       PDF/DOCX/PPTX + OCR
  │   ├─ analyzer/     layout/keyinfo/textclean
  │   ├─ rag/          chunker/semantic_chunker/fts/retriever/vector/toc_*
  │   ├─ llm/          DeepSeekProvider(flash/pro) + parse_json_response + crypto 解密
  │   ├─ vision.py     QwenVLProvider(视觉分析) + crypto 解密
  │   └─ deep_analysis.py  四级标题/连续性核对/证据约束补全/逐章总结/Markdown
  ├─ core/        config.py / database.py / crypto.py(Fernet Key 加密)
  ├─ worker/      tasks.py(FIFO 串行) + import_task.py(导入流水线；无自动云端)
  └─ models/      books/chapters/chunks/quizzes/attempts/annotations/knowledge_nodes/
                  book_deep/study_reports/settings/book_analysis/study_plans/check_ins
```

## 5. 第三阶段专项修复（2026-08-24）

- **知识来源隔离**：知识图谱必须传 `book_ids`；知识树导入与 AI 生成支持多选，但逐本建立独立根树；AI 绘图仅注入用户所选书目的摘要上下文。图谱出处查询继续沿用同一书目范围。
- **《行政管理学夏书章》回归样本**：目录从旧深度分析的 36 个残缺条目恢复为 524 个四级标题候选，层级为“章 → 节 → 一、 →（一）”。处理汉字间异常空格、跨行标题、错误“目录”书签容器、截断标题与书签层级压平。
- **连续性审计**：除章号和 `1.1` 外，新增每章“第 N 节”、中文“一/二/三”及“（一）（二）”的父级范围内连续性检查；AI 仅能补入可回查原文的标题。
- **Markdown/索引去重**：页面不再同时写入父章、子节和小标题的重叠区间；每页只进入一次检索切片。Markdown 同页按标题行切段，无法定位时才回退到最近标题。
- **PDF 阅读器**：双页改为封面单页、随后 `2–3 / 4–5` 横向跨页；修复上一页方向、虚拟滚动偏移与模式切换。单页/双页仅保留当前页面 DOM，页面尺寸按需读取，不再启动时预取整本页面对象。
- **稳定入口**：VBS 漏反斜杠和固定绝对路径已修复；BAT/VBS/PowerShell 均改用脚本自身目录。启动器以 `/api/health` 判定就绪，8000 被其他程序占用时在 8001–8010 选择可用端口，未就绪不打开拒绝连接页面。
- **自定义协议（已注册）**：当前用户已注册 `study-assistant://open`；支持如 `study-assistant://open/reader/6?page=21` 的白名单深链。WMI 启动独立 `pythonw + server_runner.py`，协议处理器退出后服务仍存活；`uninstall_protocol.ps1` 可撤销。
- **真实书目已重解析**：《行政管理学夏书章》（book_id=6）已于 2026-08-24 重建为 529 个章节节点（16/55/141/317 四级分布）和 416 个非重叠文本块；旧 `book_deep` 已失效清除。回退快照：`backend/data/backups/before_reparse_book6_20260824_153812.db`。
- **解析引擎决策**：MinerU 不进入标准安装或默认导入链；默认使用轻量结构化解析、弱页按需 OCR 与可选 PP-DocLayout-M 子进程，详见 `docs/LIGHTWEIGHT_DOCUMENT_PIPELINE.md`。
- **验证**：后端 78 项测试通过；前端生产构建与 5 项单测通过（含 SVG 安全与主题视觉令牌）；8010 实启后 `/api/health`、书目范围图谱、书目范围知识树和静态首页均返回 200。
- **全库目录重解析（2026-08-24）**：10 本全部 `ready`，目录父子异常为 0；修复“第三部门/第三部”“第一部分”断词、层级断档、教材章名页眉重复、论文作者/页码/统计量误识别。全库回退快照为 `backend/data/backups/before_full_toc_reparse_20260824_155547.db`。

## 6. 安全与隐私（本轮重点加固）

| 项 | 措施 |
|---|---|
| XSS | 所有 Markdown/HTML 经 DOMPurify 消毒（`utils/markdown.js` 的 renderMarkdown/sanitizeHtml） |
| 本地 API 访问控制 | CORS 白名单 + Origin 校验中间件（非本地 Origin 的 /api/* 返回 403） |
| API Key 存储 | Fernet 加密（`core/crypto.py`），密钥 `.env SECRET_KEY` 或 `data/.secret_key`；旧明文向后兼容 |
| 上传安全 | 流式限大小 + PDF/ZIP 签名校验 + 压缩炸弹检查 |
| 后台队列 | 真 FIFO 串行 + 每任务独立 Session（quizzes/knowledge 已改自建 Session） |
| 停止服务 | `stop.bat` PID 文件 + 命令行归属校验（防误杀） |
| 信息暴露 | `/api/health` 只返回 status（不暴露 db 路径） |
| 隐私文档 | PRIVACY.md（数据流向表）、SECURITY.md（威胁模型+边界） |

**已知安全边界**（SECURITY.md 已声明）：本地单用户工具，不防「同权限本机程序」（它们可直接读 SQLite/内存）；未做登录鉴权，勿改绑 `0.0.0.0` 暴露公网。

## 7. 测试情况

| 测试 | 位置 | 结果 |
|---|---|---|
| 单元/架构测试 | `backend/tests/`（含 reliability_phase1 + literature_workbench） | 78 项全过（新增弱页 OCR、缓存前置、单页惰性渲染、OCR 任务后释放、结构化 Markdown、目录/AI 证据硬约束、PP-DocLayout-M 单模块与原文保护） |
| 前端单测 | `frontend/tests/` | 5 项全过（SVG 颜色属性注入、XML 转义、卡片视觉令牌与按钮交互反馈） |
| UI 测试 | `backend/tests/ui_test.py`（Playwright） | 18 项全过（需先起后端） |
| CI | `.github/workflows/ci.yml` | push/PR 自动跑单测 + 前端构建 |
| Release | `.github/workflows/release.yml` | 打 `v*` 标签自动 build 前端 + 打包 zip 上传 Release |

标准运行：`.venv/Scripts/python -m pytest -q`；前端运行：`cd frontend && npm run test:unit && npm run build`

## 8. 关键踩坑记录（新对话必读）

1. **FastAPI sync 端点线程池**：`asyncio.get_event_loop()` 崩溃 → 独立后台线程 + `run_coroutine_threadsafe`
2. **SQLite 锁冲突**：FTS 写入与 ORM 同事务冲突 → 先批量 commit 再写索引
3. **中文分词**：jieba 切碎术语 → `cut_for_search` + 查询 OR 连接 + 用户词典
4. **PDF 标题混入正文**：`merge_broken_chinese` 黏连标题 → 合并前检查上一行是否像标题
5. **章节提取排版变体**：`第 3 章 | 价值` 等 → 正则放宽
6. **目录页误判**：点线 `......` → 正则过滤 `[.．·]{4,}`
7. **模型下载被墙**：HuggingFace 不可达 → GCS URL 多线程 Range 下载，`HF_HUB_OFFLINE=1`
8. **删除书籍外键失败**：显式级联清理 annotations/book_deep/chat_logs/notes/knowledge_nodes/book_analysis
9. **Element Plus el-tag 拦截 @click**：关键词芯片用原生 `<span>`
10. **Vue 事件对象污染**：`@click="doSearch"` 传 PointerEvent → 显式 `doSearch()`
11. **SQLite 无 `iif` 误写 `iiif`**：stats 聚合报错 → 子查询 + 两个 count
12. **FastAPI 0.141 `_IncludedRouter`**：`app.routes` 显示 `_IncludedRouter` 无 .path，属正常
13. **AI prompt 元组 bug**：content 括号内字符串行尾误加逗号 → tuple → DeepSeek 400，知识树 AI 框架生成必失败
14. **自引用外键歧义**：knowledge_nodes 加 ref_node_id 后需显式 `foreign_keys="KnowledgeNode.parent_id"`，否则 `AmbiguousForeignKeysError`
15. **docx/pptx 误判扫描件**：`detect_scanned` 对短文本误判 → 仅 PDF 触发 OCR
16. **Vue 路由参数复用**：`/reader/7`→`/reader/1` 不重挂载 → watch route.params.bookId
17. **svgH 只按根节点算**：思维导图子树被裁切 → 用整棵树 totalH
18. **位置记忆覆盖跳转页**：知识树「去阅读 ?page=N」被 localStorage 覆盖 → `use-saved-pos` 属性
19. **CI 测试 no such table: fts_books**：FTS 虚拟表在 lifespan 才 init，CI 全新环境直接 pytest 报错 → `conftest.py` session fixture 里 `Base.metadata.create_all` + `fts.init_fts()`
20. **git 历史含敏感文件**：`backend/app/data/*.db` 曾提交过，公开前用 `git filter-branch --index-filter` 重写 + `reflog expire` + `gc --prune=now --aggressive`；filter-repo 未装时用 filter-branch
21. **PowerShell 执行策略**：`npm` 是 .ps1 被禁 → 用 `npm.cmd`；pwsh 5.1 里 `&&` 不是分隔符，用 `;`
22. **SSH key 写丢**：ssh-keygen 生成后文件没落盘导致 publickey 认证失败 → 生成后必须 `Test-Path` 验证，且公钥要与 GitHub 上添加的一致
23. **cryptography 加密存储**：旧明文 key 向后兼容（decrypt 对无 `enc:` 前缀原样返回）；用迁移脚本把旧明文加密
24. **vite 5→6 + echarts 5→6 升级**：breaking change 但项目配置简单，build 正常，echarts graph API 兼容；升级后 npm audit 归零
25. **上传流式读取**：`UploadFile.read()` 一次读入内存，改 `read(chunk)` 循环 + 魔数/压缩炸弹校验
26. **OCR 任务无进展卡死**：152 MB 扫描 PDF 曾停在缓存 `1/174` 约 53 分钟；重启后任务自动恢复。需补页级心跳、无进展超时和可取消检查点，不能只显示笼统“版面分析”。
27. **章名页眉污染目录**：教材隔页重复章名会生成数十个根节点；仅对有编号的一级章/部分标题做全书去重，普通按章重复的小结标题不可全局去重。

## 9. 已知边界

- OCR 后端（tesseract/paddle）代码就绪未安装，扫描版 PDF 会提示
- 向量检索默认关（省内存），开启需加载 fastembed 模型
- 思维训练会话为内存态（重启后端后会话丢失；上限 100 个，超了删最旧）
- 知识图谱概念来自 `book_analysis`（需资料做过智能分析才有节点）
- 深度分析/综合阅读/问答依赖 DeepSeek Key；视觉依赖 Qwen-VL Key
- 知识库首屏 JavaScript 实测约 1.21MB（解压后）；ECharts、PDF.js 与各工具页均按需加载，Element Plus 仍可继续做组件级导入
- PPTX 已支持主要内嵌图像提取；复杂多面板智能裁剪、矢量图重绘和低置信度公式原图保留仍待实现
- DOI/OA/arXiv 与馆藏浏览器交接已接入；出版社授权 API 仍作为 provider 扩展点，未实现也不会模拟绕过
- Paper Card 与 PPTX 导出已接入；周期性文献订阅尚未实现

## 10. 开源发布状态

- 仓库：https://github.com/boway033-cell/Study-assistant（分支 main，tag v1.0.0）
- 许可证 MIT、PRIVACY.md、SECURITY.md、CHANGELOG.md、CONTRIBUTING.md、.gitattributes
- CI（ci.yml）+ Release 自动打包（release.yml）
- 分享给朋友：下载 Release 的 zip（含前端产物，不装 Node 也能用），或 git clone 后 `cd frontend && npm i && npm run build`

## 11. 快速上手命令

```bash
cd D:/86153/Documents/study-assistant
.venv/Scripts/python launcher.py          # 启动（自动开浏览器）
.venv/Scripts/python -m uvicorn backend.app.main:app --port 8000  # 直接启动

cd frontend && npm run build               # 改完前端构建（需 npm.cmd）
.venv/Scripts/python -m pytest backend/tests/test_unit.py backend/tests/test_enhance.py backend/tests/test_semantic.py backend/tests/test_toc_heuristic.py -q  # 测试

# git 推送
git push origin main
git tag v1.1.0 && git push --tags        # 触发 Release 自动打包

# 关键文档
docs/README.md  docs/产品文档.md  docs/PROJECT_HANDOVER.md
docs/LIGHTWEIGHT_DOCUMENT_PIPELINE.md  docs/nature-literature-workflow.md
docs/01-architecture.md  docs/02-database.md  docs/03-api.md
PRIVACY.md  SECURITY.md  CHANGELOG.md  CONTRIBUTING.md  README.md
```
