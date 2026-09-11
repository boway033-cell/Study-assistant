# P2 + /notes 批次交付报告（2026-09-11）

范围：`/api/knowledge/notes` 下推、API 链接层优化（适配 + 提速）、P2 可维护性批次。
约定：最小加法修复，全部改动前已备份到 `.workbuddy/backups/2026-09-11-p2/`。

---

## 一、后端（5 处）

### 1. `/api/knowledge/notes` 谓词下推（`backend/app/api/knowledge.py:132`）

| | 改前 | 改后 |
|---|---|---|
| `book_ids` | SQL `IN`（原本已下推） | 不变 |
| `q` | **两张表全量载入内存**，逐条 `f"{a} {b}".lower()` 子串匹配 | SQL `lower(title ‖ ' ' ‖ coalesce(note,'')) LIKE ? ESCAPE '\'`，只载入命中行 |

关键取舍：

- **拼接串保留分隔空格**，与旧 Python 语义逐字一致（跨字段搜索词「标题 笔记」照样命中）。
- **LIKE 通配符显式转义**：用户搜 `50%` 时 `%` 是字面量而非任意匹配。
- **放弃新增分页参数**：曾尝试给 `/notes` 加 `page/page_size`，但 `Query(default=…)` 在「测试直接位置调用函数」时是 `Query` 对象而非默认 int，立即破坏了 `test_scoped_note_inbox` 的位置调用。既然没有任何前端消费者需要分页，撤掉参数、只做下推，签名零变化。

**验证**：独立等价性脚本 13/13 用例通过（含跨字段空格、`%`/`_` 转义、非 note 类型排除、多书过滤、纯空白等价无过滤）；已固化为正式用例 `test_note_inbox_search_semantics_after_sql_pushdown`。真实库实测：无过滤 446 条，`q=方法` 命中 12 条（11 标注 + 1 笔记）。

### 2. SQLite 吞吐调优（`backend/app/core/database.py:14`）

新增 PRAGMA（逐条 try/except 防御，任一失败不阻断建连）：

| PRAGMA | 值 | 作用 |
|---|---|---|
| `synchronous` | `NORMAL` | WAL 下兼顾崩溃安全与写入延迟（SQLite 官方推荐搭配） |
| `busy_timeout` | `15000` | 并发写排队而非立刻 `SQLITE_BUSY` |
| `cache_size` | `-32000`（32MB） | 页缓存，热数据免重复 `read()` |
| `mmap_size` | `256MB` | 只读映射，减少系统调用 |
| `temp_store` | `MEMORY` | 临时表/排序走内存 |

另给 `connect_args` 加 `timeout: 30`（驱动层锁等待，与 busy_timeout 互补）。
**实测**：连接后逐项读回，全部生效。

### 3. JSON 响应 GZip（`backend/app/main.py:285`）

`GZipMiddleware(minimum_size=1024)`。**前提已验证**：本机 Starlette 1.6.0 的默认 `exclude_content_types` 已含 `text/event-stream`，任务 SSE 与 `/api/chat` 流式输出不受影响。
**实测**（隔离实例 8011）：`GET /api/books?page=1&page_size=100` → identity **48,686B** vs gzip **6,834B**，**7.1× / 省 86%**，响应头 `content-encoding: gzip` 确认。

### 4. `fitz` 句柄泄漏（`backend/app/services/parser/__init__.py:64`）

`doc.get_toc()`/`page_texts()` 抛异常时原先不会 `doc.close()` → Windows 下锁住 uploads 里的 PDF。改为 `try/finally` 关闭（关闭自身再套一层防御）。

### 5. `_persist` 静默吞异常（`backend/app/worker/tasks.py:84`）

`except Exception: pass` → `logger.warning(..., exc_info=True)`。进度/取消状态丢失从此留痕，写入仍是 best-effort 不阻塞任务。

---

## 二、前端 API 链接层（`frontend/src/api/index.js`）

「适应不同接口 + 拓宽流速减少输入输出时间」的落点：

