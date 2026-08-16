# 03 · API 接口清单

- 版本：v0.4（知识树 / DeepSeek 云端 / 卡片 API 已移除）
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
GET /api/books?status=ready&page=1&page_size=20
```

```json
{"total": 2, "items": [
  {"id": 1, "title": "高等数学（上）", "file_type": "pdf", "status": "ready",
   "total_pages": 420, "chapter_count": 12, "quiz_count": 80, "created_at": "..."}
]}
```

### 1.3 书籍详情 + 章节树

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

### 1.4 重命名 / 1.5 删除 / 1.6 重新解析

```
PATCH /api/books/{book_id}   body: {"title": "新名字"}
DELETE /api/books/{book_id}   # 204，级联清理
POST /api/books/{book_id}/reparse   # {"task_id": "t-xyz"}
```

### 1.7 全文搜索

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

### 1.8 原文定位

```
GET /api/books/{book_id}/file                    # 原始文件（iframe 支持 #page=N）
GET /api/books/{book_id}/chunk/{chunk_id}        # chunk 全文 + 页码区间
GET /api/books/{book_id}/page/{page_no}          # 指定页文本（PDF）
```

### 1.9 任务进度查询

```
GET /api/tasks/{task_id}
```

```json
{"task_id": "t-abc123", "status": "running", "progress": 0.45, "stage": "indexing", "message": "索引中 180/400"}
```

### 1.10 笔记

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

## 5. 统计 /api/stats（基于作答数据，卡片已移除）

### 5.1 总览

```
GET /api/stats/overview
```

```json
{"book_count": 5, "quiz_count": 300, "attempts_total": 1840,
 "avg_mastery": 0.68, "streak_days": 12}
```

### 5.2 章节掌握度

```
GET /api/stats/mastery?book_id=1
```

```json
{"book_id": 1, "chapters": [
  {"chapter_id": 10, "title": "第一章 函数与极限", "mastery": 0.82, "quizzes": 40, "wrong_rate": 0.12}
]}
```

> 掌握度 = 1 - 错题率（按章节题目最近一次作答）。

### 5.3 作答趋势

```
GET /api/stats/activity?days=30
```

```json
{"daily": [{"date": "2025-06-01", "attempts": 45}, {"date": "2025-06-02", "attempts": 38}]}
```

### 5.4 薄弱章节排行

```
GET /api/stats/weakness?limit=10
```

```json
{"items": [{"book_id": 1, "book_title": "高等数学（上）", "chapter_id": 22,
  "chapter_title": "5.3 定积分应用", "mastery": 0.31, "suggest": "优先复习"}]}
```

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

---

## 7. PDF 标注 /api

```
GET    /api/books/{book_id}/annotations?page=3
POST   /api/books/{book_id}/annotations   body: {"page":3,"rect_json":"[{x,y,w,h}]","text":"…","color":"#f9e572","note":"…","knowledge_node_id":null}
PATCH  /api/annotations/{id}              body: {"note":"…","color":"…"}
DELETE /api/annotations/{id}
```

## 8. AI 增强 /api/ai（可选，无 Key 时返回友好错误）

```
POST /api/ai/explain     body: {"text":"选中内容","action":"explain|translate","book_title":"…","chapter_title":"…"}
POST /api/ai/summarize   body: {"book_id":3,"chapter_id":4}        # 章节总结（本地文本 → DeepSeek）
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

## 11. 状态码约定

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