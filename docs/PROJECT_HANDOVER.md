# 项目交接文档 · Study assistant（学习助手）

> **用途**：供新对话/新协作者快速接管项目。阅读本文件 + 启动项目即可继续开发。
> **最后更新**：设计系统收敛、核心工作区重排、资料可信字段、可取消 OCR 任务与 Windows 按需唤醒入口

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

### 第三阶段文献汇报与书架闭环（2026-08-24）

- 书架是层级化虚拟集合：资料可同时归入多个书架，支持子书架、未归档筛选与批量归档，不移动或复制原文件。
- PPTX 默认改为两阶段：先生成可编辑提纲，人工调整顺序、标题、主张、要点和来源后，再进入后台渲染。
- 证据选择采用章节分层取样，对章节或 SI 组别分配字符预算，报告内容覆盖率、结构覆盖率和各组覆盖。
- 每页主张执行来源一致性审计：检查 source ID、数字一致性、关键词重叠和页码/章节定位；不支持的主张需人工确认才允许继续。
- 补充材料与图表统一使用来源权利模型，记录资源角色、许可证表达、RightsStatements URI、权利状态、归属文字、授权说明与 provenance。
- 个人/内部使用可在确认风险后继续；公开/商业用途自动排除权利不明的图像。
- Windows 安装 PowerPoint 时，输出会由 PowerPoint COM 实际渲染为逐页 PNG，保存预览并与上一版做像素差异报告；未安装时明确标记“未完成视觉验收”。
- 前端路由改为 History 模式，FastAPI 静态托管提供 SPA fallback，`/literature-workbench` 与 `/reader/{id}?page=N` 可被网页/自定义协议直接打开。

### 第四阶段目录逻辑自修正与人工校正（2026-08-24）

- 目录置信度从“单条标题特征加分”改为全书逻辑审计：按父级上下文分组检查起始号、缺号、重号、倒序、小节前缀、页码和父子层级。
- 支持“章 → 节 → 一、 → （一）”和 `1 → 1.1 → 1.1.1` 等编号链；“一、”在没有“第X节”时可上下文化推导为二级，不再固定写死为三级。
- 新增同页阅读顺序回看：若后置“（三）”能唯一续接前一父项的“（一）（二）”，则回挂并前移；存在多个可能父项时不自动决定。
- 自动修正只改动有确定编号证据的层级、父级与同页顺序；不凭空创建缺失标题，不自动删除疑似正文。
- 阅读器新增“目录校正”：显示高/复核/低置信、具体问题、自修正数量，支持改标题、层级、页码、顺序、新增/删除节点和返回原页核对。
- 人工保存为单一事务，同步重建 chunk 章节归属、source map 和 FTS 冗余定位，使问答/PPTX/知识节点不继续引用旧目录。

### 第五阶段“我的资料”阅读导向布局（2026-08-24）

- 资料区从十列后台表格改为“文献与来源 / 阅读进度 / 知识加工 / 主操作”四段式条目，长题名与作者信息拥有稳定宽度。
- “继续阅读”成为阅读中资料的主操作；详情、生成汇报和删除收纳进次级菜单，减少横向按钮竞争。
- 书架名称、当前结果数与生效筛选在标题区集中反馈；支持一键清除筛选、全选当前结果和批量加入书架。
- 单篇导入、批量导入、智能归类重新分级；批量文件先进入确认队列，再交给全局任务中心处理。
- 沿用节气古籍视觉、gray-200 细边框和 shadow-sm；仅使用 CSS Grid 与已有 Element Plus 组件，不增加图表实例、常驻监听或额外运行内存。
- `/api/health` 带应用标识、API 修订号和关键能力；VBS、BAT、PowerShell、自定义协议入口不再复用缺少新路由的旧后端进程，避免新版前端请求旧 API 出现 404/405。
- `toc_revisions` 保存自动/人工修订前后快照和备注，便于追溯；数据层版本为 6。
- 《行政管理学夏书章》已应用 16 项无歧义修正，主要是同页子项回挂；529 项中高置信 518 项，剩余 8 个非确定问题保留给人工核对。

