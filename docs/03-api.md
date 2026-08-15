# 03 · API 接口清单

- 版本：v0.1
- 基础路径：`http://127.0.0.1:8000`
- 格式：JSON（上传用 multipart）；问答用 SSE（`text/event-stream`）
- 统一响应错误格式：`{"detail": "错误信息"}`（FastAPI 默认）
- 配套：[02-database.md](02-database.md)

---

## 0. 约定

- 所有接口均为单用户本地服务，无鉴权（仅监听 127.0.0.1）
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

响应 201：

```json
{
  "id": 1,
  "title": "高等数学（上）.pdf",
  "file_type": "pdf",
  "status": "pending",
  "task_id": "t-abc123",
  "created_at": "2025-06-01T10:00:00"
}
```

> 上传即入解析队列，用 1.9 查询进度。

### 1.2 书籍列表

```
GET /api/books?status=ready&page=1&page_size=20
```

响应：

```json
{
  "total": 5,
  "items": [
    {
      "id": 1,
      "title": "高等数学（上）",
      "file_type": "pdf",
      "status": "ready",
      "total_pages": 420,
      "chapter_count": 12,
      "card_count": 156,
      "quiz_count": 80,
      "created_at": "2025-06-01T10:00:00"
    }
  ]
}
```

### 1.3 书籍详情 + 章节树

```
GET /api/books/{book_id}
```

响应：

```json
{
  "id": 1,
  "title": "高等数学（上）",
  "file_type": "pdf",
  "status": "ready",
  "total_pages": 420,
  "error_msg": null,
  "chapters": [
    {
      "id": 10,
      "title": "第一章 函数与极限",
      "level": 1,
      "order_index": 1,
      "start_page": 1,
      "end_page": 60,
      "children": [
        {"id": 11, "title": "1.1 映射与函数", "level": 2, "start_page": 1, "end_page": 15, "children": []}
      ]
    }
  ]
}
```

### 1.4 重命名

```
PATCH /api/books/{book_id}
body: {"title": "新名字"}
```

### 1.5 删除（级联清理）

```
DELETE /api/books/{book_id}
```

响应 204。

### 1.6 重新解析

```
POST /api/books/{book_id}/reparse
```

响应：`{"task_id": "t-xyz"}`

### 1.7 全文搜索

```
GET /api/search?q=拉格朗日&book_id=1&chapter_id=&page=1&page_size=20
```

响应：

```json
{
  "total": 45,
  "items": [
    {
      "chunk_id": 320,
      "book_id": 1,
      "book_title": "高等数学（上）",
      "chapter_id": 15,
      "chapter_title": "3.2 中值定理",
      "page": 128,
      "snippet": "……拉格朗日中值定理：若函数 f(x) 在闭区间 [a,b] 上连续……"
    }
  ]
}
```

### 1.8 阅读器取文本

```
GET /api/books/{book_id}/content?page=128
```

响应：`{"page": 128, "text": "……", "chapter_id": 15}`

### 1.9 任务进度查询

```
GET /api/tasks/{task_id}
```

响应：

```json
{"task_id": "t-abc123", "status": "running", "progress": 0.45,
 "stage": "embedding", "message": "向量化 已处理 180/400 页"}
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
  "book_id": 1,          // 必填；0 或省略 = 全部书籍
  "question": "解释拉格朗日中值定理的几何意义",
  "mode": "auto"         // auto / local / cloud；auto=按 settings.llm_mode
}
```

响应：`text/event-stream`

```
event: meta
data: {"mode": "cloud", "model": "deepseek-chat", "book_ids": [1]}

event: token
data: {"text": "拉格朗日中值定理的几何意义是……"}

event: done
data: {"chat_id": 88, "sources": [
        {"chunk_id": 320, "page": 128, "snippet": "……"},
        {"chunk_id": 331, "page": 130, "snippet": "……"}
      ]}
```

错误时：`event: error` + `data: {"message": "…"}`

### 2.2 历史记录

