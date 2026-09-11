# B1 检索栈升级 — 功能设计规格

**依据**：`docs/reports/COMPETITIVE_BENCHMARK_2026-09-11.md` §六 B1 + §七 Now 阶段
**日期**：2026-09-11
**范围**：查询改写 + 混合检索（保持）+ Cross-Encoder 重排 + 父子分块
**交付形态**：功能规格设计文档（不含实现代码），供评审后进入实施

---

## 0. 设计前提：先纠偏三处"文档 vs 代码"的事实差

动笔前已对代码基线逐项核实，有三处与对标文档的判断不一致，**设计必须按代码事实走**：

| 项 | 文档说法 | 代码实测 | 对设计的影响 |
|---|---|---|---|
| 重排 | "无 reranker" | `rag/reranker.py` 已存在并接入 `retriever`，但只是**词面特征打分**（覆盖率 0.4 / 位置 0.2 / RRF 0.3 / 长度 0.1），**不是 Cross-Encoder** | 不是"从零新增"，而是**在现有 `rerank()` 内部插入语义重排分支并保留降级** |
| 重排模型 | 建议 `bge-reranker-v2-m3` | 本机 fastembed **0.8.0** 的 `TextCrossEncoder` 支持列表里**没有 v2-m3** | 改用 `BAAI/bge-reranker-base`（MIT，1.04 GB，中文可用），v2-m3 列为后续升级路径 |
| 分块 | "现状未显式" | 已有**两套**：固定窗口（`CHUNK_SIZE=600`/`OVERLAP=80`）与 `semantic_chunker`（TARGET 500 / MAX 900），导入时优先语义、异常回退固定 | 父子分块要**同时兼容两条切片链路** |

另外两个关键事实：

- **锚点不依赖 `chunk_id`**。`Annotation` 用 `page + rect_json`，`EvidenceCard` 用 `book/page/chapter + source_ref_json`。因此 `chunks` 表改造**不会动摇 M1 证据链**，这是本设计能采用最小加法的前提。
- **已有一处上下文拼接洞**：`get_chapter_neighbors_batch` 对 `chapter_id IS NULL`（未归属章节的页）**直接返回空上下文**。父子分块可以顺带补上。

---

## 1. 目标与非目标

### 目标（可量化）

| # | 目标 | 度量 |
|---|---|---|
| G1 | 提升 Top-5 内的正确来源占比 | `retrieval_v2` 固定集 Recall@1 / MRR **不下降且至少一项提升** |
| G2 | 喂给 LLM 的上下文从"碎片相邻块"变成"完整语义单元" | 父子分块命中率 ≥ 95%（`parent_group IS NOT NULL` 的 chunk 占比） |
| G3 | 改善"换个说法就搜不到" | 开启改写后，固定集上**同义表述题**的 Recall@3 提升 |
| G4 | 不破坏现有承诺 | 无 Key / 不开开关时行为**与今天完全一致**；222 pytest + 44 前端测试全绿 |

### 非目标（本轮明确不做）

- ❌ **上下文压缩**（B1 表格里的一项）：父子分块已把上下文收拢为受控长度，再叠一层压缩是额外 LLM 调用与延迟，放到 B2 受控 Agentic 阶段一并设计。
- ❌ **语义分块**：对标文档已论证 NDCG 0.79 但成本 ×50、速度 ⭐⭐，明确不建议。
- ❌ **改 RRF 权重**：RRF 自动平衡多源贡献，无权重可调，也不需要调。
- ❌ **全库重建索引 / 改 chunk 主键**：不加 chunk 行、不改 `chunk_index`、不动 `source_map_json`。

---

## 2. 总体设计：四段式流水线

