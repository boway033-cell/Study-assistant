# 03 · API 接口清单

- 产品版本：v2.2.0
- 基础路径：`http://127.0.0.1:8000`
- 格式：JSON（上传用 multipart）；问答用 SSE（`text/event-stream`）
- 统一响应错误格式：`{"detail": "错误信息"}`（FastAPI 默认）
- 配套：[02-database.md](02-database.md)

---

## 0. 约定

- 所有接口均为单用户本地服务，绑定 127.0.0.1；带非本地 Origin 的 API 请求会被拒绝（防恶意网页跨源调用，详见 SECURITY.md）
- 时间字段统一 ISO8601 字符串
- 分页参数：`page`（从 1 起）、`page_size`（默认 20），响应含 `total`

---

## 1. 资料管理 /api/books

### 1.1 上传资料

```
POST /api/books/upload
Content-Type: multipart/form-data
form: file=<文件>
```

响应 201：`{"id": 1, "title": "...", "file_type": "pdf", "status": "pending", "task_id": "t-abc123", "created_at": "..."}`

> 上传即入解析队列，用 1.9 查询进度。

### 1.2 书籍列表

```
GET /api/books?status=ready&page=1&page_size=20&sort_by=custom
```

```json
{"total": 2, "items": [
  {"id": 1, "title": "高等数学（上）", "file_type": "pdf", "status": "ready",
   "total_pages": 420, "chapter_count": 12, "quiz_count": 80, "created_at": "..."}
]}
```

`sort_by` 支持 `custom`、`newest`、`oldest`、`title_asc`、`title_desc`、`author_asc`、`year_desc`、`year_asc`、`last_read`、`progress_desc`。传入 `shelf_id` 时，`custom` 使用该书架自己的顺序。

### 1.3 自定义顺序

```http
PUT /api/books/order
Content-Type: application/json

{"book_ids":[8,3,5],"shelf_id":null}
```

`shelf_id=null` 调整全库顺序；传入书架 ID 只调整该书架。接口允许提交当前已加载的子集，只替换这些文献占据的位置并归一化整个范围，不打乱未提交文献之间的相对顺序。

### 1.4 书籍详情 + 章节树

```
GET /api/books/{book_id}
```

```json
{"id": 1, "title": "...", "file_type": "pdf", "status": "ready", "total_pages": 420,
 "error_msg": null,
 "chapters": [{"id": 10, "title": "第一章 函数与极限", "level": 1, "order_index": 1,
   "start_page": 1, "end_page": 60, "children": []}],
 "analysis": {"definitions": [], "theorems": [], "keywords": [], "body_size": 10.5, ...}}
```

### 1.5 重命名 / 1.6 删除 / 1.7 重新解析

```
PATCH /api/books/{book_id}   body: {"title": "新名字"}
DELETE /api/books/{book_id}   # 204，级联清理
POST /api/books/{book_id}/reparse   # {"task_id": "t-xyz"}
```

### 1.8 全文搜索

```
GET /api/search?q=拉格朗日&book_id=1&chapter_id=&page=1&page_size=20
```

```json
{"total": 45, "items": [
  {"chunk_id": 320, "book_id": 1, "book_title": "高等数学（上）",
   "chapter_id": 15, "chapter_title": "3.2 中值定理", "page": 128,
   "snippet": "……拉格朗日中值定理：若函数 f(x) 在闭区间 [a,b] 上连续……"}
]}
```

### 1.9 原文定位

```
GET /api/books/{book_id}/file                    # 原始文件（iframe 支持 #page=N）
GET /api/books/{book_id}/chunk/{chunk_id}        # chunk 全文 + 页码区间
GET /api/books/{book_id}/page/{page_no}          # 指定页文本（PDF）
```

### 1.10 任务进度查询

```
GET /api/tasks/{task_id}
```

```json
{"task_id": "t-abc123", "status": "running", "progress": 0.45, "stage": "indexing", "message": "索引中 180/400"}
```

### 1.11 笔记

```
GET    /api/books/{book_id}/notes
POST   /api/books/{book_id}/notes   body: {"page": 128, "content": "…", "highlight_json": null}
PATCH  /api/notes/{note_id}         body: {"content": "…"}
DELETE /api/notes/{note_id}
```

---

## 2. AI 问答 /api/chat