### 第六阶段全局 UI 分阶段重排（2026-08-26，第一批）

- 采用“全局统一、分阶段重排”，不一次性推翻业务页面；第一批覆盖设计令牌、应用壳、PDF 阅读器、资料库和文献工作台基础骨架。
- `bailu.css` 新增界面黑体与长文宋体、12/13/14/16/20px 字号层级、8px 间距体系、语义纸张/文字表面与统一页面 Hero；不增加运行时依赖或常驻状态。
- 阅读器文献头拆成“题名与主要研究动作 / 阅读视图与状态”两层；长题名最多两行，断点从 1050px 提前到 1280px，阅读路由的全局页头压缩为 44px。
- PDF 目录改为默认展开的 250–320px 文档导航，显示层级、页码、条目数和当前章节；空目录不再隐藏入口，并可直达目录结构工作台。
- PDF 工具栏不再自由换行，按视图、页码、显示与研究动作分组；中等窗口收纳研究动作和次级适配操作，避免遮挡正文。
- 1065px 实际浏览器回归中，长题名显示为两行、12 项目录默认可见、正文无页面级横向溢出且控制台无错误；1440/1065/800px 均完成结构检查。

### 第七阶段资料库迁移（2026-08-26）

- 资料库 Hero 收紧为页面命令区，集中显示资料总数、阅读中与待处理数量，以及全文检索、批量导入和单篇导入三个明确动作。
- 左侧增加“全部资料 / 阅读中 / 我的收藏 / 待处理 / 未归档”智能视图，自建书架继续作为稳定虚拟归档，不移动或复制原文件。
- 中间保留阅读导向文献条目，但点击后进入资料选择状态；大屏右侧按需加载资料检查器，中小屏使用原有完整详情抽屉。
- 资料检查器显示归档元数据、阅读与解析状态、顶级目录预览和关键词，可进入阅读、生成汇报、全文检索或编辑完整档案。
- 为控制内存，不在首屏自动请求任一文献详情；用户选择后只保留一份当前详情，检查器最多渲染 12 个顶级章节，完整目录仅在详情抽屉中按需呈现。
- 全文检索卡片改为按需展开；关键词会打开检索区并发起搜索。导入、分类、检索、深度分析和空状态文案均给出明确结果与可执行下一步。
- 设计与语言规则参考 Vercel `web-interface-guidelines` 和 OpenAI `frontend-app-builder` 的公开规范：真实工作界面优先、复用设计令牌、具体按钮标签、错误提示说明恢复路径；未安装远程 skill，也未增加前端或运行时依赖。
- 后续 AI 页面迁移统一遵循：先给结论、区分原文事实与 AI 推断、保留来源定位、资料不足时明确缺口、错误与拒绝都提供安全的下一步；DeepSeek 默认路由不变。

### 第八阶段知识沉淀闭环与目录链修正（2026-08-26）