```
                      ┌─ 原句 ─┐
问题 ──▶ [① 查询改写] ─┼─ 改写1 ┼─▶ [② 混合召回] ─▶ 候选池(≤30)
        (可选/有Key)   └─ 改写2 ┘   向量+FTS+LIKE       │
                                       RRF 融合         ▼
                                              [③ 重排] ──▶ Top-K
                                        词面特征 ⊕ Cross-Encoder
                                         (可选/本地模型)   │
                                                          ▼
                                              [④ 上下文组装]
                                          parent 组 > 章节邻居 > 单块
```

**三条铁律**：

1. **每个新增环节都可关闭，关闭后行为逐字节等价于今天。**
2. **每个新增环节都有降级链**，任何异常/超预算都静默退回下一级，不向用户抛错。
3. **改写只用于扩召回，重排永远用用户原句**——防止改写漂移污染最终排序。

---

## 3. 模块一：父子分块（Parent-Child Chunking）

### 3.1 方案选型

| 方案 | 做法 | 冗余 | 风险 | 结论 |
|---|---|---|---|---|
| A. 父块落库 | `chunks` 加 `parent_id` + `chunk_role`，父块作为独立行 | 正文再存一份（+25~35% 库体积） | 新增行会影响到 `chunk_index` 序列、`source_map`、FTS 总量 | 否 |
| **B. 父块编组（推荐）** | `chunks` 加 `parent_group` + `parent_ord` 两列，父块内容按需由子块拼 | **零冗余** | 无 | **采用** |
| C. 不落库，按章节现拼 | 无 schema 改动 | 零 | 组大小不可控，无法跨"未归属章节"的页 | 否 |

**方案 B 的额外好处**：取上下文的批量查询模式与现有 `get_chapter_neighbors_batch` 完全同构（一次 `WHERE parent_group IN (...)` + Python 侧分组），Reviewer 心智负担最低。

### 3.2 数据模型变更（最小加法）

`backend/app/models/__init__.py` → `Chunk`：

```python
parent_group: Mapped[int | None] = mapped_column(Integer, index=True)
parent_ord:   Mapped[int | None] = mapped_column(Integer)
```

- 两列**均可空**，旧数据为 `NULL` → 自动走"章节邻居"回退，语义与今天一致。
- 迁移沿用 `backend/app/main.py::_migrate()` 的既有模式（`PRAGMA table_info` 判列 → `ALTER TABLE ADD COLUMN`），不需要数据回填。

```python
chunk_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(chunks)")).fetchall()]
if "parent_group" not in chunk_cols:
    conn.execute(text("ALTER TABLE chunks ADD COLUMN parent_group INTEGER"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_chunks_parent_group ON chunks(parent_group)"))
if "parent_ord" not in chunk_cols:
    conn.execute(text("ALTER TABLE chunks ADD COLUMN parent_ord INTEGER"))
```

### 3.3 编组规则

新模块 `backend/app/services/rag/parent_chunk.py`：

```python
def assign_parent_groups(db, book_id: int, *, group_size: int = 4, min_size: int = 2) -> int:
    """为已落库的 child chunks 写回 parent_group / parent_ord。返回组数。"""
```

规则：

1. 按 `(chapter_id, chunk_index)` 排序分组；**不跨章节**，`chapter_id IS NULL` 的块按 `(book_id, NULL)` 单独成序列（**顺带修复 3.1 里那个空上下文的洞**）。
2. 每 `group_size`（默认 4，≈ 4×600 = 2400 字符）个连续 child 划为一组。
3. 组内 child 数 `< min_size`（默认 2）时并入相邻组，避免退化成"组 = 1 个 child"。
4. 章节内 child 总数 ≤ 3 → 整章编为一组（与 `get_chapter_neighbors` 现有的 `len(rows) <= 3 → 返回整章` 行为对齐）。
5. `parent_group` 取**组内首个 child 的 id**，`parent_ord` 从 0 递增。

调用点：`backend/app/worker/import_task.py` 第 7 步（写 chunks + FTS 索引）之后、`source_map` 构建之前插入一次。**只写两列，不重新切片、不重跑 FTS。**