```
GET /api/chat/history?book_id=1&page=1&page_size=20
```

响应：

```json
{"total": 30, "items": [
  {"id": 88, "question": "…", "answer": "…", "mode": "cloud",
   "sources": [{"page": 128, "snippet": "…"}], "created_at": "…"}
]}
```

### 2.3 删除记录

```
DELETE /api/chat/{chat_id}
```

---

## 3. 卡片 /api/cards

### 3.1 自动生成卡片（后台任务）

```
POST /api/books/{book_id}/generate-cards
body: {"chapter_ids": [10, 11], "max_per_chapter": 20}
```

响应：`{"task_id": "t-cards-1", "estimated": 40}`

任务完成后经 `GET /api/tasks/{task_id}` 查询，`result` 字段：

```json
{"generated": 38, "preview": [
  {"front": "…", "back": "…", "chapter_id": 11}
]}
```

> 设计：生成后先进入"预览待确认"状态，用户确认后入库；P0 简化版可直接入库。

### 3.2 今日复习队列

```
GET /api/cards/review-queue?limit=50
```

响应：

```json
{
  "due_count": 64,
  "new_count": 8,
  "items": [
    {
      "id": 512,
      "front": "拉格朗日中值定理的条件与结论",
      "back": "若 f(x) 在 [a,b] 连续、在 (a,b) 可导，则存在 ξ∈(a,b) 使 f'(ξ)=(f(b)-f(a))/(b-a)",
      "state": "Review",
      "due": "2025-06-01T08:00:00",
      "book_title": "高等数学（上）",
      "chapter_title": "3.2 中值定理"
    }
  ]
}
```

### 3.3 提交复习评级

```
POST /api/cards/{card_id}/review
body: {"rating": "good"}   // again / hard / good / easy
```

响应（返回更新后的卡片排期）：

```json
{
  "card_id": 512,
  "state": "Review",
  "stability": 8.4,
  "difficulty": 0.32,
  "due": "2025-06-05T08:00:00",
  "scheduled_days": 4
}
```

### 3.4 卡片管理

```
GET    /api/cards?book_id=1&chapter_id=10&state=Review&tag=高频&page=1&page_size=20
POST   /api/cards          body: {"book_id":1,"chapter_id":10,"front":"…","back":"…","tags":"高频"}
PATCH  /api/cards/{id}     body: {"front":"…","back":"…","tags":"…"}
DELETE /api/cards/{id}
```

---

## 4. 刷题 /api/quizzes

### 4.1 生成题目（后台任务 + 预览确认）

```
POST /api/books/{book_id}/generate-quizzes
body: {"chapter_ids": [10], "types": ["choice", "blank", "short"], "count_per_type": 5}
```

响应：`{"task_id": "t-quiz-1"}` → 任务结果含预览题列表。

确认入库：

```
POST /api/quizzes/batch-import
body: {"quizzes": [{"chapter_id": 10, "q_type": "choice", "question": "…",
                    "options_json": ["A.…","B.…"], "answer": "A", "explanation": "…"}]}
```

### 4.2 题目列表（练习/组卷）

```
GET /api/quizzes?book_id=1&chapter_id=10&q_type=choice&page=1&page_size=20
```

响应：

```json
{"total": 25, "items": [
  {"id": 3, "q_type": "choice", "question": "下列哪个是拉格朗日中值定理的推论？",
   "options": ["A. 罗尔定理", "B. 柯西中值定理", "C. 泰勒公式", "D. 以上都是"],
   "difficulty": "normal", "book_title": "…", "chapter_title": "…"}
]}
```

> 注意：答案字段默认**不下发**（防作弊），答题后才返回。

### 4.3 提交答案

```
POST /api/quizzes/{quiz_id}/attempt
body: {"user_answer": "A"}          // 填空直接填文本；简答填正文
```

响应：

```json
{
  "is_correct": true,
  "answer": "A",
  "explanation": "罗尔定理是拉格朗日定理 f(a)=f(b) 的特殊情形",
  "correct_rate": 0.82
}
```