- `annotations`、`knowledge_notes`、`evidence_cards` 与 `knowledge_nodes` 分表保存，只通过 `/api/knowledge/records` 读模型聚合；旧 `node_type=note` 内容会幂等复制到独立知识笔记，原树节点不自动删除。
- PDF 标注新增 `mark_type=highlight|underline` 与 `origin=user|ai`。选中文字后可一键高亮、划线或进入可选批注卡片；创建标注不再自动创建知识树节点。
- 知识沉淀首页支持书目、章节、记录类型、颜色、标签、时间与全文筛选；来源回链为强制输出字段。高亮提升笔记、笔记加入知识树、证据组合主张均为显式操作。
- 证据卡片使用 `supported / partial / needs_review / unsupported` 四态。AI 生成或缺少有效来源锚点的主张不会默认判为“支持”。
- 知识树拖拽在写库前展示子树影响数量；AI 展开任务返回 `suggestions`，必须调用确认接口后才创建正式节点；多书模式显示书目徽标。
- 综合研读改为研究报告工作区，输入包括明确书目、可选章节、已有知识笔记、研究问题和对比框架；DeepSeek 仍是默认文本模型。报告保存选择快照和逐条主张审计，可流转到知识笔记、证据卡片或 PPTX 工作台。
- 综合研读不再由 Nature 风格模板主导。默认 `adaptive + deep`：第一阶段让 DeepSeek 识别材料类型、拆分子问题、选择分析维度与证据需求；第二阶段在用户范围内迭代检索、综合一致/冲突、检查反例和替代解释。UI 只展示可审查的研究计划，不保存模型隐性推理。
- 方法参考 PaperQA2、GPT Researcher、RD-Agent 和 Scientific Agent Skills，但仅提炼“规划—检索—证据—综合—审计”流程，不安装其 Python/容器运行时。现有 DeepSeek 路由、SQLite、FTS5 和可选向量检索保持不变。
- 研究报告的来源锚点精确到 `B:CH:P:C` 或用户笔记；后端只接受本次上下文中实际存在的锚点。主张另存类型、置信度、核验状态、理由与反例，伪造/越界引用自动降为 `needs_review`。
- 内存与成本边界：规划阶段最多 18K 字符预览，证据上下文最多 48K；章节/笔记范围按构建过程即时截断，不复制附件。快速模式一次模型调用，深度模式两次调用；后台仍由全局 FIFO 单任务执行。
- 目录算法优先把连续的同编号体系识别为兄弟节点，修复“前一个（1）被当成后一个（4）的父级”导致跳号漏检的问题；缺号只生成带页区间的建议占位，不伪造标题。重复重启、倒退和 unresolved 跳号会携带 `needs_review` 状态进入目录工作台。
- 新增论文首页作者/单位/邮箱/基金等版面负证据，降低题名、作者被切成多个一级标题的概率；测试加入用户截图对应的阿拉伯括号链与中文括号重启链。
- 运行时不复制 PDF、页面图像或 OCR 资产；新增记录仅保存小型文本/JSON。报告只保存选择 ID，知识首页按需查询，阅读器仍只保留可见页 canvas。

### 第九阶段统一设计系统与核心工作区重排（2026-08-26）

- 视觉原则固定为“保留古籍节气气质，去装饰、强层级、内容优先”。深青应用壳、米色纸面与长文宋体保留；业务页不再新增大 Hero、大卡片或重复英文眉题。
- `bailu.css` 收敛为 12/13/14/16/20px 字号、8px 间距基线、8/12/16px 圆角和 gray-200 边框。普通卡片与工作面板不投影；只有弹窗、抽屉、下拉与明确悬停表面使用阴影。
- 应用顶栏统一为 48px，侧栏选中态改用低饱和暖赭底色和 3px 标识线；操作文字以 14px 黑体为基线，标题和文献题名使用宋体。
- 新增 `StudyCommandBar`、`StudyEmptyState`、`StudyListRow`、`StudyInspector` 四个复用组件。资料库已实际使用全部四种骨架，知识沉淀与问答/绘图复用命令栏或空状态。
- 资料库改为自适应 list-detail：大屏显示书架、列表和检查器；常见笔记本宽度优先保留书架与完整列表；窄屏把书架范围收为按需面板，详情沿用抽屉。
- 阅读器在 1180px 以下隐藏全局桌面侧栏，目录在 1100px 以下按需覆盖正文；首次进入窄屏不强制占据 250–320px。PDF 非连续模式仍只保留当前页或跨页 DOM，未增加 canvas 常驻量。
- 知识沉淀改为紧凑书目范围栏、单行功能切换和唯一工作区；无范围状态缩至 112px 并提供“使用最近资料”，不再留下大面积无动作空白。
- 文献工作台彻底取消三重导航：顶层只保留“选择范围—编辑提纲—来源审计—生成与验收”步骤条；获取全文、来源权利作为范围面板次级抽屉，输出记录从命令栏打开；生成条件并入汇报目标面板，不再使用三张编号卡表达流程。
- 按钮反馈统一为 120–180ms 的颜色、边框和轻阴影变化，不使用普遍的位移或缩放；问答、设置、AI 绘图同步使用统一表面、空状态和响应式断点。
- `paper_profiles` 新增 `publication_status`、`visibility`、`demo_allowed`、`metadata_confidence`。默认私有且禁止演示；未来年份不再直接显示为可信元数据，保存上限为当前年份 + 1。
- 任务管理新增 `cancelling/cancelled` 状态和 `/api/tasks/{task_id}/cancel`；进度回调会安全终止且不自动重试。OCR/解析两分钟无更新时间时，任务中心提示取消后重试并说明缓存保留。
- 回归：后端 104 项、前端 7 项、Vite 构建通过；1440px 资料库、1024px 阅读器/知识沉淀、768px 文献工作台无页面错误。