### 3.4 上下文组装（替换点）

新增 `get_parent_contexts_batch(chunk_ids) -> dict[int, str]`，与 `get_chapter_neighbors_batch` 同构（2 条 SQL）。

`retriever.retrieve()` 第 5 步改为**三级优先**：

```python
if it.get("parent_group"):                      # ① 父块组（新）
    ctx = parent_contexts.get(cid)
elif it.get("chapter_id") is not None:          # ② 章节邻居（现状）
    ctx = neighbor_contexts.get(cid)
else:                                           # ③ 单块内容
    ctx = it.get("content", "")
```

**重叠去重**：child 之间带 `chunk_overlap=80` 重叠，直接 `"\n".join` 会出现重复段（**这在现有 `get_chapter_neighbors` 里已经存在**，不是新引入）。新实现加一个 `_join_dedup_overlap(parts, max_overlap)`：相邻两段做最长公共前后缀匹配（上限 `max_overlap`），命中则去掉重复部分。`chunk_overlap=0` 时退化为直接拼接。

**长度硬顶**：`PARENT_MAX_CHARS`（默认 2400）。超长时以命中 child 为中心截取窗口，保证命中块居中，不简单截断尾部。

### 3.5 开关与回填

- 默认**开启**（`PARENT_CHUNK=true`）：纯本地、零外部依赖、零冗余存储，无理由默认关。
- 老书无 `parent_group` → 走回退，功能不降级，只是拿不到新收益。
- 提供批量回填：`POST /api/books/rebuild-parent-groups`（任务化，复用 `submit()`，只写两列，不重新解析）。前端放在"知识库健康检查"页，**不自动执行**。

---

## 4. 模块二：Cross-Encoder 重排

### 4.1 模型选型

| 模型 | 体积 | 许可 | 中文 | 结论 |
|---|---|---|---|---|
| **BAAI/bge-reranker-base** | 1.04 GB | **MIT** | 好 | **默认** |
| Xenova/ms-marco-MiniLM-L-6-v2 | 0.08 GB | Apache-2.0 | 弱 | 低内存档备选（`RERANK_MODEL` 可切） |
| jinaai/jina-reranker-v2-base-multilingual | 1.11 GB | **CC-BY-NC-4.0** | 好 | ❌ **排除**——项目为 MIT，NC 许可会污染下游商用 |
| BAAI/bge-reranker-v2-m3 | — | — | 更好 | ❌ fastembed 0.8.0 不支持；列为后续升级路径 |

> 升级路径备注：若后续要上 v2-m3 / Qwen3-Reranker，需要自行托管 ONNX 并绕过 fastembed 的模型清单，成本不低。本轮不碰。

### 4.2 新模块

`backend/app/services/rag/cross_encoder.py`（完全对齐 `vector.py` 的写法：懒加载单例 + `HF_HUB_OFFLINE` + 本地 cache_dir + `unload()`）：

```python
from fastembed.rerank.cross_encoder import TextCrossEncoder

RERANK_MODEL = "BAAI/bge-reranker-base"
MODEL_CACHE_DIR = str(settings.data_dir / "models")   # 与 vector.py 共用缓存目录

def is_enabled() -> bool: ...
def ensure_model_ready() -> tuple[bool, str]: ...
def score_pairs(query: str, docs: list[str]) -> list[float]: ...   # 返回 sigmoid 归一化分
def unload() -> None: ...
```

### 4.3 与现有词面重排的关系：编排层改造

**不改 `rerank()` 的签名与返回结构**（现有 3 个调用方：`retriever.retrieve`、`scripts/evaluate_retrieval.build_search`、测试），只在内部插入分支：