### 4.4 简答自评

```
POST /api/quizzes/{quiz_id}/self-grade
body: {"is_correct": true}
```

### 4.5 错题本

```
GET /api/quizzes/wrong?book_id=1&page=1&page_size=20
```

### 4.6 题目管理

```
PATCH  /api/quizzes/{id}   body: {"question": "…", "answer": "…", "explanation": "…"}
DELETE /api/quizzes/{id}
```

---

## 5. 统计 /api/stats

### 5.1 总览

```
GET /api/stats/overview
```

```json
{
  "book_count": 5,
  "card_count": 620,
  "due_today": 64,
  "reviews_done": 1840,
  "quiz_count": 300,
  "avg_mastery": 0.68,
  "streak_days": 12
}
```

### 5.2 章节掌握度

```
GET /api/stats/mastery?book_id=1
```

```json
{
  "book_id": 1,
  "chapters": [
    {"chapter_id": 10, "title": "第一章 函数与极限",
     "mastery": 0.82, "cards": 40, "due": 3, "wrong_rate": 0.12}
  ]
}
```

> 掌握度 = 0.6×卡片状态分（按 stability 归一）+ 0.4×(1 - 错题率)，P1 阶段实现，可调权。

### 5.3 复习历史（曲线）

```
GET /api/stats/review-history?days=30
```

```json
{"daily": [
  {"date": "2025-06-01", "reviews": 45, "new_cards": 12, "due": 50},
  {"date": "2025-06-02", "reviews": 38, "new_cards": 10, "due": 44}
]}
```

### 5.4 薄弱章节排行

```
GET /api/stats/weakness?limit=10
```

```json
{"items": [
  {"book_id": 1, "book_title": "高等数学（上）", "chapter_id": 22,
   "chapter_title": "5.3 定积分应用", "mastery": 0.31, "suggest": "优先复习"}
]}
```

---

## 6. 设置 /api/settings

```
GET  /api/settings
```

```json
{
  "llm_mode": "local",
  "deepseek_api_key": "sk-***（脱敏显示）",
  "ollama_model": "qwen2.5:3b-instruct",
  "daily_new_cards": "20",
  "rag_top_k": "5",
  "vector_search": false,
  "ollama_connected": true,
  "deepseek_configured": false
}
```

```
PUT /api/settings
body: {"llm_mode": "cloud", "deepseek_api_key": "sk-xxx", "daily_new_cards": 30, "vector_search": true}
```

> `ollama_connected` / `deepseek_configured` 为只读探测字段，PUT 时忽略。
> `vector_search`：P0 默认 `false`（使用 FTS5 关键词检索，零额外内存）；置 `true` 时启用 fastembed + ChromaDB 向量检索（P1，需下载嵌入模型，首次启用会触发后台建向量任务）。

### 6.1 连接探测（设置页用）

```
GET /api/settings/probe
```

```json
{"ollama": {"ok": true, "models": ["qwen2.5:7b-instruct"]},
 "deepseek": {"ok": false, "reason": "未配置 API Key"}}
```

---

## 7. 保研面试（P2） /api/interview

```
GET    /api/interview/questions?course=高数&tag=高频&page=1
POST   /api/interview/questions      body: {"course": "高数", "question": "…", "answer_hint": "…", "tags": "高频"}
POST   /api/interview/generate       body: {"book_id": 1, "count": 50}   // AI 按课程生成高频题
PATCH  /api/interview/questions/{id}
DELETE /api/interview/questions/{id}
```

---

## 8. 状态码约定

| 码 | 场景 |
|---|---|
| 200 | 成功 |
| 201 | 创建成功（上传/导入） |
| 204 | 删除成功 |
| 400 | 参数错误 |
| 404 | 资源不存在 |
| 409 | 状态冲突（如未 ready 就提问） |
| 500 | 服务器错误 |
| 503 | LLM 不可用（本地 Ollama 未启动等） |