**体验目标**：本地部署（无网可用核心功能）、流畅（解析/检索毫秒级、大任务后台化）、功能完备（学-练-测-复盘全流程）。

**启动方式（2026-08 更新）**：根目录 `start.bat` / `stop.bat` 为稳定入口；中文 BAT/VBS 快捷入口在 `scripts/launchers/`，底层运行脚本在 `scripts/runtime/`。
> 注意：**不设开机自启**（用户要求手动启动）。

---
## 2. 位置与环境

| 项 | 值 |
|---|---|
| 项目根目录 | `D:/86153/Documents/study-assistant` |
| 后端 | Python 3.14 + FastAPI + SQLAlchemy + SQLite（WAL）+ cryptography（Key 加密） |
| 前端 | Vue3 + Vite 6 + Element Plus + ECharts 6 + pdf.js + marked + DOMPurify（History 路由 + FastAPI SPA fallback） |
| 虚拟环境 | 项目内 `.venv`（已装全部依赖，含 cryptography） |
| 启动 | 双击 `start.bat`；停止 `stop.bat`（PID 文件 + 归属校验）；旧启动器归档于 `scripts/legacy/` |
| 前端开发 | `cd frontend && npm run dev`（5173，代理 /api 到 8000）；改完 `npm run build` 后重启后端 |
| 数据 | `backend/data/`（study.db + uploads/ + chroma/ + models/ + .secret_key），备份=复制该目录或运行 `scripts/maintenance/backup.bat` |
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
- 四色高亮 + 固定批注面板（笔记/挂知识树/导出）、AI 选中解释翻译/总结本章/Qwen-VL 视觉解读
- PDF.js 官方文字缩放/旋转命中规则；跨页选区按页分段保存，原文引用含前后文锚点
- 扫描页按当前页生成 RapidOCR 透明文字层，页级 JSON 缓存，离开可见缓冲区即释放 DOM

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
- **稳定入口**：BAT/VBS/PowerShell 均按脚本自身目录定位。启动器使用系统互斥量避免重复双击，以 `/api/health` 判定就绪，8000 被占用时在 8001–8010 选择可用端口，并从真实监听连接反查 PID；动态地址写入 `backend/data/runtime/server.url`，失败写入 `launcher.log` 并弹出诊断提示。
- **自定义协议（按需唤醒）**：当前用户注册 `study-assistant://open`；支持如 `study-assistant://open/reader/6?page=21` 的白名单深链。`scripts/protocol/install.ps1` 同时创建桌面 `.url` 入口，WMI 启动独立 `pythonw + scripts/runtime/server_runner.py`，协议处理器退出后服务仍存活；未设置开机自启或额外守护进程，`scripts/protocol/uninstall.ps1` 可撤销。
- **入口验证（2026-08-27）**：完全停止后由协议唤醒至首页健康可用约 5.8 秒；人为占用 8000 时约 3.8 秒切换至 8001。固定 HTTP 地址只负责访问已运行实例，能够唤醒进程的稳定入口是桌面“打开学习助手”或 `study-assistant://open`。
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

## 7. 2026-08-25 阅读与导入专项修复