```python
def rerank(query, items, top_k=5):
    scored = _lexical_score(query, items)          # 现状：覆盖率/位置/RRF/长度 → 保留
    if not cross_encoder.is_enabled() or len(items) < 2:
        return _take_top(scored, top_k)            # 与今天完全一致
    try:
        with _time_budget(settings.rerank_time_budget_ms):
            ce = cross_encoder.score_pairs(query, [content_of(i) for i in items])
        for it, s in zip(items, ce):
            it["rerank_score"] = round(
                (1 - settings.rerank_lexical_weight) * s
                + settings.rerank_lexical_weight * it["rerank_score"], 4)
            it["rerank_mode"] = "cross_encoder"
    except (BudgetExceeded, Exception):
        _record_degrade()                          # 连续 3 次 → 本次进程内熔断
        return _take_top(scored, top_k)            # 静默退回词面分
    return _take_top(sorted_by(items, "rerank_score"), top_k)
```

融合权重：`RERANK_LEXICAL_WEIGHT` 默认 **0.3**（语义为主、词面为辅）。保留词面分不是冗余——它对**专名/公式/编号**这类 CE 容易失手的精确匹配有兜底价值。

### 4.4 候选池扩容

现在 `retriever` 只给 `rerank` 喂 `top_k * 2 = 10` 条，重排空间太小。改为：

```python
pool = max(top_k * 2, settings.rerank_candidates if cross_encoder.is_enabled() else top_k * 2)
```

`RERANK_CANDIDATES` 默认 **30**。仅当重排开启时扩容，关闭时**不额外召回**（避免无谓的 FTS/向量开销）。

### 4.5 降级链

```
Cross-Encoder 未开 / 候选 < 2        → 词面重排（现状）
模型加载失败                          → 词面重排 + 设置页显示"模型未就绪"
单次耗时 > RERANK_TIME_BUDGET_MS(800) → 本次降级 + 计数
连续 3 次异常                         → 进程内熔断，本次会话不再尝试
```

任何一级都不向用户抛错，只在响应里带 `rerank_mode` 字段供可观测。

### 4.6 资源预算（必须写进设置页 UI）

| 项 | 数值 |
|---|---|
| 磁盘 | +1.1 GB（`backend/data/models`） |
| 常驻内存 | ~1.2 GB（ONNX 会话） |
| 首次加载 | 3–8 s（CPU） |
| 单次重排 30 对 | 目标 P95 < 800 ms（**需实测校准**，超标即触发降级） |

> 项目已有"低内存"设计倾向（嵌入模型默认关、可 `unload()`）。重排**默认关闭**，与 `vector_search` 的处理方式保持一致。

---

## 5. 模块三：查询改写（Multi-query）

### 5.1 设计

新模块 `backend/app/services/rag/query_rewrite.py`：

```python
async def expand(question: str, cfg: dict) -> list[str]:
    """返回 [原句, 改写1, 改写2]；任何失败都返回 [原句]。"""
```

- 走 `load_llm_config(db, "utility")` + `LLMRouter.get()`（复用现有任务路由与回退链）。
- 参数：温度 0.2、`max_tokens` 200、超时 `QUERY_REWRITE_TIMEOUT`（默认 3.0 s）。
- 输出要求 JSON `{"queries": ["...", "..."]}`，用现有 `parse_json_response()` 解析；解析失败 / 超时 / 无 Key → 返回 `[question]`。
- **只产出 2 个改写**（不是越多越好：3 路召回已经能把候选池撑满，再多只会放大噪声与成本）。

### 5.2 关键约束

1. **改写只用于扩召回**。`retrieve()` 内部：3 路召回 → 合并去重 → RRF 融合 → **用用户原句做重排**。
2. **默认关闭**（`QUERY_REWRITE=false`）。开启需要用户显式确认。
3. **隐私明示**：设置页必须写明"开启后，你的**提问文本**会发送给已配置的模型服务以生成等价表述（不会上传知识库内容）"。这与项目"不静默上传"的既有约定一致。
4. **成本可见**：改写会多一次 LLM 调用。设置页标注"每次提问 +1 次模型调用"。

