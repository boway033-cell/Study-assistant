# 待办核验报告（第二批 / 第三批）

核验时间：2026-09-11 · 方式：逐条回到源码比对（非凭记忆）

---

## 一、结论速览

你列出的 10 项里：**7 项已完成、2 项部分完成、1 项功能缺口（已当场修复）**。

| # | 条目 | 状态 | 证据（文件:行） |
|---|---|---|---|
| 5 | ReaderView：`pdfReaderKey` 绑 book.id + `onUnmounted` + loadBook 序号 | ✅ 完成 | `ReaderView.vue:228`（key = `book.id` + `pdfReloadSeq`）、`:766`（onUnmounted）、`:730/739`（`bookRequestSeq` 序号守卫） |
| 6 | PdfReader：`watch(props.src)` 重载 | ⚠️ **本次补修** | 原先只有 `watch(() => props.toc.length)` 与 `watch(mode)`，无 src 监听 |
| 6 | PdfReader：`onBeforeUnmount` 彻底销毁 | ✅ 完成 | `PdfReader.vue:1086-1102`（generation++ → 取消队列 → destroy loadingTask → 逐页 cleanup → destroy pdfDoc） |
| 6 | PdfReader：连续模式窗口化 + hlStyles 预建 Map | ✅ 完成 | `:206`（`windowSpacers`）、`:409`（`visibleRange`）、`:732-761`（`hlIndex` Map）、模板 `:66/:81` spacer |
| 7 | 全部 `subscribeTask` 补 signal | ✅ 完成 | 4 个调用点全部带 signal：`StudyView.vue:213`、`ReaderView.vue:601/666`、`WritingLabDrawer.vue:153/160`、`LiteratureWorkbenchView.vue:238` |
| 7 | 手写轮询换成可取消循环 | ✅ 完成 | `KnowledgeView.vue:224` / `QuizView.vue:125` 均有 `unmounted` 标志，三处循环体内 `if (unmounted) return` |
| 8 | books.py 翻页解析缓存 | ✅ 完成 | `books.py:42-79`（`_PAGE_TEXT_CACHE`，LRU 上限 8 + 线程锁 + 按文件失效）、`:871` 命中、`:905` 删除书籍时失效 |
| 8 | knowledge.py records 改 SQL 分页 | ✅ 完成 | `knowledge.py:177+`（谓词下推 + 每源 `LIMIT page*page_size` + 独立 COUNT 求精确 total） |
| 8 | knowledge.py 树改批量查询 | ✅ 完成 | `:53 _build_child_map`（一次取全量建父子映射）、`:72 _collect_ids` 复用映射，无递归查库 |
| 8 | retriever.py 上下文批量取 | ✅ 完成 | `retriever.py:77-87`（`get_chapter_neighbors_batch` 一次取，失败回退逐条） |
| 9 | 任务中心重试扩展到全部重任务 | ✅ 完成 | `GlobalTaskCenter.vue:36 canRetry`（import/reimport 走 `retryTask`）+ `:38 canResubmit`（其余重任务 → 「回到原工作区」），`:71-75 goWorkspace` 按类型路由 |
| 9 | 写作台三条流程改 subscribeTask | ✅ 完成 | `WritingLabDrawer.vue:160 runViaTask` 统一走 `subscribeTask` + `taskAbort.signal` |
| 9 | PPTX 提交后读取 `submittedTaskId` | ✅ 完成 | `LiteratureWorkbenchView.vue:232-238 followDeckTask` 消费 `submittedTaskId` 并订阅进度 |
| 10 | `prefers-reduced-motion` 补 animation 降级 | ✅ 完成 | `theme/bailu.css` reduce 块内 `.logo-dew/.dew-dot` 与 `.streaming::after` 均 `animation: none`（loading 旋转有意保留） |
| 10 | 字号 / 色值 token 化 | ⚠️ 部分完成 | 色值 ✅（4 个语义 token）；字号仅 `WritingLabDrawer.vue` 全量完成，其余仍有 **225 处 `font-size:Npx`** |

---

## 二、本次实际改动（唯一功能缺口）

**问题（P1 #6 遗漏项）**：`PdfReader.vue` 没有监听 `props.src`。`ReaderView.vue` 用 `:key="pdfReaderKey"` 强制重建组件，掩盖了这个问题；但另外三处使用点**都没有 `:key`**：

