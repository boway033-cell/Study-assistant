# 代码与设计缺陷审计

日期：2026-09-08。基于 v2.2.0 + 工作区未提交的"长文档可靠性"改动，只读扫描。

> **修复进度（2026-09-09 更新）**：P0 五项中的四项 + OCR 边缘项已按"最小加法"修复并验证（备份在 `.workbuddy/backups/2026-09-08-p0/`，后端 182 项测试通过，前端单测 43 项 + Vite 生产构建通过）。
> **第三批已修（2026-09-09）**：P1 第 7、8、10、14 项。7 ✅ 翻页解析加缓存｜8 ✅ 知识树/子树 id 批量查询｜10 ✅ 删除节点解 `ref_node_id`｜14 ✅ 章节上下文批量取 + `books.py` 分页 `top_k` 修正。备份在 `.workbuddy/backups/2026-09-09-perf/`，后端 186 项测试通过。
> **本轮未动**：9（records 全表载入后过滤分页）、11（重解析期 FTS 幽灵结果）、12、13、16，以及设计契约类与前端虚拟滚动。
> OCR 边缘项 ✅ 超时后标记引擎占用并自动解除｜5 ✅ 阅读器切书已绑定 `book.id` 强制重建，并补齐卸载清理与请求序号。
> 已修 P1 片段：SSE/轮询随组件卸载中断（`ReaderView`、`WritingLabDrawer`）。

> **第四批已修（2026-09-09）**：P1 第 11、12、13 项（SimHash 部分）。11 ✅ 重解析提交即清 FTS｜12 ✅ `TaskCancelled` 独立分支（pending 而非 failed）｜13 ✅ 词哈希改 BLAKE2b。备份在 `.workbuddy/backups/2026-09-09-p1/`，后端 186 项测试通过。
> **后续不再修的原因**：16（`office_render` 全局锁 + subprocess）要把渲染搬进任务队列才治本，杀 Office 进程会误伤用户自己打开的文档；13 的查询部分与 9（`records` 全表载入）都只是规模相关的性能项，单人本地库的数据量下收益有限；6（双队列同 event loop）属架构改造。

> **第五批已修（2026-09-11）**：P1 剩余全部条目已修完。后端：9 ✅ `records` 过滤下推 SQL + 每源 `ORDER BY created_at DESC LIMIT page*page_size` + 独立 `COUNT` 求精确 total｜13 前半 ✅ 去重改一次批量取 chunks（`book_id IN (...) AND chunk_index < 20`）后内存分组｜16 ✅ `Popen` + `communicate(timeout)`，超时仅 `taskkill /T` 本模块自己拉起的进程树，渲染移入独立单线程执行器，`books.py` 端点改 `async`｜17 ✅ 新增 `LiteratureNetworkError`，网络错误 503 / 未预期 500 不再回显 `str(exc)`、不再泄漏路径。前端：21 ✅ 连续模式窗口化渲染（只渲染可视区 ±1 页，其余用等高度 spacer 撑滚动高度）+ `hlStyles` 改 `Map<page, styles[]>` 一次预索引（O(N×M) → O(M)）｜23 ✅ 三处手写轮询加卸载中止｜24 ✅ 流式改为纯文本渲染 + 终态一次性 sanitize，新增「停止生成」并支持 abort｜25 ✅ 分类下拉改合并去重（切书架时重置）｜26 ✅ 列表请求加序号丢弃旧响应｜27 ✅ 组件 ref 替代全局选择器 + `onerror`/兜底 revoke｜28 ✅ 错误详情白名单化，不再 stringify 对象上屏｜29 ✅ `PDFPageProxy.cleanup()` 随页面离开可视区/卸载/切书释放，缩放路径保留 proxy 缓存。契约：30 ✅ 任务中心对所有重任务给「重新解析」或「回到原工作区」明确出路｜31 ✅ 仿写/去 AI 味（文本/Word）改 `202 + submit` 任务化 + `subscribeTask`｜32 ✅ `StudyView`/`WritingLabDrawer` 正文补 76ch｜33 ✅ `WritingLabDrawer` 字号全部 token 化（0 处硬编码）｜34 ✅ 赭石系收敛为 `--study-ink-strong/--study-ink/--study-ink-soft/--study-accent`｜35 ✅ `submittedTaskId` 消费并订阅进度｜36 ✅ 覆盖/云端发送补确认｜37 ✅ `prefers-reduced-motion` 补 `animation` 降级（含生成光标）｜38 ✅ 目录重识别加「停止解析」内联取消。
> 备份在 `.workbuddy/backups/2026-09-11-p1-final/`。
> 验证：后端 **222 passed / 1 skipped**；前端单测 **44 passed**；`vite build` 通过；真实 Chromium 走查 468 页文档确认连续模式 DOM 仅渲染 2–3 个页节点、滚动时窗口正确滑动（107→234→467）、跳页 300 精确落位（scrollTop 298404 vs 期望 298415）、单页/双页模式各 1/2 节点、无控制台错误。