### 5.3 不做 HyDE / Step-back

文档里列了三种。本轮只做 **Multi-query**：HyDE 需要生成假设性文档再嵌入，在"无向量/低配"场景下不可用且延迟翻倍；Step-back 对学术提问收益不稳定。两者留到 B2 受控 Agentic 阶段评估。

---

## 6. 配置项

`backend/app/core/config.py` 新增（**默认值保证不开开关时行为不变**）：

```python
# 父子分块（纯本地、零成本 → 默认开）
self.parent_chunk: bool = _env("PARENT_CHUNK", "true").lower() == "true"
self.parent_group_size: int = max(2, int(_env("PARENT_GROUP_SIZE", "4")))
self.parent_max_chars: int = int(_env("PARENT_MAX_CHARS", "2400"))

# Cross-Encoder 重排（需下载模型 → 默认关）
self.rerank_enabled: bool = _env("RERANK_ENABLED", "false").lower() == "true"
self.rerank_model: str = _env("RERANK_MODEL", "BAAI/bge-reranker-base")
self.rerank_candidates: int = max(self.rag_top_k * 2, int(_env("RERANK_CANDIDATES", "30")))
self.rerank_time_budget_ms: int = int(_env("RERANK_TIME_BUDGET_MS", "800"))
self.rerank_lexical_weight: float = min(1.0, max(0.0, float(_env("RERANK_LEXICAL_WEIGHT", "0.3"))))

# 查询改写（需模型 Key → 默认关）
self.query_rewrite: bool = _env("QUERY_REWRITE", "false").lower() == "true"
self.query_rewrite_timeout: float = float(_env("QUERY_REWRITE_TIMEOUT", "3.0"))
```

`rerank_enabled` / `query_rewrite` / `parent_chunk` 三项同时落 `settings` 表（沿用 `vector_search` 的 DB 设置 + env 双通道模式）。

---

## 7. API 契约变更

| 端点 | 变更 | 兼容性 |
|---|---|---|
| `GET /api/settings` | `SettingsResp` 增加 `parent_chunk` / `rerank_enabled` / `rerank_model` / `rerank_ready` / `query_rewrite` | 加字段，向后兼容 |
| `PUT /api/settings` | 接受上述字段；`rerank_enabled=true` 时**同步调用** `cross_encoder.ensure_model_ready()`（与 `vector_search` 在 `settings.py:221` 的处理一致） | 向后兼容 |
| `POST /api/settings/rerank/prepare` | **新增**。显式触发模型下载/加载，返回 `{ok, message}` | 新增 |
| `POST /api/books/rebuild-parent-groups` | **新增**。批量回填老书的 `parent_group`（任务化，走 `submit()`） | 新增 |
| `GET /api/books/search` | `SearchResultItem` 增加**可选** `rerank_mode: str \| None`（用于可观测与调试） | 加可选字段 |

**不改动**：`POST /api/chat`、`/api/study` 的对外契约（内部走同一条 `retrieve()`，自动受益）。

---

## 8. 前端改动

`frontend/src/views/SettingsView.vue`（现有 `form` 里已有 `rag_top_k` / `vector_search` 两个检索项，按同样风格追加）：

| 控件 | 默认 | 说明文案 |
|---|---|---|
| 父子分块 | 开 | "把连续小段编为完整语义单元后再喂给 AI，提升上下文完整性" |
| 语义重排 | 关 | "加载本地 Cross-Encoder 模型（约 1.1 GB 磁盘 / 1.2 GB 内存）对候选重排序" |
| — 模型状态 | — | 未就绪时显示"下载并启用"按钮（走 `/settings/rerank/prepare`） |
| 查询改写 | 关 | ⚠️ "开启后你的**提问文本**会发送给已配置模型生成等价表述；知识库内容不会上传。每次提问 +1 次模型调用" |

`KnowledgeHealthView.vue`：新增"重建父子分块索引"入口（显示 `parent_group IS NULL` 的 chunk 数量），**不自动执行**。

