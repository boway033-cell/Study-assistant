# 项目交接文档 · Study assistant（学习助手）

> **用途**：供新对话/新协作者快速接管项目。阅读本文件 + 启动项目即可继续开发。
> **最后更新**：知识图谱/学习计划两大功能 + 安全加固（Key 加密/上传校验/依赖审计修复）+ 开源发布至 GitHub 之后

---

## 1. 项目一句话

本地部署的个人学习辅助软件：把 PDF/Word/PPT 教材转化为「AI 问答 + 知识树 + 知识图谱 + 自测刷题 + 深度分析 + AI 研读 + 学习计划」的完整学习闭环。

**原始文件本地保存；启用 AI 功能时，问答/深度分析/出题/研读/知识树/视觉分析会向 DeepSeek / Qwen-VL 发送相关片段或图像（详见 PRIVACY.md）。** 知识图谱、学习计划、批注回流均为**纯本地**，不联网。

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

### 3.11 主题（古籍学术风）
- 深青灰绿底 `#2A3B3D`、纸色卡片 `#F5F0E8`、棕褐强调 `#8B5A2B`、暗青侧边栏 `#2E4042`
- 侧边栏/页头动态展示「节气名 + 年月日」（`frontend/src/utils/solarTerm.js`）

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
  │   └─ deep_analysis.py  三级标题/核对/补全/逐章总结/Markdown
  ├─ core/        config.py / database.py / crypto.py(Fernet Key 加密)
  ├─ worker/      tasks.py(FIFO 串行) + import_task.py(导入流水线；无自动云端)
  └─ models/      books/chapters/chunks/quizzes/attempts/annotations/knowledge_nodes/
                  book_deep/study_reports/settings/book_analysis/study_plans/check_ins
```

## 5. 安全与隐私（本轮重点加固）

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

## 6. 测试情况

| 测试 | 位置 | 结果 |
|---|---|---|
| 单元测试 | `backend/tests/`（test_unit/enhance/semantic/toc_heuristic + conftest.py） | 34 项全过（conftest 初始化 FTS，CI 全新环境也能过） |
| UI 测试 | `backend/tests/ui_test.py`（Playwright） | 18 项全过（需先起后端） |
| CI | `.github/workflows/ci.yml` | push/PR 自动跑单测 + 前端构建 |
| Release | `.github/workflows/release.yml` | 打 `v*` 标签自动 build 前端 + 打包 zip 上传 Release |

运行：`.venv/Scripts/python -m pytest backend/tests/test_unit.py backend/tests/test_enhance.py backend/tests/test_semantic.py backend/tests/test_toc_heuristic.py -q`

## 7. 关键踩坑记录（新对话必读）

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

## 8. 已知边界

- OCR 后端（tesseract/paddle）代码就绪未安装，扫描版 PDF 会提示
- 向量检索默认关（省内存），开启需加载 fastembed 模型
- 思维训练会话为内存态（重启后端后会话丢失；上限 100 个，超了删最旧）
- 知识图谱概念来自 `book_analysis`（需资料做过智能分析才有节点）
- 深度分析/综合阅读/问答依赖 DeepSeek Key；视觉依赖 Qwen-VL Key
- 主 JS 约 2.8MB（gzip 953KB），本地 localhost 首屏可接受，未做代码分割（按需可优化）

## 9. 开源发布状态

- 仓库：https://github.com/boway033-cell/Study-assistant（分支 main，tag v1.0.0）
- 许可证 MIT、PRIVACY.md、SECURITY.md、CHANGELOG.md、CONTRIBUTING.md、.gitattributes
- CI（ci.yml）+ Release 自动打包（release.yml）
- 分享给朋友：下载 Release 的 zip（含前端产物，不装 Node 也能用），或 git clone 后 `cd frontend && npm i && npm run build`

## 10. 快速上手命令

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
docs/00-PRD.md  docs/01-architecture.md  docs/02-database.md  docs/03-api.md
docs/04-roadmap.md  docs/experience-pdf-analysis.md  docs/产品文档.md
PRIVACY.md  SECURITY.md  CHANGELOG.md  CONTRIBUTING.md  README.md
```