- **PDF 阅读器**：缩放时失效旧 Canvas、取消过期 render task，并在 140ms 稳定后按新倍率重绘；单页限制约 8MP 像素预算，仅保留可见页及相邻页，页面失败可单独重试。视觉解读改用异步 `toBlob`，书名直接请求单本 API。
- **Word/PPT 原版阅读**：新增 `GET /api/books/{id}/rendered-file`。本机 Office 只读、禁用宏后按需导出 PDF，中文源文件先复制到 ASCII 临时路径规避 COM 代码页问题；按 SHA256 缓存，完成后退出 Office。真实 DOCX 已验证生成 1.66MB PDF。
- **结构抽取**：DOCX 保留正文顺序中的表格和图片标记；PPTX 按坐标读取文本、表格、图片、图表及分组对象并生成逐页标题。结构化 PDF JSON 升级为 v2，加入图像资源 ID、坐标、图注关系和阅读顺序；双栏内容不再跨栏续句。
- **目录工作台**：旧 1180px 全量表格改为虚拟折叠树、原页 iframe、节点检查器三栏布局；支持问题队列、撤销、整组移动、升降级、新增删除和完整阅读器跳转。DocReader 内的重复简易目录编辑入口已移除。
- **网页 PDF 获取**：HTTPS 页面先探测内容类型，再解析 `citation_pdf_url`、下载链接、iframe/embed 和受控站点规则。人大复印报刊资料详情页可发现 `/qw/DownPdf?id=...`；用无 Cookie 新会话复核后判定为 Chrome 登录交接，避免解析成功但导入失败。
- **模型边界**：没有引入 MinerU。问答、总结和深度分析仍由 `LLMRouter` 固定返回 DeepSeek。设置页新增备用 OpenAI Chat Completions 兼容接口登记/检测，以及可配置的兼容视觉 Base URL；不会静默切换默认厂家。
- **附加修复**：设置页不再声称全部 AI 本地完成；视觉 Key 检测改为真实 `/models` 探测；结构阅读字号限制为 12–24px；网页登录失败返回可操作状态而非笼统 400。
- **验证**：后端 92 项测试通过；前端 5 项单测和 Vite 生产构建通过；真实 DOCX 与自动生成 PPTX 的 Office→PDF 渲染、rdfybk 精确入口均已验证。

### 7.1 PDF 划线与批注锚点修复（2026-08-25）

- 补齐 PDF.js 6 文本层的 `--font-height`、`--scale-x`、旋转与缩放样式；高亮覆盖层不再截获文字拖选。
- 修复浮动工具条重复叠加 `scrollTop/scrollLeft` 的坐标错误；批注编辑改为阅读器右侧固定面板。
- 批注 schema v2 使用 `anchor_json` 保存文档指纹、原文 exact/prefix/suffix 与多页 segments；后端拒绝越界、零宽高和超页码坐标，同时保留 v1 `page/rect_json` 投影。
- 新增扫描页 `GET /api/books/{id}/pdf-text-layer/{page}`：RapidOCR 只识别当前页，保存文字框后释放引擎；实测《行政管理学夏书章》第17页生成 28 个文字框，首次约 5.9 秒、缓存命中约 29ms，稳定工作集约 186MB。
- 历史 PDF 批注已按原文重新校准：book 3 与 book 6 共 3 条均迁移为 v2，原第68页越界坐标已恢复；无法自动定位时保留记录并支持用户重新选择。
- 修复 `/reader/{id}?page=N` 首次加载被宽度适配滚动事件重置为第1页的问题。
- 回归：后端相关测试 46 项、前端单测 7 项及生产构建通过；浏览器实测原生 PDF 跨页2段零越界、扫描页只加载当前页 OCR 层。

### 7.2 资料库与文献工作台迁移（2026-08-26）

- **资料库**：改为紧凑命令区、智能视图/书架、阅读导向列表与按需资料检查器。阅读状态、解析状态、顶级章节和关键词集中在选中资料上下文；详情和完整目录不再首屏批量加载。
- **工作台动线**：固定为“选择证据 → 设定汇报目标 → 审阅提纲 → 渲染与验收”。整篇、章节、选段和关联资源共同构成可见的证据边界；任务、全文获取、来源权利和输出记录分栏处理。
- **提纲编辑**：采用页面列表、当前页编辑、来源审计三栏结构，支持增删和排序。每页显示主张与来源状态，保存后重新执行数字、术语和定位审计。
- **文献输出**：DeepSeek 仍为默认文本分析模型。生成提示要求根据论文类型建立论证主线、一页一主张、结论式标题、术语一致、证据后解释意义，并显式呈现局限；证据不足时不得补造。
- **来源备注**：可编辑 PPTX 的演讲者备注写入页面主张、source ID、章节/页码和证据摘录；观众可见页面仅保留必要来源定位，不显示内部编辑说明。
- **合法全文**：界面明确区分开放获取、Chrome/馆藏登录接续与 PDF 校验导入。空结果和失败提示给出下一步；系统不读取 Cookie、密码或会话文件，也不绕过访问控制。
- **内存约束**：没有新增常驻服务；章节树默认不全量展开，资料详情按选中项加载，预览图懒加载，PPTX 与 Office 渲染继续交由全局串行任务中心。
- **验证**：前端生产构建通过；`presentation_deck.py` 语法检查通过；Playwright 实测 1600/1065/800px 下四个工作台标签页无横向溢出、无控制台错误。