**不做**：检索结果页展示重排分数（对普通用户是噪声；`rerank_mode` 只在接口层保留，供调试与评测）。

---

## 9. 验收标准

### 9.1 回归底线（任一不通过即打回）

| 项 | 标准 |
|---|---|
| 后端单测 | `.venv/Scripts/python.exe -m pytest -q` → **222 passed / 1 skipped**，无新增失败 |
| 前端测试 | `cd frontend && node --test tests/*.test.js` → **44 passed** |
| 默认配置行为 | 三项开关保持默认（parent 开 / rerank 关 / rewrite 关）时，`retrieval_v2` 指标与改造前**逐项对比不下降** |
| 无 Key 可用 | 全部关闭且无 API Key 时，解析/检索/组织功能完整可用（守住 M4） |

### 9.2 固定集量化收益

`backend/eval/retrieval_v2.json`（114 题 = 102 可答 + 12 无答案）现网阈值：

```
recall_at_1 ≥ 0.78 | recall_at_3 ≥ 0.90 | recall_at_5 ≥ 0.95
mrr ≥ 0.82 | citation_correct_rate ≥ 0.78 | no_answer_rejection_rate = 1.0
```

`scripts/evaluate_retrieval.py` 的 `build_search()` 需升级为**可插拔开关矩阵**，产出 2×2 对比报告：

```powershell
.venv\Scripts\python scripts\evaluate_retrieval.py `
  --parent on --rerank on --rewrite off `
  --output backend\eval\reports\retrieval-b1.json
```

验收门槛：

- **父子分块单独开启**：Recall@3 / MRR **不低于基线**（允许 Recall@1 持平）。
- **重排开启**：`mrr` 与 `recall_at_1` **至少一项提升 ≥ 3 个百分点**，且其余项不下降。
- **无答案拒答率**：任何组合下**必须保持 1.0**（这是 M1 的底线，不因召回扩大而放松）。

### 9.3 真实库人工抽检

固定集是合成语料，不足以证明真实收益。补充：选 **3 本真实书 × 20 题**（覆盖中文教材 / 英文论文 / 扫描 OCR 三类），人工对比"改造前后 Top-5 是否包含正确出处"，记录到 `docs/` 附录。

### 9.4 延迟

| 开关组合 | 单次检索 P95 增量 |
|---|---|
| 仅父子分块 | ≤ 50 ms（多 1 条批量 SQL） |
| + 重排（本地 CPU） | ≤ 800 ms（超预算自动降级，不算失败） |
| + 查询改写 | ≤ 3 s（受 `QUERY_REWRITE_TIMEOUT` 硬顶） |

---

## 10. 实施 WBS

按"每步可独立交付、独立验收"拆分，**严格串行**（后一步依赖前一步的评测基线）：

| 步骤 | 内容 | 涉及文件 | 验收 | 复杂度 |
|---|---|---|---|---|
| **S0** | 评测脚本可插拔改造（`--parent/--rerank/--rewrite` 开关矩阵 + 对比报告） | `scripts/evaluate_retrieval.py`、`rag/evaluation.py` | 跑出改造前基线报告并存档 | 低 |
| **S1** | 父子分块：加列 + 迁移 + 编组 + 批量取上下文 + 三级回退 + 重叠去重 | `models/__init__.py`、`main.py::_migrate`、`rag/parent_chunk.py`、`rag/fts.py`、`rag/retriever.py`、`worker/import_task.py` | S0 基线不下降；新书 `parent_group` 覆盖 ≥95% | 中 |
| **S2** | 重排编排层：保留 `rerank()` 签名，插入 CE 分支 + 时间预算 + 熔断降级 | `rag/reranker.py`、`rag/retriever.py`（候选池扩容） | CE 关闭时行为与 S1 完全一致 | 中 |
| **S3** | Cross-Encoder 模型接入 + 配置 + 设置项 + prepare 端点 | `rag/cross_encoder.py`（新）、`core/config.py`、`api/settings.py`、`SettingsView.vue` | 模型可下载/加载/unload；未就绪时静默降级 | 中 |
| **S4** | 查询改写 | `rag/query_rewrite.py`（新）、`rag/retriever.py`、配置与设置页 | 无 Key / 超时 / 解析失败 → 退回原句 | 低—中 |
| **S5** | 验收与公开化：2×2 矩阵报告 + 真实库抽检 + 更新 `SCALING_AND_RETRIEVAL_EVAL.md` | `backend/eval/reports/`、`docs/` | 第 9 节全部达标 | 低 |