**剩余未修**：仅 6（`_worker(_queue)` 与 `_worker(_interactive_queue)` 挂同一 event loop，导致同步阻塞调用互相冻结）属架构改造，单列；及 P2 中的「组件拆分」（见上文 P2 小节，其余 P2 条目已于第六批修完）。
> 原「剩余未修 P1」：9（records 全表载入后过滤分页）、13 前半（去重按书逐个查 chunks）、16（`office_render` 全局锁持锁跑 subprocess）；6（双队列挂同一 event loop）属架构改造，单列。
范围：后端 `backend/app`（23,727 行 Python）、前端 `frontend/src`（8,261 行）、`backend/tests` + `frontend/tests`、产品契约文档。
方式：三个方向并行全量扫描 + 对全部 P0 条目逐条回到源码复核（已修正子代理报告中 1 处不准确论断）。

---

## P0 — 会丢失数据 / 后台永久停摆 / 静默错乱

| # | 位置 | 问题 | 影响 |
|---|---|---|---|
| 1 | `core/data_manager.py:246-257` | 启动时 `check_integrity()` 返回 False 就**无确认**自动 `_restore_backup(最近日备份)`；而 `check_integrity`(:74) 把任意异常（含 `database is locked`、杀软占用）都算作"损坏" | 一次启动时的文件锁竞争就可能把库回滚到昨天的备份，当天批注/笔记/研读报告静默消失；恢复前还删掉 `-wal`/`-shm`(:56-57) |
| 2 | `core/crypto.py:34-39` | `key_file.write_bytes(key)` 失败被 `except: pass` 吞掉，仍返回这把新密钥 | 写盘失败（权限/杀软）→ 下次启动换新密钥 → 库内全部 API Key 永久不可解密，界面仍显示"已连接" |
| 3 | `core/crypto.py:60-63` | `decrypt` 异常时 `return value`，把 `enc:xxx` 密文原样当明文返回 | 密文被拼进 `Authorization` 头，且失败无任何告警 |
| 4 | `worker/tasks.py:50-55` vs `:188` | `_release_completed_tasks()` 在后台 loop 线程遍历 `_task_registry`，请求线程在 `:188` 插入，全程无锁 | `RuntimeError: dictionary changed size during iteration`；异常发生在 `_worker` 的 `finally`(:155)，会**终止 worker 协程**，此后该队列所有任务永远 pending，且无自愈重启 |
| 5 | `views/ReaderView.vue:44` + `components/PdfReader.vue:1045` | `pdfReaderKey` 只在 `inspectTocPage`(:524) 递增，切书不递增；`loadPdf()` 只在 `onMounted` 调一次 | `/reader/1 → /reader/2` 时目录/标题已切到 B 书，**画布仍是 A 书**；此后的标注与 `progress_page` 全部写到错误的 bookId |

补充（P0 边缘）：`services/parser/ocr.py:57` 看门狗超时后 `shutdown(wait=False)` 不中断线程，该线程仍持有 `_OCR_ENGINE_LOCK`(:382)。下一页不会永久死锁（外层 180s 会再超时），但会**每页各泄漏一个线程 + 每页各失败一次**，长扫描件 OCR 整体报废。