### 7.3 知识沉淀迁移（2026-08-26）

- **单一入口**：侧栏只保留 `/knowledge-hub`；容器内部再切换笔记、知识树、知识图谱与综合研读，避免侧栏和页内导航重复。旧 `/notes`、`/knowledge`、`/graph`、`/study` 均重定向到对应 `view`，原有深链接继续可用。
- **统一范围**：`KnowledgeScopeSelector.vue` 与 `stores/knowledgeScope.js` 为笔记、知识树、知识图谱、综合研读提供同一组书目；范围保存在本地并在页面切换时持续生效。所有页面必须显式选择来源，不再把空范围解释为整个资料库。
- **职责边界**：笔记负责捕获与整理，知识树负责层级组织，图谱负责探索关系，综合研读负责形成跨文献结论。四页通过顶部子导航串成一条连续动线。
- **笔记收集箱**：新增 `/notes` 页面与 `GET /api/knowledge/notes`，复用现有 `KnowledgeNode(type=note)` 和 Annotation 数据，不建立第二套笔记副本。支持全文筛选、批注/知识笔记分类、编辑、明确主来源和原文回跳。
- **知识树减负**：移除页面内嵌 `PdfReader` 和 `default-expand-all`；保留可滚动原文摘录与阅读器定位。新根节点必须限定单本资料，子节点继承父节点书目，多本资料通过分别建树和跨树引用连接。
- **数据安全**：删除知识节点前先解除 Annotation 外键，阅读器中的原文高亮仍被保留。新增回归测试覆盖笔记范围、父子书目继承与删除不误伤高亮。
- **知识图谱**：增加概念搜索、最低出现次数筛选、概念出处侧栏与原文回跳。ECharts 仍按路由懒加载，离开页面即 dispose，不增加常驻内存。
- **综合研读**：请求必须包含非空 `book_ids` 与具体 `focus`。DeepSeek 输出要求注明支持文献、区分一致/互补/冲突并说明证据不足；历史区只展示与当前范围完全一致的报告。
- **交互与错误**：补充检索、创建、保存、删除、生成、空结果与失败后的可行动反馈；Axios 能把 FastAPI 校验数组转换为可读信息，不再显示 `[object Object]`。
- **验证**：知识沉淀与文献工作台专项后端测试 17 项通过，前端单测 7 项和生产构建通过；Playwright 实测 1600/1065/800px 下单一入口、四模式切换、旧链接映射与范围继承正常，无横向溢出和控制台错误；离开图谱后 Canvas 被卸载。

### 7.4 综合研读自主分析（2026-08-26）

- `StudyOverviewReq` 新增 `research_mode=adaptive|comparative|critical|gap` 与 `reasoning_depth=standard|deep`，且后端强制非空研究问题。
- 深度模式先生成并规范化 `material_type / subquestions / analysis_axes / evidence_needs / report_outline`，随后用研究问题和最多四个子问题迭代检索；用户已勾选章节或笔记时不会越界补充其他章节。
- 报告历史在原有 `selection_json` 内保存研究模式、深度、计划与待核查问题，无数据库迁移和附件副本。
- 多书向量检索改为按 `book_ids` 逐书调用，避免启用向量能力后混入未选资料。相关边界测试已加入 `test_knowledge_deposition.py`。

## 8. 测试情况