### 2.1 流式提问（SSE）

```
POST /api/chat
body: {
  "book_id": 1,          // null 或省略 = 全部书籍
  "question": "解释拉格朗日中值定理的几何意义",
  "model": "flash"       // flash / pro；省略 = 用设置页默认档位
}
```

响应：`text/event-stream`

```
event: meta
data: {"mode": "deepseek", "model": "deepseek-v4-flash", "book_ids": [1]}

event: token
data: {"text": "拉格朗日中值定理的几何意义是……"}

event: done
data: {"chat_id": 88, "sources": [
        {"chunk_id": 320, "page": 128, "snippet": "……"},
        {"chunk_id": 331, "page": 130, "snippet": "……"}
      ]}
```

错误时：`event: error` + `data: {"message": "…"}`

> 前端收到 `done` 后，用 `sources[0].chunk_id` 调 `/books/{id}/chunk/{cid}` 在右侧展示原文。

### 2.2 历史记录 / 2.3 删除

```
GET    /api/chat/history?book_id=1&page=1&page_size=20
DELETE /api/chat/{chat_id}
```

```json
{"total": 30, "items": [
  {"id": 88, "question": "…", "answer": "…", "model": "deepseek-v4-flash",
   "sources": [{"chunk_id": 320, "page": 128, "snippet": "…"}], "created_at": "…"}
]}
```

---

## 3. 知识树 /api/knowledge

### 3.1 获取整棵树

```
GET /api/knowledge/tree
```

```json
{"total": 3, "items": [
  {"id": 1, "parent_id": null, "title": "公共管理学·核心框架", "book_id": null,
   "chapter_id": null, "note": null, "order_index": 0,
   "children": [
     {"id": 2, "parent_id": 1, "title": "第一章 导论", "book_id": 3, "chapter_id": 1,
      "note": "我的理解……", "order_index": 0, "children": []}
   ]},
  {"id": 4, "parent_id": null, "title": "高数错题梳理", ...}
]}
```

### 3.2 节点 CRUD

```
POST   /api/knowledge/nodes              body: {"parent_id": null, "title": "新知识树"}  → 201
PATCH  /api/knowledge/nodes/{id}         body: {"title": "…", "note": "…", "book_id": 3, "chapter_id": 1}
DELETE /api/knowledge/nodes/{id}         # 204，级联删除子树
POST   /api/knowledge/nodes/{id}/move    body: {"parent_id": 5}  # 防环校验
```

> 关联章节时后端以章节所属书籍为准（保证书/章一致）。

### 3.3 从书籍章节一键导入骨架

```
POST /api/knowledge/import-chapters
body: {"book_id": 3, "parent_node_id": null}   // parent_node_id=null → 新建《书名》章节骨架根节点
```

响应 201：新创建的根节点（含章节子节点树）。

### 3.4 AI 生成课程框架（后台任务）

```
POST /api/knowledge/ai-generate
body: {"book_id": 3, "parent_node_id": null}
```

响应 202：`{"task_id": "knowledge-ai-xxx", "status": "running"}`，用 `GET /api/tasks/{id}` 轮询，完成后 result.created = 节点数。

### 3.5 节点关联章节原文

```
GET /api/knowledge/nodes/{id}/source
```

```json
{"node_id": 2, "node_title": "第一章 导论", "book_id": 3, "book_title": "公共管理学",
 "chapter_id": 1, "chapter_title": "第一章 导论", "page_start": 1, "page_end": 28,
 "text": "（该章全部 chunk 合并后的原文）"}
```

---

## 4. 刷题 /api/quizzes

### 4.1 生成题目（后台任务，AI 分析教材内容生成）

```
POST /api/books/{book_id}/generate-quizzes
body: {"chapter_ids": [10]}     // 可选；缺省 = 全部章节；每章生成选择+简答各 5 道
```

响应：`{"task_id": "t-quiz-1", "estimated": 50}`，用 `GET /api/tasks/{id}` 轮询进度，完成后 result.generated = 题目数。

> 批量生成固定使用 flash 模型（速度快、省 token）。

### 4.2 题目列表 / 4.3 提交答案 / 4.4 简答自评 / 4.5 错题本 / 4.6 管理