---

## P1 — 明显功能错误 / 性能瓶颈 / 契约违背

### 后端

| # | 位置 | 问题 |
|---|---|---|
| 6 | `worker/tasks.py:102-103` | `_worker(_queue)` 与 `_worker(_interactive_queue)` 挂在**同一个 event loop**，而 `import_task.py:65 parse_document`、`:158 subprocess.run(timeout=900)` 是同步阻塞调用 → 2.2.0 宣称的"重任务/交互任务两条队列"实际互相冻结 |
| 7 | `api/books.py:828-833` | 阅读器每翻一页调用 `parse_document()` 全量重解析整本 PDF，无缓存 → 数百页文档单次请求数十秒 |
| 8 | `api/knowledge.py:35-46` | 知识树递归构造，每节点一次 SQL（N+1），无深度上限；`_collect_ids`(:56) 同 |
| 9 | `api/knowledge.py:184/212/242` | `/api/knowledge/records` 全表载入后在 Python 里过滤、排序、切片分页 |
| 10 | `api/knowledge.py:475-476` | 删除节点只解除 `Annotation` 外键，未解除 `KnowledgeNode.ref_node_id` 自引用 → 被引用节点删除必 500 |
| 11 | `api/books.py:883` | 重解析先删 chunks，FTS 行要等到 `import_task.py:272` 才清 → 数小时 OCR 期间检索持续返回指向已删 chunk 的幽灵结果 |
| 12 | `worker/import_task.py:359-366` | `TaskCancelled` 被当普通异常吞掉，书籍标记 `failed` → 用户取消后资料库显示"解析失败"，与任务中心"已取消"矛盾 |
| 13 | `worker/import_task.py:427/401` | 去重对全库每本书各发一次查询；`_simhash` 用 `hash(w)`，受 `PYTHONHASHSEED` 随机化 → 指纹跨进程不可复现 |
| 14 | `services/rag/retriever.py:78-81` | 每条检索结果回查全章 chunks（N+1），且 `books.py:951` 分页是结果算完后再切片 |
| 15 | `core/data_manager.py:69-75` | `sqlite3.connect` 无 try/finally，异常时连接泄漏；`main.py:316` 把内部异常文本直接返回给 `/api/health/data` |
| 16 | `services/office_render.py:62-76` | 全局 `_render_lock` 持锁跑 `subprocess.run(timeout=120)`，同步端点占满 FastAPI 线程池；超时后可能残留 WINWORD 进程 |
| 17 | `api/literature.py:174-176` | 网络/JSON/DB 错误统一压成 400 并回显 `str(exc)`（可能含内部路径）；`literature_access.py:171-177` 未捕获 `httpx.HTTPError` |

### 前端

| # | 位置 | 问题 |
|---|---|---|
| 18 | `ReaderView.vue:646` / `:197` | `progressTimer` 无 `onUnmounted` 清理（文件只 import 了 `onMounted`）；`loadBook` 把 `book.value = null` 后旧定时器抛 TypeError，最后 800ms 进度永久丢失 |
| 19 | `ReaderView.vue:658` | `loadBook` 无请求序号/AbortController，快速切书时慢响应覆盖新状态（与 P0-5 叠加） |
| 20 | `api/index.js:73` + `ReaderView.vue:582`、`WritingLabDrawer.vue:143` | `subscribeTask` 不传 `signal`（`StudyView.vue:200` 传了）→ 离开页面后 SSE + 1.8s 轮询继续，占满浏览器每域 6 条 SSE 上限 |
| 21 | `PdfReader.vue:212/66/76` | 连续模式 `v-for` 渲染**全部页**（非虚拟滚动）；模板里 `hlStyles(p)` 对每页遍历全部标注，内部每页 `JSON.parse` → 400 页 × 200 标注 = 单次重渲染 8 万次 parse |
| 22 | `PdfReader.vue:1048` | `onBeforeUnmount` 未 `renderGeneration++`、未置空 `pdfDoc`、未清 `renderQueue` → 离开后渲染任务继续在已 destroy 的文档上跑 |
| 23 | `KnowledgeView.vue:456/550`、`QuizView.vue:216` | 手写 `for` 轮询 60/120/180 次，无 onUnmounted、无取消 → 切走后继续请求，甚至在已离开的页面弹 `ElMessageBox` |
| 24 | `ChatView.vue:34/202` | 流式回答每 token 全量 `sanitizeHtml`（O(n²)，2000 token ≈ 2000 次 DOMPurify）；`chatStream` 不收 AbortSignal，无"停止生成" |
| 25 | `LibraryView.vue:407` | 分类下拉用当前页结果**覆盖**而非合并 → 翻到第 2 页后第 1 页的分类从筛选器消失 |
| 26 | `LibraryView.vue:402` | 列表请求无序号/取消，书架切换、排序、筛选并发时旧响应覆盖新结果 |
| 27 | `MindMap.vue:154-164` | `URL.revokeObjectURL` 只写在 `img.onload`，无 `onerror`；`document.querySelector('.mm-svg')` 全局取节点（多实例取错） |
| 28 | `api/index.js:14` | 错误提示直接 `JSON.stringify(detail)` 上屏 → 用户看到 `{"code":500,"traceback":...}`，且无重试入口 |
| 29 | `PdfReader.vue:442` | pdf.js page 对象从不 `.cleanup()`，operatorList/字体资源长期挂在文档缓存 |

