# 02 · 数据库设计

- 版本：v0.2（已按「低内存占用 / Python 3.14 / FTS5 优先」修正）
- 数据库：SQLite（WAL 模式），ORM：SQLAlchemy 2.0
- 配套：[01-architecture.md](01-architecture.md) / [03-api.md](03-api.md)

---

## 1. ER 关系总览

```
books 1───* chapters 1───* chunks
  │            │
  │            ├───* cards 1───* review_logs
  │            │
  │            ├───* quizzes 1───* attempts
  │            │
  │            └───* notes
  │
  ├───* chat_logs
  │
  └───（chunks 的向量存于 ChromaDB，id 与 chunks.id 对应）

settings（键值对，独立）
tasks（内存态，不入库）
```

## 2. 表结构

### 2.1 `books` — 书籍/资料

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK, AUTOINCREMENT | |
| title | TEXT | NOT NULL | 文件名（可改） |
| file_path | TEXT | NOT NULL | 相对 data/uploads 的路径 |
| file_type | TEXT | NOT NULL | pdf / docx / pptx |
| file_size | INTEGER | | 字节 |
| total_pages | INTEGER | | 解析后页数 |
| status | TEXT | NOT NULL, DEFAULT 'pending' | pending / parsing / ready / failed |
| error_msg | TEXT | | 失败原因 |
| created_at | DATETIME | NOT NULL | |

索引：`ix_books_status(status)`

### 2.2 `chapters` — 章节

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| book_id | INTEGER | FK→books.id, NOT NULL | |
| parent_id | INTEGER | FK→chapters.id, NULL | 多级目录 |
| title | TEXT | NOT NULL | |
| level | INTEGER | NOT NULL | 目录层级 1/2/3… |
| order_index | INTEGER | NOT NULL | 同级排序 |
| start_page | INTEGER | | 起始页（1-based） |
| end_page | INTEGER | | 结束页 |

索引：`ix_chapters_book(book_id, order_index)`

### 2.3 `chunks` — 文档切片（RAG 检索单元）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | 同时作为 ChromaDB 中的 doc id |
| book_id | INTEGER | FK, NOT NULL | |
| chapter_id | INTEGER | FK, NULL | |
| content | TEXT | NOT NULL | 切片文本 |
| page_start | INTEGER | | 起始页 |
| page_end | INTEGER | | 结束页 |
| chunk_index | INTEGER | NOT NULL | 书内顺序 |
| word_count | INTEGER | | 便于统计 |

索引：`ix_chunks_book(book_id, chunk_index)`

> 向量不存 SQLite，存 ChromaDB（collection=`study_chunks`，metadata 含 book_id/chapter_id/page_start/page_end，doc_id=chunks.id）。

### 2.4 `notes` — 笔记/标注

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| book_id | INTEGER | FK, NOT NULL | |
| chapter_id | INTEGER | FK, NULL | |
| page | INTEGER | NOT NULL | 所在页 |
| content | TEXT | NOT NULL | 笔记内容 |
| highlight_json | TEXT | | 高亮区域（pdf.js 坐标） |
| created_at | DATETIME | NOT NULL | |

### 2.5 `chat_logs` — AI 问答记录

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| book_id | INTEGER | FK, NULL | NULL=全部书籍 |
| question | TEXT | NOT NULL | |
| answer | TEXT | NOT NULL | |
| sources_json | TEXT | | [{chunk_id, page, snippet}] |
| mode | TEXT | NOT NULL | local / cloud |
| model_name | TEXT | | 实际使用的模型 |
| created_at | DATETIME | NOT NULL | |

索引：`ix_chat_book(book_id, created_at)`

### 2.6 `cards` — 记忆卡片

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| book_id | INTEGER | FK, NOT NULL | |
| chapter_id | INTEGER | FK, NULL | |
| front | TEXT | NOT NULL | 正面问题 |
| back | TEXT | NOT NULL | 背面答案 |
| tags | TEXT | | 逗号分隔 |
| source | TEXT | DEFAULT 'manual' | manual / auto |
| state | TEXT | NOT NULL, DEFAULT 'New' | New / Learning / Review / Relearning |
| stability | REAL | NOT NULL, DEFAULT 0 | FSRS 参数 |
| difficulty | REAL | NOT NULL, DEFAULT 0 | FSRS 参数 |
| due | DATETIME | NOT NULL | 下次到期时间 |
| last_review | DATETIME | | |
| elapsed_days | INTEGER | DEFAULT 0 | |
| scheduled_days | INTEGER | DEFAULT 0 | |
| reps | INTEGER | DEFAULT 0 | 复习次数 |
| lapses | INTEGER | DEFAULT 0 | 遗忘次数 |
| created_at | DATETIME | NOT NULL | |