```
GET    /api/quizzes?book_id=1&chapter_id=10&q_type=choice&page=1&page_size=20
POST   /api/quizzes/{quiz_id}/attempt      body: {"user_answer": "A"}
POST   /api/quizzes/{quiz_id}/self-grade   body: {"is_correct": true}
GET    /api/quizzes/wrong?book_id=1
PATCH  /api/quizzes/{id}    body: {"question": "…", "answer": "…", "explanation": "…"}
DELETE /api/quizzes/{id}
POST   /api/quizzes/batch-import  body: {"quizzes": [{"chapter_id": 10, "q_type": "choice", ...}]}
```

```json
{"total": 25, "items": [
  {"id": 3, "q_type": "choice", "question": "下列哪个是拉格朗日中值定理的推论？",
   "options": ["A. 罗尔定理", "B. 柯西中值定理", "C. 泰勒公式", "D. 以上都是"],
   "difficulty": "normal", "book_title": "…", "chapter_title": "…"}
]}
```

> 答案字段默认不下发（防作弊），答题后才返回。

---

## 5. 知识库洞察 /api/stats

```
GET /api/stats/knowledge-base?days=30
```

返回一个只读快照，覆盖：知识库健康分、解析/索引/目录/元数据覆盖、原文取证、知识对象、来源可追溯率、证据核验状态、研究报告、写作/PPTX 输出和分类型活动趋势。

```json
{
  "overview": {
    "book_count": 30,
    "ready_count": 27,
    "health_score": 84,
    "knowledge_object_count": 126,
    "traceable_rate": 0.91,
    "report_count": 8
  },
  "coverage": [
    {"key": "indexed", "label": "全文可检索", "value": 26, "total": 27, "rate": 0.963}
  ],
  "health": {"issue_count": 7, "categories": [], "items": []},
  "knowledge": {"annotations": 80, "notes": 24, "evidence_cards": 12, "tree_nodes": 10},
  "outputs": {"reports": 8, "writing": 3, "decks": 5, "recent": []},
  "activity": [{"date": "2026-08-29", "imports": 1, "evidence": 4, "knowledge": 2, "outputs": 1}]
}
```

统计不再使用在线时长、连续打卡或 AI 生成数量评价学习质量。`days` 支持 7–365 天。

---

## 6. 设置 /api/settings

```
GET /api/settings
```

```json
{
  "deepseek_api_key": "sk-***（脱敏显示）",
  "deepseek_model": "flash",       // flash / pro
  "rag_top_k": "5",
  "vector_search": false,
  "deepseek_configured": true
}
```

```
PUT /api/settings
body: {"deepseek_api_key": "sk-xxx", "deepseek_model": "pro", "vector_search": false}
```

> `deepseek_api_key` 留空 = 保留已存 Key；`deepseek_model` 只能是 flash / pro。
> `vector_search`：默认 `false`（FTS5 关键词检索，零额外内存）；置 `true` 时启用 fastembed + ChromaDB（需下载嵌入模型）。

### 6.1 连接探测

```
GET /api/settings/probe
```

```json
{"deepseek": {"ok": true, "reason": "已连接（模型: deepseek-v4-flash）"}}
```

### 6.2 模型连接与功能路由

```
GET    /api/settings/providers
POST   /api/settings/providers
DELETE /api/settings/providers/{provider_id}
POST   /api/settings/providers/{provider_id}/probe
GET    /api/settings/providers/{provider_id}/models
POST   /api/settings/providers/models/discover
PUT    /api/settings/providers/routing
```

`POST` 支持供应商预设标识、模型名、Base URL 与 `openai_chat`、`anthropic_messages`、`google_generate` 三种协议。远程接口必须使用 HTTPS；loopback 本机接口可使用 HTTP。

`POST /api/settings/providers/models/discover` 可在保存连接前读取模型列表。请求包含 `base_url`、`protocol` 和可选的 `api_key`；编辑已有连接时可传 `provider_id` 并省略 Key，以复用本机加密保存的密钥。返回 `{"items":["model-a","model-b"],"endpoint":"https://gateway.example/v1/models"}`。接口支持常见模型列表格式与 API 根路径回退，失败时返回 502；不支持列表查询的网关仍可手动输入模型名。

路由请求示例：