1. **语义化超时档 `TIMEOUT`**（read 60s / write 120s / ai 6min / longAi 10min / upload 10min）——不同接口的合理等待时间差异极大，集中定义，6 处魔法数字已替换。数值与原值逐一相等，行为零变化。
2. **通用适配器 `api`**（get/post/put/patch/del/task/upload）——统一 baseURL、重复参数序列化（`book_ids` 这类）、错误规范化；新接口按语义取用。
3. **幂等读请求单次重试**——仅 GET/HEAD、仅无响应（断网/服务重启）或 429/502/503/504、300ms 退避、`__retried` 防重入、`signal.aborted` 不重试、写请求一律不重试（防重复提交）。
4. **161 个具名导出全部原样保留**（逐一比对）。

---

## 三、P2 零散小修（9 项，10 个文件）

| 项 | 文件 | 改动 |
|---|---|---|
| 拖拽环检测 | `KnowledgeView.vue` | `allowDrop` 从 `() => true` 改为：目标是自身或自己的子孙（沿 `parent` 上溯）一律禁止 |
| 无谓 deep watch ×2 | `KnowledgeView.vue` / `StudyView.vue` | 去掉 `deep: true`。前提已核实：`setKnowledgeScope` 是整体替换赋值，引用比较即可感知 |
| 硬编码 16 卡片 | `ReaderView.vue` | 改为 `artifactHeadings`：由已渲染 DOM 的 `h2`/`h2,h3,h4` 实测采集（`watch([artifactText, mode])` + `nextTick`），索引语义与 `jumpArtifact` 一致（0 基） |
| 同步 revoke ×3 | `PdfReader.vue` / `DrawView.vue`×2 | `a.click()` 后延迟 1s `revokeObjectURL`，部分浏览器不再判定为取消下载 |
| 键盘可达 | `MindMap.vue` | 节点 `role="button"` + `tabindex="0"` + Enter/Space 触发 + `:focus-visible` 焦点环（token 色） |
| 键盘可达 | `KnowledgeView.vue` | 树操作按钮从仅 `:hover` 显示扩为 `:hover, :focus-within` |
| 键盘可达 | `ChatView.vue` | 历史条目 `role="button"` + `tabindex` + Enter/Space + 焦点样式 |
| 触控 <40px | `KeycapCard.vue` | compact 档 35→40px（face 32→37px，保持比例关系） |
| 触控 <40px | `LibraryView.vue` | 收藏按钮补 `min-width/min-height: 40px` + inline-flex 居中 |

---

## 四、验证汇总

| 项 | 结果 |
|---|---|
| 后端全量 pytest | **229 passed, 1 skipped**（含新增 `/notes` 契约用例） |
| 前端单测 | **44 passed, 0 failed** |
| 10 个改动前端文件 | script 块逐个提取 `node --check` 全过 |
| SQLite PRAGMA | 连接后逐项读回，5 项全部生效 |
| GZip | 实测 7.1×（48,686B→6,834B）；SSE 端点确认不受影响 |
| `/notes` 下推 | 等价性 13/13；真实库 `q=方法` 12 条命中正确 |
| 临时实例 | 8011 隔离端口起停，已确认释放，未触碰 8000 主服务 |

## 五、未做与原因

1. **组件拆分**（`PdfReader` 1177 行等 4 个文件）——大重构，与「最小加法」约定冲突，建议单独立项。
2. **`service_registry.get()` 死代码**——文件本身不是死代码（`init/shutdown` 在 lifespan 使用），删除 `get()` 属可选清理，本轮不动。
3. **`/api/knowledge/records` 分页参数的 `Query` 对象陷阱**——现存代码把 `page/page_size` 排在 `db` 之后，若有测试直接位置调用会踩坑（当前无调用方）。属潜在而非现实问题，已在审计文档标注。

## 六、派发说明

原计划将 P2 前端小修派发子代理并行执行，因子代理遭遇平台频率限制（429，预计 12:48 重置）失败，全部工作由主会话直接完成，无内容缺失。