索引：`ix_cards_due(due)`、`ix_cards_book(book_id)`、`ix_cards_state(state)`

### 2.7 `review_logs` — 复习记录

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| card_id | INTEGER | FK→cards.id, NOT NULL | |
| rating | TEXT | NOT NULL | again / hard / good / easy |
| state_before | TEXT | | |
| state_after | TEXT | | |
| elapsed_days | INTEGER | | |
| scheduled_days | INTEGER | | |
| reviewed_at | DATETIME | NOT NULL | |

索引：`ix_review_card(card_id)`、`ix_review_time(reviewed_at)`

### 2.8 `quizzes` — 题目

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| book_id | INTEGER | FK, NOT NULL | |
| chapter_id | INTEGER | FK, NULL | |
| q_type | TEXT | NOT NULL | choice / blank / short |
| question | TEXT | NOT NULL | 题干（可含填空位 ___） |
| options_json | TEXT | | 选择题选项 ["A.…","B.…"] |
| answer | TEXT | NOT NULL | 选择=正确项；填空=答案；简答=参考答案 |
| explanation | TEXT | | 解析 |
| difficulty | TEXT | DEFAULT 'normal' | easy / normal / hard |
| source | TEXT | DEFAULT 'auto' | auto / manual |
| created_at | DATETIME | NOT NULL | |

索引：`ix_quizzes_book(book_id, chapter_id)`

### 2.9 `attempts` — 答题记录

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| quiz_id | INTEGER | FK, NOT NULL | |
| user_answer | TEXT | NOT NULL | |
| is_correct | INTEGER | NOT NULL | 0/1（简答为自评） |
| is_self_graded | INTEGER | DEFAULT 0 | 简答自评标记 |
| answered_at | DATETIME | NOT NULL | |

### 2.10 `settings` — 系统设置（键值对）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| key | TEXT | PK | llm_mode / deepseek_api_key / daily_new_cards / embed_model / … |
| value | TEXT | | |

初始默认值：

```json
{
  "llm_mode": "local",
  "deepseek_api_key": "",
  "deepseek_base_url": "https://api.deepseek.com",
  "ollama_base_url": "http://localhost:11434",
  "ollama_model": "qwen2.5:3b-instruct",
  "embed_model": "BAAI/bge-small-zh-v1.5",
  "vector_search": "false",
  "daily_new_cards": "20",
  "rag_top_k": "5",
  "chunk_size": "600",
  "chunk_overlap": "80"
}
```

> 注意：`vector_search` 默认 `false`——P0 检索走 FTS5 关键词（零内存）；开启后才加载嵌入模型并建向量（P1）。

## 3. FTS5 全文索引

```sql
-- 由 jieba 分词后的文本写入（分词结果以空格连接）
CREATE VIRTUAL TABLE fts_books USING fts5(
  content,            -- 分词后的全文
  book_id UNINDEXED,
  chapter_id UNINDEXED,
  page UNINDEXED,
  chunk_id UNINDEXED
);
```

查询示例（**注意：UNINDEXED 列不能出现在 MATCH 内**，需放 WHERE 中）：

```sql
SELECT chunk_id, page, snippet(fts_books, 0, '[', ']', '…', 12)
FROM fts_books
WHERE book_id = 1 AND fts_books MATCH '"拉格朗日"'
ORDER BY rank
LIMIT 20;
```

需自行实现 jieba 分词 → 查询词同样分词后拼 MATCH 表达式（词间默认 AND 关系）。

> **P0 简化决策**：RAG 检索 P0 阶段**默认使用 FTS5（BM25 风格）关键词检索**，零额外内存、无模型下载；向量检索（fastembed + ChromaDB）作为 **P1 可选增强**，在设置中开关，默认关闭。原因：教材问答中关键词命中已覆盖大部分场景，且符合"低内存占用"硬约束。

## 4. 数据一致性约定

- 删除 Book 时级联删除：chapters、chunks、notes、chat_logs、cards、review_logs、quizzes、attempts、FTS 行、ChromaDB 对应向量
- 删除 Chapter 时级联其下 chunks/cards/quizzes（notes 保留）
- 卡片删除仅删卡片本身，review_logs 保留（用于统计）
- SQLite 开启 `PRAGMA foreign_keys=ON` 与 `PRAGMA journal_mode=WAL`