**S1 与 S2 之间必须停一次**：S1 落地后先跑 S0 的评测确认父子分块没有负收益，再动重排。

---

## 11. 风险与应对

| # | 风险 | 影响 | 应对 |
|---|---|---|---|
| R1 | bge-reranker-base 1.1 GB 下载失败 / 磁盘不足 / 内存吃紧 | 重排不可用 | 默认关闭；显式下载按钮 + 失败提示；`unload()` 释放；低内存场景可切 MiniLM-L-6（0.08 GB） |
| R2 | CPU 重排延迟超标，拖慢检索 | 体验下降 | 时间预算 800 ms 硬顶 + 降级链；只在候选 ≤30 时启用 |
| R3 | 父块让上下文变长 → token 成本上升 | 成本 | `PARENT_MAX_CHARS=2400` 硬顶 + 命中块居中截取；`build_prompt` 现有 12000 字符总截断不变 |
| R4 | 改写引入噪声，召回质量反而下降 | 质量 | 只用于召回、重排用原句；默认关；固定集上单开关验证，收益不达标就不上 |
| R5 | `chunks` 加列影响既有查询/分页 | 稳定性 | 可空列 + 默认 NULL；不加行、不改 `chunk_index`、不动 `source_map`；老库零数据迁移 |
| R6 | `evaluate_retrieval.py` 的 3 个既有测试依赖 `rerank` 行为 | 回归 | **S2 明确保留 `rerank()` 签名与返回结构**；测试不改动 |
| R7 | 中文重排效果不及预期（bge-reranker-base 非最新） | 收益打折 | S3 验收设"MRR 提升 ≥3pt"硬门槛，不达标则记录数据、暂不默认开启，转评估 v2-m3 自定义 ONNX 方案 |

---

## 12. 明确不做（防止范围蔓延）

1. 不改 RRF 融合逻辑与 `RRF_K=60`。
2. 不做语义分块 / 不重做现有两套切片算法的边界策略。
3. 不做上下文压缩（延后至 B2）。
4. 不做 HyDE / Step-back 改写（延后至 B2 评估）。
5. 不引入 `torch` / `FlagEmbedding` 等重依赖（只用已有的 fastembed + onnxruntime）。
6. 不引入 NC 许可模型（jina-reranker-v2-multilingual）。
7. 不改 `chunks` 主键、不新增 chunk 行、不动 `source_map_json`。
8. 不在前端暴露重排分数与改写后的查询（仅接口层保留 `rerank_mode` 供调试）。

---

## 13. 与后续步骤的接口预留

| 后续项 | 本设计预留的接口 |
|---|---|
| **B2 受控 Agentic 研究报告** | `retrieve()` 保持纯函数签名；改写能力独立成 `query_rewrite.expand()`，Agentic 循环可直接复用；候选池大小可参数化 |
| **B3 局部 GraphRAG** | 父块组天然是实体抽取的最小上下文单元（比相邻块拼接更完整） |
| **B6 评测公开化** | S0 的开关矩阵 + 对比报告格式，就是后续自动生成公开评测报告的雏形 |
| **B5 引用核验加码** | 父块上下文让"引用语境分类"能拿到完整句子，而不是被切碎的半句 |