```json
{
  "default_provider_id": "kimi-main",
  "task_routes": {
    "research": "glm-research",
    "writing": "anthropic-writing",
    "presentation": "kimi-main"
  }
}
```
Key 单独加密保存，不进入接口配置 JSON；备用接口不会自动接管现有问答、总结或深度分析。

### 6.3 Office 原版渲染

```
GET /api/books/{book_id}/rendered-file
```

PDF 直接返回原文件；DOCX/PPTX 首次访问时调用本机 Office 按需渲染 PDF，之后按源文件哈希缓存。
Office 不可用或渲染失败时返回 503，结构文本阅读仍可使用。

### 6.4 网页全文解析与浏览器交接

```
POST /api/literature/resolve           body: {query,include_si}
POST /api/literature/import            body: {query,include_si,url,provider,title}
POST /api/literature/browser-handoff   body: {query,include_si,url}
POST /api/literature/library-handoff   body: {query,include_si}
```

`resolve` 会区分直接 PDF、普通 HTML、页面发现的 PDF 和登录依赖入口。只有 `direct_download=true`
的候选可以交给 `/import`；登录依赖候选使用 `/browser-handoff` 在当前 Chrome 打开，应用不读取 Cookie。

---

## 7. PDF 标注 /api

```
GET    /api/books/{book_id}/annotations?page=3
POST   /api/books/{book_id}/annotations   body: {"page":3,"rect_json":"[...]","anchor":{"schema_version":2,"quote":{"exact":"…","prefix":"…","suffix":"…"},"segments":[{"page":3,"source":"pdf-text|ocr","rects":[{"x":0.1,"y":0.2,"w":0.3,"h":0.04}]}]},"text":"…","color":"#f9e572","note":"…"}
PATCH  /api/annotations/{id}              body: {"note":"…","color":"…","anchor":{...},"status":"active|needs_reanchor"}
DELETE /api/annotations/{id}
GET    /api/books/{book_id}/annotations/audit
POST   /api/annotations/{id}/repair       # 按原文自动重建旧锚点，失败则 needs_reanchor
GET    /api/books/{book_id}/pdf-text-layer/{page}?generate=true  # 扫描页 OCR 坐标层
```

`anchor` 是权威坐标；`page/rect_json` 是旧客户端兼容投影。所有坐标归一化到页面 `0～1`，跨页选择必须拆成多个 segment。

## 8. AI 增强 /api/ai（可选，无 Key 时返回友好错误）

```
POST /api/ai/explain     body: {"text":"选中内容","action":"explain|translate","book_title":"…","chapter_title":"…"}
POST /api/ai/summarize   body: {"book_id":3,"chapter_id":4}        # 章节总结（本地文本 → 通用生成路由）
POST /api/ai/vision      body: {"book_id":3,"page":4,"image":"data:image/jpeg;base64,…","prompt":null}  # Qwen-VL
```

## 9. 深度分析 /api（标题目录+精读+Markdown）

```
POST /api/books/{book_id}/deep-analyze   # 触发（导入后自动触发；可手动重跑）
GET  /api/books/{book_id}/deep           # {status, toc, summaries, markdown}
POST /api/books/{book_id}/classify       # AI 分类单本
POST /api/books/classify-all             # AI 分类全部
PATCH /api/books/{book_id}/category      # 手动改分类 {category}
GET  /api/deep/status                    # 全部书籍深度状态
```

## 10. AI 研读 /api/study

```
POST /api/study/overview        # 综合阅读报告（后台任务，book_ids 可空=全部）
GET  /api/study/reports         # 历史报告
POST /api/study/train/start     # 思维训练开始 {book_ids, mode: quiz|free, topic}
POST /api/study/train/ask       # 回答一轮 {session_id, answer} → {message, round, done}
```

## 11. 虚拟书架 `/api/shelves`

```
GET    /api/shelves
POST   /api/shelves                         body: {name,parent_id?,description?,color?}
PATCH  /api/shelves/{shelf_id}              body: {name?,parent_id?,description?,color?,order_index?}
DELETE /api/shelves/{shelf_id}
PUT    /api/shelves/{shelf_id}/books        body: {book_ids:[...],mode:"add|replace"}
DELETE /api/shelves/{shelf_id}/books/{book_id}
GET    /api/books?shelf_id={id}              # shelf_id=0 表示未归档
```

> 删除书架只删除虚拟归属关系，不删除书籍记录或原文件。