| 测试 | 位置 | 结果 |
|---|---|---|
| 单元/架构测试 | `backend/tests/`（含 reliability_phase1 + literature_workbench + knowledge_deposition） | 103 项全过（含研究计划/来源审计、多书向量范围、网页 PDF 发现、知识范围与笔记数据安全和 DeepSeek 默认路由） |
| 前端单测 | `frontend/tests/` | 7 项全过（SVG 安全、XML 转义、跨页批注锚点、卡片视觉令牌与按钮交互反馈） |
| UI 测试 | `backend/tests/ui_test.py`（Playwright） | 18 项全过（需先起后端） |
| CI | `.github/workflows/ci.yml` | push/PR 自动跑单测 + 前端构建 |
| Release | `.github/workflows/release.yml` | 打 `v*` 标签自动 build 前端 + 打包 zip 上传 Release |

标准运行：`.venv/Scripts/python -m pytest -q`；前端运行：`cd frontend && npm run test:unit && npm run build`

## 9. 关键踩坑记录（新对话必读）

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
28. **Office COM 中文路径**：Python 3.14/Windows 代码页可能把中文文件名传成乱码；渲染前复制到受控 ASCII 临时路径，输出验证 `%PDF-` 后再原子写入哈希缓存。
29. **网页候选会话假阳性**：详情页请求建立的临时 Cookie 可能让候选探测成功，但后续独立下载失败；候选必须用无 Cookie 新会话复核，不能把解析会话带入“开放获取”判断。

## 10. 已知边界

- OCR 默认使用已安装的 RapidOCR/ONNX；Tesseract/Paddle 仅为可选后端
- 向量检索默认关（省内存），开启需加载 fastembed 模型
- 思维训练会话为内存态（重启后端后会话丢失；上限 100 个，超了删最旧）
- 知识图谱概念来自 `book_analysis`（需资料做过智能分析才有节点）
- 深度分析/综合阅读/问答依赖 DeepSeek Key；视觉依赖 Qwen-VL Key
- 知识库首屏 JavaScript 实测约 1.21MB（解压后）；ECharts、PDF.js 与各工具页均按需加载，Element Plus 仍可继续做组件级导入
- PPTX 已支持主要内嵌图像提取；复杂多面板智能裁剪、矢量图重绘和低置信度公式原图保留仍待实现
- DOI/OA/arXiv 与馆藏浏览器交接已接入；出版社授权 API 仍作为 provider 扩展点，未实现也不会模拟绕过
- Paper Card 与 PPTX 导出已接入；周期性文献订阅尚未实现
- 视觉回归依赖本机 Microsoft PowerPoint；当 Office 不可用时仍可生成 PPTX，但质量门会保留未验收警告

## 11. 开源发布状态

- 仓库：https://github.com/boway033-cell/Study-assistant（分支 main，当前发布 tag v1.2.0）
- 许可证 MIT、PRIVACY.md、SECURITY.md、CHANGELOG.md、CONTRIBUTING.md、.gitattributes
- CI（ci.yml）+ Release 自动打包（release.yml）
- 分享给朋友：下载 Release 的 zip（含前端产物，不装 Node 也能用），或 git clone 后 `cd frontend && npm i && npm run build`

## 12. 快速上手命令

```bash
cd D:/86153/Documents/study-assistant
start.bat                                 # 稳定启动（自动开浏览器）
.venv/Scripts/python -m uvicorn backend.app.main:app --port 8000  # 直接启动

cd frontend && npm run build               # 改完前端构建（需 npm.cmd）
.venv/Scripts/python -m pytest -q          # 全量后端测试

# git 推送
git push origin main
git tag v1.2.0 && git push --tags        # 触发 Release 自动打包

# 关键文档
docs/README.md  docs/产品文档.md  docs/PROJECT_HANDOVER.md
docs/LIGHTWEIGHT_DOCUMENT_PIPELINE.md  docs/nature-literature-workflow.md
docs/01-architecture.md  docs/02-database.md  docs/03-api.md
PRIVACY.md  SECURITY.md  CHANGELOG.md  CONTRIBUTING.md  README.md
```
