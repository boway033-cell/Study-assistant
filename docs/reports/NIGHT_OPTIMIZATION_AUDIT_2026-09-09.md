# 定向审计：研读任务持久化续连（2026-09-08 晚优化）

范围：`backend/app/api/deep.py`（362 行）、`backend/app/api/study.py`（893 行）、`frontend/src/views/StudyView.vue`（279 行），以及与之直接耦合的 `backend/app/worker/tasks.py:290-299`。
方式：全文件逐行精读 + 对每条结论回到源码/配置确认（已排除 3 处误判：`workspace-grid` 响应式其实是完备的、`yield_per` 期间没有并发查询、`task-monitor` 的 `finally` 清理是正确的）。

> **修复状态（2026-09-09 当日）**：1 ✅ 前端区分取消与失败｜2 ✅ 取消文案按任务类型区分｜3 ✅ `deep.py` 加独立 `except TaskCancelled`｜4 ✅ 生成阅读卡前重取 chunks｜5 ✅ 重复提交入口拦截（`has_active_task`）｜8、9、10 ✅ 清理死代码与重复加载。备份在 `.workbuddy/backups/2026-09-09-audit-fix/`，后端 186 项 + 前端 43 项通过，Vite 构建通过。
> **已撤回第 6 条**：`prose_prompt = [dict(item) for item in prompt]` 会产生新的 dict 对象，赋的值是字符串（不可变），因此修改副本并不影响 `prompt[0]` —— 审计时判断有误，该项不是缺陷。
> **更正第 5 条机制**：每个队列各只有**单 worker 串行**执行（`tasks.py:169 _RESOURCE_HEAVY_TASKS` + `_worker(queue)` 一次只 `await record._coro`），所以两个 `deep` 任务**不会**并发写坏数据；真实代价是重复调大模型 / OCR。拦截仍然值得做，但理由是"避免重复计费与任务中心歧义"而非"防止数据错乱"。
> **未修**：11（`_stream_answer` 每 token 建 Task，纯开销）。

结论：**没有 P0**。这套实现的质量比我上一轮审计时看到的整体水平更高——增量缓存（`chapter_hashes_json` + `persist_chapter`）、不在模型推理期间持有读事务（`db.rollback()` / `db.commit()`）、草稿实时落盘（`draft_markdown`）都是正确且讲究的做法。问题集中在**取消语义**和几处可维护性。

---

## P1 — 用户可见的错误

| # | 位置 | 问题 |
|---|---|---|
| 1 | `StudyView.vue:201` + `:208-210` | 主动取消被当成失败：点「停止任务」后 SSE 返回 `cancelled`，这里把 `failed` 和 `cancelled` 一并 `throw new Error(task.error \|\| task.message \|\| '任务未完成')`，最终由 catch 里的 `ElMessage.error('生成失败：…')` 弹出红色错误。用户自己点的停止，却看到"生成失败"。草稿其实已由 `liveDraft` 正常保留 |
| 2 | `worker/tasks.py:299` | 取消提示文案写死 OCR 场景：`update_progress` 抛出的是"已完成的页面缓存会在下次解析时复用"。这条路径现在被研读/报告/知识树扩展共用 → 取消一份研究报告时会看到关于 PDF 页面缓存的说明。上一条的错误弹窗里原样显示这句文案 |

## P2 — 数据一致性

| # | 位置 | 问题 |
|---|---|---|
| 3 | `deep.py:184-192` vs `:205` | `TaskCancelled` 只在 `use_ai` 分支里局部 import 并捕获；在此之前第 62/76/87/129/149/170 行任何一次 `update_progress` 抛出的取消，都会落到 205 行的通用分支 → `book_deeps.status='failed'`、`error_msg` 是取消文案，而此时逐章增量缓存里其实已成功落盘若干章节。目前没有 UI 读这个 status（`ReaderView` 只取 toc/markdown/paper_card），属潜伏状态，但和任务中心的"已取消"永久矛盾 |
| 4 | `deep.py:186` | `build_paper_card(provider, book.title, toc, chunks, …)` 传入的 `chunks` 已在 146、179 行两次 `db.commit()` 后全部过期（`sessionmaker` 默认 `expire_on_commit=True`）。`deep_analysis.py:463-469` 逐块访问 `.page_start/.id/.content` → 每个 chunk 触发一次懒加载 SELECT。数百块书籍会在一次 LLM 调用前先跑数百条查询 |
| 5 | `deep.py:218-228` | 无并发保护：`POST /books/{id}/deep-analyze` 不检查该书是否已有 running 的研读任务。连点两次会起两个 `run_deep_analysis`，两者都在 await 后写 `summaries_json`/`chapter_hashes_json`，`persist_chapter` 交错提交会让"章节哈希表 ↔ 已保存正文"的对应关系错乱 |

## P2 — 可维护性

| # | 位置 | 问题 |
|---|---|---|
| 6 | ~~`study.py:490-491`~~ | **已撤回**：该项判断有误，不是缺陷。`[dict(item) for item in prompt]` 产生的是新的 dict 对象，被赋值的 content 是字符串（不可变），修改副本不会影响原始 `prompt` |
| 7 | `study.py:591-592` | ✅ 已删 |
| 8 | `deep.py:28-38` | `_page_texts_from_chunks()` 全仓无调用（已改用 `build_chapter_inputs`），死代码 |
| 9 | `StudyView.vue:253` | `typeLabel` 定义后在模板与脚本中均无引用，死代码 |
| 10 | `StudyView.vue:259` + `:260-262` | `watch(knowledgeBookIds, resetScope, { deep: true })`：`onMounted` 里 `loadKnowledgeBooks()` 填充 store 会触发该 watcher → `resetScope()` 跑一遍 `loadMaterials()`，紧接着 `onMounted` 自己也跑一遍 → 每次进页面，所有书目的 `getBook` 详情 + 笔记列表都请求两遍 |
| 11 | `study.py:74-77` | `_stream_answer` 每接收一个 token 都 `asyncio.create_task(anext(iterator))` 再 `wait(timeout=2)`：万级 token 的长报告会产生上万个 Task 与调度开销。功能正确，纯开销 |

## 建议处理顺序

1 和 2 是同一件事的两半，`update_progress` 的取消文案应按任务类型区分（或改为中性文案），前端对 `cancelled` 单独走 `ElMessage.info('已停止，草稿已保留')` —— 合起来约 10 行。
3 照搬我为 `import_task.py` 加的独立 `except TaskCancelled` 分支即可（约 8 行）。
4 的最小改法是在 179 行的 commit 之前把 `chunks` 转成已经取值完毕的普通列表。
5 需要在 `deep_analyze` 入口查一次 running 任务；要不要做取决于你是否介意重复扣费。
6–11 属于清理，随时可做。