### 目录逻辑审计与校正

```
GET  /api/books/{book_id}/toc-review          # 项级置信度、编号问题和安全修复建议
POST /api/books/{book_id}/toc-auto-repair     body: {apply:false|true}
PUT  /api/books/{book_id}/toc                 body: {items:[...],note?}
GET  /api/books/{book_id}/toc-revisions       # 最近 30 次修订快照摘要
POST /api/maintenance/toc-rebuild-all          # 后台复用结构证据重识别全库 PDF 目录
```

`PUT /toc` 是全量事务；每项使用 `client_key`，新项可用 `new:*`，
`parent_key` 必须指向列表中位于当前项之前的节点。保存后会同步
章节页界、chunk 归属、source map 和 FTS 定位，并使旧深度分析失效。

## 12. 文献汇报与来源权利

```
POST  /api/presentations/outline             # 分层取样后生成可编辑提纲
PATCH /api/presentations/{deck_id}/outline   # 保存人工编辑的 slides
POST  /api/presentations/{deck_id}/render    # 执行权利门禁、审计与 PPTX 渲染
GET   /api/presentations/{deck_id}/preview/{slide_no}
GET   /api/presentations/{deck_id}/download

GET    /api/literature/books/{book_id}/resources
POST   /api/literature/books/{book_id}/resources
PATCH  /api/literature/resources/{resource_id}
DELETE /api/literature/resources/{resource_id}
```

`POST /api/presentations/outline` 支持 `chapter_ids`、`chunk_ids`、`resource_ids`、`selected_text`、
`max_source_chars`、`use_scope`、`include_figures` 和 `rights_acknowledged`。返回的 coverage
同时包含内容覆盖率、结构覆盖率和各分组取样量。

## 13. 写作实验室 `/api/writing`

```
GET    /api/writing/profiles
POST   /api/writing/profiles
GET    /api/writing/profiles/{profile_id}
POST   /api/writing/profiles/{profile_id}/refine   # 202，返回 task_id；自动剔除已删除书目（pruned_book_ids）
DELETE /api/writing/profiles/{profile_id}          # 删除项目，输出保留并解除关联
POST   /api/writing/profiles/{profile_id}/imitate
POST   /api/writing/literature-review            # 202，返回 task_id
POST   /api/writing/clean-text
POST   /api/writing/clean-docx
GET    /api/writing/outputs
GET    /api/writing/outputs/{output_id}
PATCH  /api/writing/outputs/{output_id}
POST   /api/writing/outputs/{output_id}/review
GET    /api/writing/outputs/{output_id}/download
```

`POST /api/writing/literature-review` 请求示例：

```json
{
  "title": "地方治理研究中的参与机制",
  "question": "不同研究为何形成冲突结论？",
  "book_ids": [12, 18, 23],
  "review_type": "narrative",
  "discipline": "social_science",
  "length": 3500,
  "profile_id": 2,
  "ai_tone_constraints": true
}
```

- `review_type`：`narrative | scoping | evidence_map`。
- `discipline`：`auto | social_science | humanities | natural_biomedical`。
- 至少 2 篇、最多 50 篇已解析文献。内容只来自显式选择的书目，引用锚点格式为 `[B{book}:C{chunk}:P{start}-{end}]`。
- 综述通过全局任务中心异步生成；完成结果包含 `output_id`。目标长度为 1,200–20,000 字。无效锚点或引用覆盖不足会写入审计警告并保留草稿，不再丢弃整篇输出。
- `profile_id` 可空；选中时 Writing DNA 只校准表达，不能充当事实来源。`ai_tone_constraints` 默认开启生成期表达约束，之后仍可把输出送入逐条去 AI 味审阅。
- 这是封闭语料综述；没有完整检索、去重、筛选和质量评价流程时，不得宣称系统综述、元分析或 PRISMA 合规。

## 14. 状态码约定

| 码 | 场景 |
|---|---|
| 200 | 成功 |
| 201 | 创建成功（上传/导入/建节点） |
| 204 | 删除成功 |
| 400 | 参数错误 |
| 404 | 资源不存在 |
| 409 | 状态冲突（如未 ready 就提问） |
| 500 | 服务器错误 |
| 503 | LLM 不可用（未配置 API Key / 云端异常） |
