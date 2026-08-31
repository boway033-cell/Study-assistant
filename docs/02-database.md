# 02 · 数据库设计

- 产品版本：v2.0.0；数据库 schema：v10
- 数据库：SQLite（WAL 模式），ORM：SQLAlchemy 2.0
- 配套：[01-architecture.md](01-architecture.md) / [03-api.md](03-api.md)

---

## 1. ER 关系总览

```
books 1───* chapters 1───* chunks
  │            │
  │            ├───* quizzes 1───* attempts
  │            │
  │            └───* notes
  │
  ├───* chat_logs
  │
  └───（chunks 的向量存于 ChromaDB，id 与 chunks.id 对应）

knowledge_nodes（自引用树，可关联 books/chapters）
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
| file_hash | TEXT | 索引 | SHA-256，用于精确查重 |
| total_pages | INTEGER | | 解析后页数 |
| status | TEXT | NOT NULL, DEFAULT 'pending' | pending / parsing / ready / failed / needs_ocr |
| error_msg | TEXT | | 失败原因 |
| category | TEXT | | 资料分类 |
| duplicate_of | INTEGER | | 疑似重复资料 ID |
| library_order | INTEGER | NOT NULL | 全库自定义拖拽顺序 |
| created_at | DATETIME | NOT NULL | |

书架顺序保存在 `shelf_books.order_index`，与 `books.library_order` 相互独立；一篇文献可以属于多个书架。

### 2.2 `chapters` — 章节

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| book_id | INTEGER | FK→books.id, NOT NULL | |
| parent_id | INTEGER | FK→chapters.id, NULL | 多级目录 |
| title | TEXT | NOT NULL | |
| level | INTEGER | NOT NULL | 目录层级 |
| order_index | INTEGER | NOT NULL | 同级排序 |
| start_page | INTEGER | | 起始页（1-based） |
| end_page | INTEGER | | 结束页 |

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
| word_count | INTEGER | | |

> 向量不存 SQLite，存 ChromaDB（collection=`study_chunks`，doc_id=chunks.id）。

### 2.4 `notes` — 笔记/标注

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| book_id | INTEGER | FK, NOT NULL | |
| chapter_id | INTEGER | FK, NULL | |
| page | INTEGER | NOT NULL | 所在页 |
| content | TEXT | NOT NULL | 笔记内容 |
| highlight_json | TEXT | | 高亮区域 |
| created_at | DATETIME | NOT NULL | |

### 2.5 `chat_logs` — AI 问答记录

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| book_id | INTEGER | FK, NULL | NULL=全部书籍 |
| question | TEXT | NOT NULL | |
| answer | TEXT | NOT NULL | |
| sources_json | TEXT | | [{chunk_id, page, snippet}] |
| mode | TEXT | NOT NULL | deepseek |
| model_name | TEXT | | 实际模型名（deepseek-v4-flash/pro） |
| created_at | DATETIME | NOT NULL | |

### 2.6 `quizzes` — 题目

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| book_id | INTEGER | FK, NOT NULL | |
| chapter_id | INTEGER | FK, NULL | |
| q_type | TEXT | NOT NULL | choice / blank / short |
| question | TEXT | NOT NULL | 题干 |
| options_json | TEXT | | 选择题选项 |
| answer | TEXT | NOT NULL | |
| explanation | TEXT | | 解析 |
| difficulty | TEXT | DEFAULT 'normal' | |
| source | TEXT | DEFAULT 'auto' | auto / manual |
| created_at | DATETIME | NOT NULL | |

### 2.7 `attempts` — 答题记录

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| quiz_id | INTEGER | FK, NOT NULL | |
| user_answer | TEXT | NOT NULL | |
| is_correct | INTEGER | NOT NULL | 0/1（简答为自评） |
| is_self_graded | INTEGER | DEFAULT 0 | 简答自评标记 |
| answered_at | DATETIME | NOT NULL | |

### 2.8 `knowledge_nodes` — 知识树节点

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PK | |
| parent_id | INTEGER | FK→knowledge_nodes.id, NULL | NULL=根节点 |
| title | TEXT | NOT NULL | 节点标题 |
| book_id | INTEGER | FK→books.id, NULL | 关联书籍（可选） |
| chapter_id | INTEGER | FK→chapters.id, NULL | 关联章节（可选，右侧看原文） |
| note | TEXT | | 用户笔记/总结 |
| order_index | INTEGER | NOT NULL | 同级排序 |
| created_at | DATETIME | NOT NULL | |

### 2.9 `settings` — 系统设置（键值对）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| key | TEXT | PK | deepseek_api_key / deepseek_model / rag_top_k / vector_search / … |
| value | TEXT | | |

初始默认值：

```json
{
  "deepseek_api_key": "",
  "deepseek_base_url": "https://api.deepseek.com",
  "deepseek_model": "flash",
  "vector_search": "false",
  "rag_top_k": "5"
}
```

> `vector_search` 默认 `false`——P0 检索走 FTS5 关键词（零内存）；开启后才加载嵌入模型（P1）。
> 卡片相关表（cards / review_logs）已随「取消卡片学习」删除。

## 3. FTS5 全文索引

### 2.10 `shelves` / `shelf_books` — 层级虚拟书架

- `shelves` 保存 `name`、`parent_id`、`description`、`color`、`order_index` 和时间戳。
- `shelf_books` 以 `(shelf_id, book_id)` 为复合主键，另保存 `order_index`。
- 同一本书可属于多个书架；删除书架只级联删除归属关系。

### 2.11 `literature_resources` — SI 与来源权利

| 字段组 | 说明 |
|---|---|
| `book_id`, `resource_book_id`, `role`, `title` | 所属主文、可选已导入资源书籍及 main/SI/figure/table/dataset/other 角色 |
| `source_url`, `provenance_json` | 可回查的来源与获取记录，不保存 Cookie/密码 |
| `license_expression`, `rights_statement_uri` | SPDX 式许可证表达与 RightsStatements URI |
| `rights_status`, `rights_holder` | 权利评估状态与权利人 |
| `attribution`, `permission_note`, `allow_reuse` | 归属文字、单独授权说明与复用标记 |

### 2.12 `presentation_decks` — 提纲与渲染状态

- 状态流：`pending → outlining → outline_ready → rendering → done|failed`。
- `selection_json` 保存选择边界、source map 和覆盖率；`outline_json` 保存人工可编辑页面。
- `qa_json` 保存主张—来源审计、结构检查、真实 PowerPoint 渲染与视觉差异结果。
- `manifest_json` 保存输出版本、所用来源、权利决策和预览信息。

### 2.13 `toc_revisions` — 目录修订追溯

- `book_id`：所属文献；删除文献时级联删除。
- `source`：`auto` 或 `user`；`note` 保存修订说明。
- `before_json` / `after_json`：保存标题、层级、父级、顺序和页界的前后快照。
- 当前 schema version = 7。

### 2.14 `annotations` — PDF 批注与稳定锚点

- `page` / `rect_json`：v1 兼容投影，保存第一个页面片段。
- `schema_version`：`1` 为旧单页矩形，`2` 为版本化多页锚点。
- `anchor_json`：保存文档指纹、原文 `exact/prefix/suffix` 和逐页 `segments`；每段标记 `pdf-text` 或 `ocr` 来源。
- `status`：`active` 或 `needs_reanchor`。自动原文定位失败时保留批注，允许用户在阅读器重新选择位置。
- 所有矩形为页面归一化坐标，后端验证 `x/y/w/h`、页码及边界，不再接受越界记录。

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

查询示例（**UNINDEXED 列不能出现在 MATCH 内**，需放 WHERE 中）：

```sql
SELECT chunk_id, page, snippet(fts_books, 0, '[', ']', '…', 12)
FROM fts_books
WHERE book_id = 1 AND fts_books MATCH '"拉格朗日"'
ORDER BY rank
LIMIT 20;
```

> **P0 简化决策**：RAG 检索 P0 阶段**默认使用 FTS5（BM25 风格）关键词检索**，零额外内存；向量检索（fastembed + ChromaDB）作为 **P1 可选增强**，默认关闭。

## 4. 数据一致性约定

- 删除 Book 时级联删除：chapters、chunks、notes、chat_logs、quizzes、attempts、book_analysis、FTS 行、ChromaDB 对应向量
- 删除知识树节点时级联删除其子树（`knowledge_nodes`）
- SQLite 开启 `PRAGMA foreign_keys=ON` 与 `PRAGMA journal_mode=WAL`
