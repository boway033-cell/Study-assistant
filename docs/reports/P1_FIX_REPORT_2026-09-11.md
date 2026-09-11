# P0 / P1 缺陷修复交付说明

日期：2026-09-11
依据：`docs/reports/CODE_AUDIT_2026-09-08.md`
备份：`.workbuddy/backups/2026-09-11-p1-final/`（`backend_app` + `frontend_src`）

---

## 一、范围结论

审计文档中 **P0 五项 + OCR 边缘项** 在 2026-09-09 已修完，经复核确认当前代码状态有效。
本轮把 **P1 剩余全部条目**修完，未留 P1 尾巴：

| 分区 | 条目 | 状态 |
|---|---|---|
| 后端 | 9、13（去重批量）、16、17 | 本轮修复 |
| 后端 | 7、8、10、11、12、13（SimHash）、14、15 | 2026-09-09 已修，本轮复核有效 |
| 前端 | 21、23、24、25、26、27、28、29 | 本轮修复 |
| 前端 | 18、19、20、22 | 2026-09-09 已修，本轮复核有效 |
| 设计/契约 | 30–38 | 本轮修复 |
| 架构 | 6（双队列同 event loop） | **有意不做**，见下 |

---

## 二、逐条方案与改动点

### 后端

**#9 `/api/knowledge/records` 全表载入后内存分页** — `backend/app/api/knowledge.py`
根因：三条语句各自 `.all()` 全表载入，Python 过滤/排序/切片。
方案：把 `scope / color / date / chapter_id / q(ilike) / tag(tags_json LIKE)` 全部下推 SQL；标注的 `record_type` 也下推为 SQL 条件（`trim(note) <> ''` → annotation，否则 `coalesce(mark_type,'highlight') IN wanted_mark`）；每源加 `ORDER BY created_at DESC LIMIT page*page_size`（数学等价：全局倒序取第 page 页，任一源中排在前 `page*page_size` 之外者不可能入页）；`total` 改由三条同条件 `COUNT` 累加，保持精确。
返回结构与 `content_length/quote_length` 截断行为不变。

**#13 前半 去重按书逐个查 chunks** — `backend/app/worker/import_task.py`
根因：对每本 ready 书各发一次 `Chunk` 查询（N+1）。
方案：改一次批量查询 `where(book_id IN (...), chunk_index < 20) ORDER BY book_id, chunk_index`，Python 内按 `book_id` 分组后再算 SimHash；`chunk_index` 自 0 起且连续（`services/rag/chunker.py:146`），与原 `order_by(chunk_index).limit(20)` 等价。命中即 return 的早退语义与 `duplicate_of` 写入不变。

**#16 `office_render` 全局锁持锁跑 subprocess** — `backend/app/services/office_render.py`、`api/books.py`、`api/annotations.py`
根因：同步端点持 `_render_lock` 跑 `subprocess.run(timeout)`，占 FastAPI 线程池；超时后 `subprocess.run` 不回收子进程。
方案：改 `Popen` + `communicate(timeout=)`；超时后仅 `taskkill /F /T /PID <本模块启动的 powershell>`，**不触碰用户自己打开的 Office**（符合审计对误伤的顾虑）。渲染移入独立 `ThreadPoolExecutor(max_workers=1)`，`books.py` 端点改 `async` + `await`；`annotations.py` 端点改 `async` + `asyncio.to_thread`。缓存命中、`rendered_pdf_path`、`office_renderer_available` 未动。

**#17 literature 错误处理** — `backend/app/api/literature.py`、`services/literature_access.py`
根因：`except Exception → HTTPException(400, str(exc))` 回显内部文本/路径；httpx 调用未捕获 `httpx.HTTPError`。
方案：新增领域异常 `LiteratureNetworkError(ValueError)`；`_read_probe` / `resolve_candidates(Unpaywall)` / `download_verified_pdf` 三处 httpx 调用捕获 `httpx.HTTPError` 转领域错误。`import_open_access` 拆为：`HTTPException` 透传、网络类 → 503（中文文案 + `logger.warning`）、`ValueError` → 400、其它 → 500（通用文案 + `logger.error`，不回显 `str(exc)`）。既有 `resolve` 的 `ValueError→400` 分支保留以免破坏既有断言。

### 前端

**#21 连续模式全量渲染 + hlStyles 全量扫描** — `frontend/src/components/PdfReader.vue`
根因：`renderPageList` 连续模式返回全部页（468 页 = 468 个 canvas）；`hlStyles(p)` 对每页遍历全量标注并重复 `JSON.parse`（O(页数 × 标注数)）。
方案：新增 `windowRange` + `windowSpacers`，连续模式只输出窗口内页，窗口上下用等高度 `.pr-spacer` 撑出总滚动高度（总高度 = 原「所有页高 + 每页 gap」之和，滚动不跳变）；`renderVisible` 更新窗口后 `await nextTick()` 等新增页 canvas 挂载再渲染。`hlStyles` 改为 `hlIndex` computed 一次建成 `Map<page, styles[]>`（O(1) 查表）。

**#29 pdf.js page 从不 `cleanup()`** — 同文件
方案：渲染成功时记 `rendered.value[p] = { pageProxy }`；新增 `cleanupPageProxy(p)`，`clearPage` 默认释放；缩放路径传 `{cleanup:false}` 保留 proxy 缓存避免重解析；切书前先逐个 cleanup 再 `destroy()`。