### 设计 / 产品契约

| # | 位置 | 契约违背 |
|---|---|---|
| 30 | `GlobalTaskCenter.vue:36` | 重试只对 `import/reimport` 开放；`deck*`、`writing_dna`、`literature-review`、`study`、`deep` 失败即死路 → 违反产品文档「所有重任务都要有 …失败、重试…」 |
| 31 | `WritingLabDrawer.vue:148-154` | 仿写/去 AI 味/审阅是同步 POST（360s），只有 loading+error，无进度/取消/重试；同文件综述已用 `subscribeTask`，能力不统一 |
| 32 | `NotesView.vue:107` 之外 | 76ch 行宽只覆盖 2 处；`StudyView.vue:275` 常规报告面板、`WritingLabDrawer.vue:164-171` 写作结果**无 max-width** → 1600px 下 90+ 汉字/行 |
| 33 | 前端 `.vue` 全域 | 硬编码 `font-size:Npx` 180 处 vs token `var(--study-font-size-*)` 49 处；出现 9/10/11/15/17/18/22/24/25/27/28/30/34px 等 14 种离群值（WritingLabDrawer 30 处最集中） |
| 34 | `App.vue:253-279`、`StudyView.vue:276`、`WritingLabDrawer.vue:164-171` | 硬编码色值成片：赭石系 8 种近似值（`#8b5a2b/#6f4721/#b98a58/#9a7958/#99671d/#895c39/#a35c42/#8d7358`），主题 token 形同虚设 |
| 35 | `LiteratureWorkbenchView.vue:172/228/239` | `submittedTaskId` 写入后**从不读取** → PPTX 提交后无进度、无结果回跳，失败只有一句 error |
| 36 | `WritingLabDrawer.vue` | 全文件 0 处 confirm：云端发送与覆盖 Word 均只有静态说明，无逐次确认（对比 `KnowledgeView.vue:463`） |
| 37 | `theme/bailu.css:229-240` | `prefers-reduced-motion` 只覆盖 transition/transform，`App.vue:146/209` 与 `ChatView.vue:296` 三处 `animation` 无降级 |
| 38 | `ReaderView/PdfReader` | 阅读器内 OCR/解析任务无内联取消（`cancelTask` 引用为 0），产品文档承诺的"页级进度 + 支持取消"在阅读现场未兑现 |

---

## 测试质量（重要）