- `views/ChatView.vue:80` —— 点不同「引用来源」时 `src` 变化，阅读器**停留在上一本**
- `components/OriginalViewer.vue:18` —— 切换原文来源同理
- `components/DocReader.vue:20` —— 换书同理

**方案**：最小加法，新增两个 `watch`（无 `immediate`，挂载路径行为不变）：

```js
// 父组件未用 :key 强制重建时（ChatView 引用面板 / OriginalViewer / DocReader），
// src 变化必须重建文档，否则阅读器会停留在上一本。
watch(() => props.src, (next, prev) => { if (next && next !== prev) loadPdf() })

// 同一文档内换目标页（如同一本书的另一条引用）：只跳页，不拖着重载整本文档。
watch(() => props.initialPage, (next, prev) => {
  if (props.useSavedPos || !pdfDoc || !next || next === prev) return
  scrollToPage(next, false)
})
```

**安全性论证**：

1. 无 `immediate`，首屏仍由 `onMounted → loadPdf()` 单次触发，不会重复加载。
2. src 与 initialPage 同时变化时（换到另一本书的引用），两个 watcher 在同一 flush 内按注册顺序执行：src watcher 先跑，`loadPdf()` 同步段已 `pdfDoc.destroy(); pdfDoc = null`，initialPage watcher 随即命中 `!pdfDoc` 直接返回——不会对新文档做错误的二次跳页。
3. `props.useSavedPos` 为真时（ReaderView 场景）initialPage watcher 完全空转。
4. 复用既有 `loadPdf()`，其内部已含旧文档 `cleanupPageProxy` + `destroy`，不新增销毁路径。
5. 备份：`.workbuddy/backups/2026-09-11-gapfix/PdfReader.vue.bak`

**验证**：

- 提取 `<script setup>` 块 `node --check` → 语法通过
- 前端单测 **44 passed / 0 failed**
- 真实 Chromium 打开 468 页文档（/reader/85）：DOM 3 页节点、3 canvas、页码记忆位置正常、**零控制台错误**

---

## 三、实际剩余待办（按建议优先级）

### P1｜字号 token 化收尾（唯一"部分完成"项）

现状分布（`font-size:Npx` 硬编码，共 225 处 / token 使用 79 处）：

| 文件 | 硬编码处数 |
|---|---|
| `views/LibraryView.vue` | 18 |
| `views/ReaderView.vue` | 17 |
| `components/PdfReader.vue` | 13 |
| `App.vue` | 13 |
| `components/DocReader.vue` | 12 |
| `views/LiteratureWorkbenchView.vue` | 6 |
| `SettingsView / QuizView / KnowledgeView / ChatView` | 各 5 |
| `StudyView / NotesView / DrawView / MindMap / OriginalViewer …` | 各 2–4 |

**为什么上一轮没做完（有意取舍）**：全域 225 处改动会跨越 25 个文件，其中 `App.vue` 的 `.markdown-body` 用的是 `1.65em / 1.35em` 相对档位（本身合理，不应改），而 `PdfReader/ReaderView/LibraryView` 的字号与画布缩放、工具条密度耦合，盲改会改变视觉与命中区域。因此只收敛了最集中的 `WritingLabDrawer.vue`。

**建议做法**：分批做，每批 2–3 个文件 + 截图比对，优先 `LibraryView → ReaderView → PdfReader`；`App.vue` 只收敛绝对 px（26/17/14/11/10），保留 em 档位。**不建议一次性全量替换。**

### P2｜`/api/knowledge/notes` 旧兼容端点未下推

`knowledge.py:132-173` 仍是全表载入 → Python 过滤 → `items.sort()`。它是"兼容旧前端的聚合视图"，前端已切到 `/records`，属存量遗留。数据量大时仍会慢，但优先级低。

### P2｜审计文档中的其余 P2（上一轮已说明，未动）

`PdfReader/LibraryView` 组件拆分、键盘可达性、触控目标尺寸、`fitz` 句柄 `try/finally`、第四批测试（`crypto` 往返、`presentation_deck` 三个异步 task、`books.py` 剩余路由、前端 34 项源码正则测试换 jsdom 真断言）。

---

## 四、需要你决策的两点

1. **字号 token 化是否继续按文件分批推进？** 我建议按上表顺序做 3 批，每批附截图比对。
2. **`/api/knowledge/notes` 与 `P2` 一批是否开工？** 属"不修也能跑"的存量项。