**#38 阅读器内解析任务无内联取消** — `frontend/src/views/ReaderView.vue`
方案：目录「保存并重建」任务记录 `rebuildTaskId`，弹窗 footer 增加「停止解析」按钮调 `cancelTask`，复用既有 `rebuildAbort` 与 `onUnmounted` 约束；`cancelled` 状态友好结束不再报错。

**#23 手写轮询无卸载清理** — `KnowledgeView.vue`（2 处）、`QuizView.vue`
方案：加组件级 `unmounted` 标志 + `onUnmounted`，每轮 `await` 后 `if (unmounted) return`；间隔/次数/成功判定/提示语义均不变，卸载后不再弹 `ElMessageBox`。

**#24 流式每 token 全量 sanitize + 无停止生成** — `ChatView.vue`、`api/index.js`
方案：流式中渲染纯文本，终态一次性 `sanitizeHtml`；`chatStream` 增加 `options.signal` 透传 `fetch`；新增「停止生成」按钮与 `onBeforeUnmount` abort，abort 后保留已生成内容。

**#25 分类下拉被当前页结果覆盖** — `LibraryView.vue`：改合并去重；`reset`（切书架/筛选/排序）时先清空再累积。

**#26 列表请求无序号** — 同文件：`listSeq` 序号守卫，响应/错误/`finally` 均按 `seq === listSeq` 判定。

**#27 MindMap 资源泄漏与全局选择器** — `MindMap.vue`：`svgEl` ref 替代 `document.querySelector('.mm-svg')`；`revoke` 幂等，`onload`/`onerror`/兜底三处释放。

**#28 错误提示上屏原始 detail** — `api/index.js`：对象详情只取白名单 `msg/message/detail`，否则回退通用文案；`error.status` / `error.retryable` 挂到 Error 上（仅新增属性，调用方不变）。

### 设计 / 契约

- **#30** `GlobalTaskCenter.vue`：新增 `canResubmit` / `goWorkspace`，非 import 类重任务失败/取消后给「回到原工作区」明确出路（按任务名路由到研读/文献汇报/阅读器/写作台），不再是死路；取消文案按任务类型区分。
- **#31** 写作台三条同步流程任务化：后端 `api/writing.py` 的 `imitate` / `clean-text` / `clean-docx` 均改 `202 + submit`；前端 `runViaTask` + `subscribeTask(signal)`，能力与综述统一。
- **#32** `StudyView.vue` `.report-content`、`WritingLabDrawer.vue` `.generate-result/.output-detail .markdown-body` 补 76ch 行宽。
- **#33** `WritingLabDrawer.vue` 字号全部 token 化（现 0 处硬编码 `font-size:Npx`）。
- **#34** 赭石系 8 种近似值收敛为 `--study-ink-strong / --study-ink / --study-ink-soft / --study-accent`（定义在 `theme/bailu.css`），替换范围限定 `App.vue` / `StudyView.vue` / `WritingLabDrawer.vue`。
- **#35** `LiteratureWorkbenchView.vue` 消费 `submittedTaskId`：订阅进度、成功刷新产出、失败报错，卸载 abort。
- **#36** `WritingLabDrawer.vue` 补确认弹窗（覆盖既有输出、云端发送等破坏性动作）。
- **#37** `theme/bailu.css` 的 `prefers-reduced-motion` 块补 `animation` 降级（`.logo-dew/.dew-dot` 停动画；`.streaming::after` 保留光标仅停止闪烁）。
- **#38** 见前端部分。

---

## 三、验证结果

| 项目 | 结果 |
|---|---|
| 后端 pytest（`pytest.ini` → `backend/tests`） | **222 passed, 1 skipped**（18.1s） |
| 前端单测（`node --test tests/*.test.js`） | **44 passed, 0 failed** |
| 前端生产构建（`vite build`） | **通过**（10.65s，产物已重建） |
| 真实浏览器走查（468 页 book 85） | 连续模式 DOM 仅 2–3 个页节点；滚动窗口 107→234→467 正确滑动；跳页 300 精确落位（scrollTop 298404 / 期望 298415）；总滚动高度 467084px 可达末页；单页/双页模式各 1/2 节点；**无控制台错误** |

修测试一处：`frontend/tests/pdf_reader_performance.test.js` 原先断言字面量 `rendered.value[p] = true`，本次记录 `pageProxy` 后失效；改为断言「标记已渲染发生在文字层渲染之前」这一行为契约，不再锁定字面量（顺带降低该类源码正则测试的脆性）。

---

## 四、有意不做的项

- **#6 双队列挂同一 event loop**：`import_task.py` 的 `parse_document` 与 `subprocess.run(timeout=900)` 是同步阻塞调用，只有把整条解析链路搬成 async 或独立进程池才能治本。属架构改造，风险与工作量独立于本次 P1 批次，建议单独立项。

## 五、已知风险 / 待观察

1. **#21 非 uniform 页高文档**：占位高度用「实测页高 + gap」累加，uniform 文档精确；页高差异极大的文档落位为估算（目标页一定在窗口内并正确 emit `page`）。建议用一本版式变化大的 PDF 再走查一次。
2. **#16 超时回收**：`taskkill /T` 只作用本模块启动的 powershell 进程树；若该脚本已被系统回收，`taskkill` 静默失败（已 `check=False` 吞掉）。
3. **并发会话**：本轮执行期间检测到有**另一会话正在改同一仓库**（`models/__init__.py` 新增 `ChatSession`、`api/study.py`、`api/writing.py`、`services/writing_lab.py`、若干文档与测试）。`CHANGELOG.md` / `PROJECT_HANDOVER.md` / 产品文档本轮**未写入**以避免覆盖对方改动，合并发布时需统一整理。