- **前端 43 项"测试"中 34 项是读源码做正则匹配**：`writing_lab.test.js:71`、`theme.test.js:19` 等 10 个文件只 `readFileSync` + `assert.match`。字符串存在 ≠ 样式生效或逻辑正确，B 部分的六态缺失、行宽失守、token 被绕过正是因此长期未被拦截。真正跑行为的只有 `diagram/officialFormat/pdf_annotations/reader_navigation` 4 个文件 9 项。
- **`core/crypto.py` 零测试**，却有 15+ 调用点（settings/llm/main），直接对应上面 P0-2/3。
- **`presentation_deck.py` 三个异步 task（`:328/:618/:659/:708`）完全无测试** —— 进度上报、权利审计、失败降级路径裸奔。
- **假测试**：`test_arch.py:88-103` 自己写 3 个 txt 再自己读回来断言；`:67-71` 只断言 dataclass 默认值；`:5-10` `isinstance(x, bool)` 恒真；`test_lightweight_document_pipeline.py:12-18` 同义反复。
- **CI 静默跳过**：`test_literature_workbench.py:272` 依赖真实 PowerPoint，CI（ubuntu）必然跳过，且无"不可用应降级为结构验收"的替代断言。
- **覆盖洼地**：`api/books.py` 36 个路由仅约 12 个被触及；`official_skills/sanmu/scripts/docx_engine.py`（711 行）零直接单测。
- 已核实为**优点**：后端 0 处 MagicMock，59 处 monkeypatch 只替换真实外部依赖；删除级联、锚点越界、任务取消、LLM 降级、公文写作门禁的断言都是真的。

---

## P2 — 可维护性

> **第六批已修（2026-09-11，/notes + P2 批次）**：除「组件拆分」与「service_registry 死代码」外全部修完。
> - `/api/knowledge/notes` ✅ `book_ids`/`q` 过滤下推 SQL（拼接串语义 + LIKE 通配符转义，与旧 Python 子串过滤 13/13 用例等价），新增 `test_note_inbox_search_semantics_after_sql_pushdown` 锁契约。
> - `parser/__init__.py` ✅ `fitz` 句柄 `try/finally`，异常路径不再锁住 uploads 的 PDF。
> - `worker/tasks.py` ✅ `_persist` 失败改记 `logger.warning(exc_info)`，不再无声丢失。
> - `KnowledgeView` ✅ `allowDrop` 环检测（目标为自身/子孙一律禁止）；两处对 ID 数组的 `deep:true` watch 去除（store 为整体替换赋值，引用比较即可感知）。
> - `ReaderView` ✅ `v-for 16` 改为由已渲染标题实测的 `artifactHeadings`，超出 16 节可跳转。
> - `PdfReader`/`DrawView`（3 处）✅ `a.click()` 后延迟 1s `revokeObjectURL`，不再被部分浏览器判定为取消下载。
> - 键盘可达 3 处 ✅：`MindMap` 节点 `role=button`+`tabindex`+Enter/Space+焦点环；知识树操作按钮补 `:focus-within` 显示；`ChatView` 历史条目可 Tab + Enter 触发。
> - 触控尺寸 2 处 ✅：`KeycapCard` compact 35→40px；`LibraryView` 收藏按钮 40×40。
> - 同批 API 链路优化：前端 `api/index.js` 语义化超时档 + 通用 `api` 适配器 + 幂等读请求单次重试；后端 SQLite PRAGMA 调优（`synchronous=NORMAL`/`busy_timeout=15000`/`cache_size=32MB`/`mmap_size=256MB`/`temp_store=MEMORY`，实测全部生效）；JSON 响应 GZip（实测 `/api/books` 48,686B→6,834B，**7.1×**，SSE 已被 Starlette 默认排除不受影响）。
> 验证：后端 **229 passed / 1 skipped**；前端单测 **44 passed**；10 个改动文件语法门禁全过；隔离实例(8011)实测 gzip/notes 通过。
> 备份在 `.workbuddy/backups/2026-09-11-p2/`。

**剩余未修**：组件拆分（`PdfReader`/`LibraryView`/`ReaderView`/`KnowledgeView`）属大重构，建议单独立项；`service_registry.get()` 仍零调用（`init/shutdown` 在 lifespan 中使用，文件本身非死代码）；`/api/knowledge/records` 的新增分页参数若被直接位置调用存在 `Query` 对象陷阱（当前无调用方，属潜在而非现实问题）。

- `PdfReader.vue` 1177 / `LibraryView.vue` 908 / `ReaderView.vue` 781 / `KnowledgeView.vue` 607 行，适合按「目录工作台 / AI 面板 / 书架树 / 标注面板」拆分。→ 剩余，建议单独立项
- `services/service_registry.py` 的 `get()` 零调用方。→ 剩余（`init`/`shutdown` 为 lifespan 所用，不能整文件删除）
- ~~`api/study.py:589-590` `except Exception as e: raise` 无意义~~ → 已在早前批次消解（现行代码为 `TaskCancelled` 透传 + 统一回滚）
- ~~`worker/tasks.py:84-85` `_persist` 全吞异常~~ → ✅ 已记日志
- ~~`KnowledgeView.vue:490` `allowDrop = () => true`，无环检测~~ → ✅ 已修
- ~~`KnowledgeView.vue:578`、`StudyView.vue:259` 对纯 ID 数组用 `deep: true` watch~~ → ✅ 已修
- ~~`ReaderView.vue:59` `v-for="i in 16"` 硬编码证据卡片数~~ → ✅ 已修
- ~~`PdfReader.vue:929`、`DrawView.vue:169-184` `a.click()` 后同步 `revokeObjectURL`~~ → ✅ 已修
- ~~键盘不可达：`MindMap.vue:19` / `KnowledgeView.vue:592` / `ChatView.vue:13`~~ → ✅ 已修
- ~~`KeycapCard.vue:142` 35px、`LibraryView.vue:89` 收藏按钮无 min-height~~ → ✅ 已修
- ~~`parser/__init__.py:64` `fitz.open` 无 try/finally~~ → ✅ 已修

---

## 建议修复顺序

**第一批（数据/停摆，先于任何新功能）**
1. `data_manager.py`：区分"真损坏"与"检测失败/被锁"，自动恢复前必须留快照 + 用户确认；恢复前禁止删 `-wal`。
2. `crypto.py`：写盘失败直接抛错；`decrypt` 失败返回 `""` 并记日志，绝不回吐密文。
3. `tasks.py`：`_task_registry` 加 `threading.RLock`，`_release_completed_tasks` 放 try/except，worker 协程加守护重启。
4. `ocr.py`：超时后标记引擎为"污损"，后续页直接快速失败 + 一次性提示，不再逐页起线程。

**第二批（阅读器链路，直接与今天的改动相关）**
5. `ReaderView` 把 `pdfReaderKey` 绑定 `book.id`，补 `onUnmounted`；`loadBook` 加请求序号。
6. `PdfReader` 加 `watch(props.src)` 重载、`onBeforeUnmount` 彻底销毁、连续模式虚拟滚动 + `hlStyles` 改为按页索引预建 Map。
7. 全部 `subscribeTask` 补 `signal`；手写轮询换成可取消循环。

**第三批（性能与契约）**
8. `books.py:828` 翻页加解析缓存；`knowledge.py` 树与 records 改为批量查询 + SQL 分页；`retriever.py` 上下文批量取。
9. 任务中心重试扩展到全部重任务类型；写作台三条流程改 `subscribeTask`；PPTX 提交后读取 `submittedTaskId`。
10. 字号/色值 token 化（180 处硬编码）；补 `prefers-reduced-motion` 的 animation 降级。

**第四批（测试）**
11. 补 `crypto` 往返测试、`presentation_deck` 三 task 的失败/降级测试、`books.py` 剩余 24 个路由。
12. 把前端 34 项源码正则测试逐步换成 jsdom 组件断言（至少先把行宽、token、六态做成真断言）。

---

## 边界说明

- 本报告为静态扫描结论，未运行应用；性能类问题（#7/#21）为代码路径推断，建议用真实大文档实测确认量级。
- P0-1 的触发条件是"启动时 DB 被占用/异常"，非必然发生，但后果不可逆。
- 已核实无问题（不必重复排查）：`markdown.js` 的 DOMPurify 配置严格（禁 `script`/`foreignObject`/`style`）；`window.open` 已带 `noopener`；API Key 未落 localStorage；取材门禁前后端均强制；一级四步导航与次级折叠已实现。
