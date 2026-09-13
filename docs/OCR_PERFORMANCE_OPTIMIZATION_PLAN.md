# 大文档 OCR 性能优化开发方案

更新日期：2026-09-13
适用项目：Study Assistant  
状态：第一、二阶段已完成；216 页真实性能基准已验收（见 §5.4、§9.1）
目标环境：Windows、本地 CPU OCR、RapidOCR + ONNX Runtime

## 1. 背景

当前系统已经具备以下 OCR 能力：

- 仅对 PDF 启用扫描页识别；
- 混合 PDF 只识别文本层不足的页面；
- 按文件哈希和页码保存 OCR 缓存；
- 中断后可跳过已经完成的页面；
- 大于 200 个待识别页时自动降低渲染分辨率；
- 单页识别具有超时看门狗；
- OCR、交互式 AI 和其他重型任务使用隔离队列。

大扫描文档仍然耗时较长。当前运行环境有 24 个逻辑 CPU 核心，实际 OCR 后端为 RapidOCR + ONNX Runtime CPU，没有 GPU 执行器。系统已实现可选的有限并发流水线，但真实基准确认当前后端的单实例已使用大部分 CPU，多实例会争抢资源；因此默认仍采用单 worker，耗时基本随待识别页数线性增长。

## 2. 当前瓶颈

主要瓶颈位于：

- `backend/app/services/parser/ocr.py`
- `backend/app/worker/import_task.py`
- `backend/app/worker/tasks.py`
- `backend/app/core/config.py`

已确认的问题：

1. 全局 `_OCR_ENGINE_LOCK` 使所有页面只能逐页串行识别。
2. `_run_with_timeout()` 每页创建并销毁一个 `ThreadPoolExecutor`。
3. 页面渲染、颜色转换和 OCR 推理没有形成生产者—消费者流水线。
4. 每份导入任务结束后都会释放 OCR 引擎，连续导入需要反复加载模型。
5. 文本少于固定字符阈值的页面全部进入 OCR，可能误处理空白页、封面、插图页和只有页码的页面。
6. 大文档虽然降低至 120 DPI，但没有清晰页快速识别、困难页高分辨率重试机制。
7. OCR 完成后才统一清洗、切块和索引，用户必须等待整本书处理完成。
8. OCR 在总进度中只占 `0.15 → 0.30`，数百页任务会长时间停留在很窄的进度区间。

## 3. 设计原则

- 不直接按 CPU 核心数创建 OCR 实例；RapidOCR 和 ONNX Runtime 自身也会使用多线程。
- 默认使用 1 个 OCR 工作实例；2~4 仅保留用于线程控制或推理后端变化后的诊断复测。
- 页面图像队列必须有上限，禁止把整本文档渲染到内存。
- 每完成一页立即写入缓存，任何失败都不能破坏已经完成的页面。
- OCR 结果可乱序产生，但最终文本、结构数据和进度必须按页码归并。
- 准确率优先于单纯追求吞吐量；低分辨率结果不可靠时只重试问题页。
- 结构变更分阶段进行，先优化 OCR 内核，再改数据库状态和增量索引。
- 全部新增行为必须可通过环境变量关闭或降级到单线程路径。

## 4. 第一阶段：低风险优化

预计工期：0.5～1 个开发日。  
**实施状态：已完成（2026-09-11）；第二阶段也已完成，第三、四阶段未开始。**

### 4.1 修改范围

- `backend/app/services/parser/ocr.py`
- `backend/app/core/config.py`
- `.env.example`
- OCR 专项测试

实际交付（按最小增量原则增补了必要接线，均为第一阶段实现所需）：

- `backend/app/services/parser/ocr.py`：核心实现
- `backend/app/core/config.py`、`.env.example`：4 个新配置 + 安全边界
- `backend/app/worker/import_task.py`：接入 `on_metrics` 回调并落日志、OCR 结束改为延迟释放
- `backend/app/services/rag/toc_rebuild.py`：目录补页 OCR 的释放同步改为延迟释放（否则会把刚预热的模型立即卸载）
- `backend/app/main.py`：应用退出时统一清理 OCR 运行时（6 行，无其他改动）
- `backend/tests/test_ocr_phase1.py`：新增 24 个专项用例

### 4.2 开发内容

1. 将每页独立线程池改成任务级共享线程池。
2. 任务结束后不立即卸载 RapidOCR：
   - 导入队列仍有任务时继续复用；
   - 空闲达到配置时间后释放；
   - 应用退出时释放；
   - 内存压力过高时允许主动释放。
3. 增加空白页检测：
   - 使用低分辨率灰度缩略图；
   - 根据非白像素比例判断是否为空白；
   - 空白页写入空结果缓存并正常推进进度。
4. 增加分阶段性能指标：
   - PDF 页面渲染耗时；
   - 图像转换耗时；
   - OCR 推理耗时；
   - 缓存读取和写入耗时；
   - 平均每页耗时；
   - 当前吞吐量和剩余时间估计。
5. 保持现有缓存目录和文本缓存文件兼容。

实现差异说明（与设计不一致处）：

- 共享执行器 `max_workers=1`，第一阶段仍严格单页串行；多 worker 属于第二阶段。
- "内存压力过高时允许主动释放"：未实现独立的内存监控器，通过 `release_ocr_engine(force=True)` / `shutdown_ocr_runtime()` 提供主动释放入口，未增加第三方内存探测依赖。
- "当前吞吐量和剩余时间估计"：吞吐（页/分钟）与平均秒/页由 `OcrMetrics.summary()` 输出；剩余时间估计沿用 `import_task` 里已有的按页 ETA（未重复实现）。
- 页缓存写入由"直接 write_text"改为临时文件原子替换（`page_NNNN.txt.tmp` → `replace`），格式保持 `page_NNNN.txt` 兼容，旧纯文本缓存仍可读取（有专项用例）。

### 4.3 新增配置

```env
OCR_ENGINE_IDLE_SECONDS=300
OCR_SKIP_BLANK_PAGES=true
OCR_BLANK_PAGE_THRESHOLD=0.008
OCR_METRICS_ENABLED=true
```

配置解析使用带边界的 `_env_int / _env_float / _env_bool`：非法值回退默认值，
数值越界夹取到安全区间（`OCR_ENGINE_IDLE_SECONDS` ∈ [0, 3600]，
`OCR_BLANK_PAGE_THRESHOLD` ∈ [0, 0.5]；0 秒 = 任务结束立即释放，即旧行为）。

### 4.4 验收标准

- 修改前后的识别正文没有无原因的内容丢失；
- 连续导入第二份文档时不重复冷启动模型；
- 空白页不执行完整 OCR；
- 中断后重新解析仍能命中已完成页面缓存；
- 单页卡死仍能触发超时；
- 性能日志不包含文档正文、API 密钥或用户隐私内容。

验收结果（全部通过，依据 §4.5 的真实测试）：

| 验收项 | 结果 | 对应用例 |
| --- | --- | --- |
| 识别正文不丢失 | ✅ 通过 | `test_blank_pages_skip_ocr_write_empty_cache_and_keep_pagination`、`test_legacy_plain_text_cache_is_still_reused`、全量回归 253 passed |
| 连续导入复用模型 | ✅ 通过 | `test_consecutive_imports_reuse_warm_engine`（第二次初始化计数 = 1） |
| 空白页不做完整 OCR | ✅ 通过 | `test_blank_pages_skip_...`（3 页合成 PDF 仅 1 次引擎调用） |
| 中断续跑命中缓存 | ✅ 通过 | `test_cached_pages_including_blank_resume_without_recognition`（重跑 0 次引擎调用，空白页空缓存也命中） |
| 单页卡死触发超时 | ✅ 通过 | `test_page_timeout_sets_stuck_flag_and_clears_after_thread_ends`、`test_stuck_engine_fast_fails_next_run_without_waiting` |
| 日志无正文/密钥/路径 | ✅ 通过 | `test_metrics_summary_has_no_text_or_paths`（摘要 JSON 不含正文 token、无 `/`、`\`、盘符） |

### 4.5 真实测试结果（2026-09-11，本机）

环境：Windows，Python 3.14（`.venv`），pytest 9.x；测试全部使用 fitz/PIL 现场合成的小 PDF/图像与假 OCR 引擎，不加载 ONNX 模型、不联网、不写真实用户 OCR 缓存。

| 测试范围 | 命令 | 结果 |
| --- | --- | --- |
| 第一阶段专项 | `pytest backend/tests/test_ocr_phase1.py -q` | 24 passed（约 10 s） |
| OCR 相关既有回归 | `pytest backend/tests/test_arch.py test_lightweight_document_pipeline.py test_long_document_regressions.py test_reliability_phase1.py -q` | 45 passed |
| 后端全量 | `pytest -q --basetemp=.pytest_tmp3`（全新 basetemp） | 253 passed, 1 skipped（约 51 s） |

已知测试环境限制（与代码无关）：直接复用残留已久的 `.pytest_tmp` 跑全量时，沙箱的
safe-delete 会拦截 pytest 清理旧 basetemp 子目录的动作（trash 报 "Some operations were aborted"），
造成 `tmp_path` 夹具 setup 阶段 ERROR（22 个）。使用全新 basetemp 后全绿，故判定为环境问题。

### 4.6 已实现的第一阶段已知限制

- 超时时被放弃的本地 OCR 线程无法真正终止（Python 限制）；保留原有
  `_OCR_ENGINE_STUCK` 快速失败语义，共享执行器在被卡线程结束前先阻塞后续提交。
- 进程退出**无法保证立即返回**：共享执行器的工作线程是 ThreadPoolExecutor 的非 daemon
  线程，解释器退出时 concurrent.futures 注册的 `_python_exit` 仍会 join 它，`shutdown(wait=False)`
  只停止调用方的等待、不能终止在途推理。因此"最坏只等一页超时时长"并非可保证的上界
  （真卡死的引擎可能让退出延迟更久）。详细说明见 §5.8。
- 空白页检测只跑在 RapidOCR 路径；tesseract / paddleocr 回退路径未启用空白跳过。
- 性能摘要只做结构化回传与日志，未做数据库迁移，也没有在 UI 展示。

## 5. 第二阶段：有限并发流水线

预计工期：1～2 个开发日。  
**实施状态：已完成（2026-09-11），第三、四阶段未开始。**

### 5.1 目标结构

```text
PDF 页面读取
    ↓
单路页面渲染，最多提前生成 3 页
    ↓
有界图像队列
    ↓
OCR 工作实例 1 ─┐
                 ├→ 按页码归并 → 页级缓存 → 进度回调
OCR 工作实例 2 ─┘
```

### 5.2 开发内容

1. 增加 `OCR_WORKERS`，默认值为 1（安全档）；2~4 保留为诊断/实验选项，真实基准已确认当前环境不应默认开启。
2. 每个工作线程持有独立 RapidOCR 实例，不共享非线程安全的推理状态。
3. 移除覆盖所有页面的单例 `_OCR_ENGINE_LOCK`，保留实例级生命周期控制。
4. 页面渲染使用单独生产者，提前渲染数量受 `OCR_RENDER_AHEAD` 控制。
5. 图像队列使用固定容量，默认最多保存 4 页。
6. 工作线程完成识别后立即：
   - 更新页文本；
   - 保存文本缓存；
   - 保存 RapidOCR 坐标；
   - 上报页级进度；
   - 释放页面图像。
7. 最终返回前按 1-based 页码归并结果，禁止使用完成顺序代替文档顺序。
8. 页面失败策略：
   - 缓存成功页；
   - 记录失败页码和错误类别；
   - 根据配置继续或停止；
   - 取消后不再领取新页面。
9. 限制每个 ONNX Runtime 实例的内部线程数，避免两个实例分别占满全部 CPU。
10. 保留 `OCR_WORKERS=1` 的兼容路径，作为故障回退方式。

实现要点与差异说明：

- 流水线为 `_run_rapid_pipeline()`：单生产者线程 + N 个 daemon worker 线程 +
  协调器看门狗。`OCR_WORKERS` 经 `_env_int` 钳位到 [1, 4]，**默认 1** 时完全不进入
  流水线（走第一阶段 `_ocr_rapid_serial` 串行路径，有专项用例锁定）。
  2~4 为实验性并发档。2026-09-12 的 216 页真实基准确认当前环境下多 worker
  会造成负加速，因此只保留给后续更换推理后端或线程控制生效后的诊断复测。
- 引擎实例池 `get_rapid_pool(workers)` 跨任务复用，与第一阶段空闲延迟释放共用
  同一生命周期（`ocr_engine_session` 期间禁止释放；`release_ocr_engine` 同时清池）。
- 页级超时由协调器看门狗实现：超时页记 `OCRPageTimeout` 失败、不写缓存、**真正退役**
  对应 worker——该 worker 的迟到调用返回/抛错后立即退出线程，不再领取新页，也不补建
  替代实例（避免绕过卡死）；迟到的结果/异常同样丢弃，不写文本、版面或页缓存。
  只有当所有 worker 都被退役（已无可用实例）时抛出 `OCRPageTimeout`，语义与串行路径
  一致。`_OCR_ENGINE_STUCK` 精确反映"仍有卡死 worker 的迟到调用未返回"：仅当所有卡死
  worker 的调用都返回后才清除，一个 worker 恢复不会误清其他仍卡住的标记。
- 乱序归并：worker 只写 `texts[page_no-1]`，与完成顺序无关
  （用例：第 1 页延迟 0.3s、其余 0.01s，结果仍按页码对齐）。
- 取消协作：checkpoint 异常/回调内 TaskCancelled 经错误通道汇总，停止领页，
  已完成页缓存保留；未消费图像统一 close 回收。
- 第 9 项线程数限制的实现差异：当前捆绑的 `rapidocr_onnxruntime` 版本的
  `OrtInferSession` 不读取 intra/inter 线程数（源码探测缓存判定），此时配置
  自动忽略并一次性记日志；安装到支持的版本后自动生效。0 = 使用 onnxruntime 默认。
- 关键缺陷修复（在第二阶段暴露并同批修复）：
  - `_iter_pdf_page_images` 曾在生成器推进时替消费方关闭上一页图像——串行安全，
    但在流水线里会关闭队列中待消费的图像，导致**所有页静默识别失败**。
    现改为所有权随 yield 转移：生成器不 close，串行/流水线各消费方负责 close。
  - 全部命中缓存时不再加载模型（第一阶段遗留）。
  - 空闲定时器竞态加锁；重试间隔沿用本次 armed 值（上限 5s），不再固定 5s。
  - `OcrMetrics` 计数加锁（并发 worker 下的 `+=` 竞态）。
  - 空白检测改为"先缩略再灰度"，避免整页灰度转换的成本。

### 5.3 新增配置

```env
# 默认 1（安全档）。真实基准显示 2~4 会产生 CPU 争抢和负加速，请勿默认开启。
OCR_WORKERS=1
OCR_RENDER_AHEAD=3
OCR_MAX_IMAGE_QUEUE=4
OCR_ONNX_INTRA_THREADS=6
OCR_ONNX_INTER_THREADS=1
OCR_CONTINUE_ON_PAGE_ERROR=true
```

全部经 `_env_int/_env_bool` 钳位（workers ∈ [1,4]，render_ahead/queue ∈ [1,16]）。

### 5.4 验收标准

- 使用 100 页以上真实文档完成 1、2、3 worker 基准测试；
- 双 worker 相对新单 worker 的目标提升不低于 1.5 倍；未达到时保持单 worker 默认值；
- 实际提升必须以基准结果为准，不在产品界面承诺固定倍数；
- 峰值内存受图像队列上限约束，不随总页数持续增长；
- OCR 页文本、坐标数据和原 PDF 页码完全对应；
- 取消任务后工作线程能够收尾，不继续领取大量页面；
- 超时页面不会让整个 OCR 引擎永久锁死；
- 重试时跳过已经成功的页面。

验收结果：

| 验收项 | 结果 | 依据 |
| --- | --- | --- |
| 页码/结构对应 | ✅ 通过 | `test_pipeline_merges_out_of_order_results_by_page_number`（乱序完成仍按页码归并） |
| 峰值内存有界 | ✅ 通过 | `test_pipeline_image_queue_is_bounded`（队列容量=配置）+ RENDER_AHEAD 信号量 |
| 取消收尾 | ✅ 通过 | `test_cancellation_stops_pipeline_and_keeps_completed_cache` |
| 超时不锁死引擎 | ✅ 通过 | `test_timeout_page_fails_but_others_complete`（其余页完成、stuck 线程结束后解除标记）、`test_all_workers_stuck_raises_timeout_fast`（全卡快速失败） |
| 超时 worker 真正退役 | ✅ 通过 | `test_retired_worker_drops_late_result_and_stops_claiming`（迟到结果不写文本/缓存、不重试该页） |
| STUCK 精确语义 | ✅ 通过 | `test_stuck_flag_clears_only_after_all_stuck_workers_return`（单 worker 恢复不清其他卡死标记） |
| 结算与退役原子性（TOCTOU） | ✅ 通过 | `test_finish_worker_call_never_observes_settled_without_retired`（Barrier 强制并发，finish 绝不见"已结算未退役"）、`test_settle_page_timeout_rechecks_stale_snapshot`（过期快照不误结算） |
| 引擎生命周期竞态 | ✅ 通过 | `test_release_ocr_engine_refuses_during_active_session`、`test_idle_timer_does_not_release_engine_during_active_session`（会话内定时器到期不释放） |
| 池无丢失/无重复加载 | ✅ 通过 | `test_get_rapid_pool_concurrent_release_no_loss_or_duplicate`（会话内并发 release 全拒绝、只加载 2 实例） |
| 退出幂等 | ✅ 通过 | `test_shutdown_ocr_runtime_is_idempotent`（shutdown/release 多次调用无副作用） |
| OCR 进度单调不回退 | ✅ 通过 | `test_ocr_progress_reporter_is_monotonic_under_concurrency`（多线程乱序写入值单调非降） |
| 重试跳过成功页 | ✅ 通过 | 失败页不写缓存：`test_page_error_continues_when_enabled`；续跑用例沿用第一阶段 |
| 1/2/3 worker 真实基准 | ✅ 完成 | 216 页、120 DPI、零缓存命中的 RapidOCR 实测：1/2/3 worker 分别为 472.64/570.26/1019.59 s；输出逐页一致、均无失败或超时，见 §9.1。 |
| 双 worker 1.5 倍提升目标 | ❌ 未达到 | workers=2 相对 workers=1 为 0.829 倍（慢 20.7%）；workers=3 为 0.464 倍（慢 115.7%）。保持默认 `OCR_WORKERS=1`。 |

### 5.5 真实测试结果（2026-09-12 审查修正后，本机）

| 测试范围 | 结果 |
| --- | --- |
| 第一阶段专项 `test_ocr_phase1.py` | 24 passed（约 7 s） |
| 第二阶段专项 `test_ocr_phase2.py` | 24 passed（约 26 s，含原子性/生命周期确定性用例） |
| OCR 相关既有回归（arch / lightweight / long-doc / reliability） | 45 passed |
| 后端全量（全新 basetemp） | 280 passed, 1 skipped（约 72 s） |

说明：commit `147605a` 当时的记录为「268 passed, 1 failed, 1 skipped」——唯一失败
`test_literature_workbench.py::test_real_powerpoint_render_when_available` 为环境性失败：
该 PPT 渲染测试在全量长会话中触发沙箱 safe-delete 的"单轮累计删除 >50 文件"守卫
（自行清理 372 个渲染临时文件），与 OCR 改动无关；单独运行该文件全部通过。本次修正后
的全量在全新 basetemp 下为 280 passed / 1 skipped，PPT 测试未再触发该守卫。
2026-09-12 又完成了 216 页真实文档的 1/2/3 worker 基准；基准没有修改产品代码或正式
配置，结论为当前环境继续保持 `OCR_WORKERS=1`，详见 §9.1。

### 5.6 第二阶段已知限制

- 216 页真实基准已确认当前环境的多 worker 不会提升吞吐：workers=2/3 均比 workers=1
  更慢并占用更多内存。在 ONNX 内部线程限制真正生效或推理后端变化前，不建议开启。
- 当前安装的 `rapidocr_onnxruntime` 不支持 intra/inter 线程数下发，配置暂为
  best-effort no-op；CPU 争抢仅靠 worker 数 ≤4 与 onnxruntime 默认行为兜底。
- 超时废弃的 worker 线程不可强杀（Python 限制），线程已 daemon 化，不阻塞进程退出；
  其引擎实例在被废弃期间不释放、不补建。
- tesseract / paddleocr 回退路径仍为串行单实例（第二阶段只覆盖 RapidOCR 路径）。
- 失败页只在指标与进度消息中体现（"失败 N 页，重跑会自动补识别"），未做任务中心
  结构化展示（属第四阶段范围）。

### 5.8 退出等待语义（准确说明）

Python **无法强制终止本地 OCR 线程**——`threading` 没有 kill API，`future.cancel()` 只能
取消尚未开始的任务，对已在执行的推理无效。因此：

- 超时（`page_timeout_seconds`）停止的只是**调用方的等待**，识别线程仍在后台跑，
  由 `_OCR_ENGINE_STUCK` 让后续提交快速失败，避免再空等一整轮。
- 流水线的 worker 线程是 daemon 线程，进程退出时不会 join、不会阻塞；
  但**共享执行器**（串行路径 `get_ocr_executor()`）的工作线程是非 daemon 的
  ThreadPoolExecutor 线程。concurrent.futures 在解释器退出时注册的 `_python_exit`
  仍会 join 它们，`shutdown(wait=False)` 只停止当前调用方的等待、不能终止在途推理。
- 因此 `shutdown_ocr_runtime()` 与 atexit/lifespan 清理**不保证**"立即退出"或
  "最坏只等一页超时时长"：若某次推理真正卡死（而非慢），退出可能被该线程无限期拖住。
  可接受的上界是"在途推理自然返回或彻底失败"，而非任何固定时长。
- 退出路径（`force=True`）只清引擎引用、不等待在途调用：正在执行的调用持有自己的
  实例引用不会崩，之后再次取引擎会重新加载模型。

## 6. 第三阶段：自适应识别质量

预计工期：1 个开发日。

### 6.1 用户模式

| 模式 | 首次 DPI | 方向检测 | 用途 |
| --- | ---: | --- | --- |
| 快速 | 96 | 关闭 | 清晰印刷稿 |
| 均衡 | 120 | 关闭 | 默认设置 |
| 精细 | 160 | 按需 | 小字、模糊稿、复杂表格 |

### 6.2 开发内容

1. 首次使用所选模式的 DPI 识别。
2. 综合以下指标判断页面是否需要重试：
   - 有效字符数量；
   - OCR 平均置信度；
   - 异常字符比例；
   - 页面图像是否明显包含文字；
   - 识别结果是否只有零散字符。
3. 只对低质量页面使用 160 DPI 或启用方向检测重试。
4. 不用更高 DPI 的空结果覆盖已有较好结果。
5. OCR 缓存元数据加入：
   - 引擎名称；
   - 引擎/模型版本；
   - DPI；
   - 是否启用方向检测；
   -质量档位；
   - 置信度摘要。
6. 缓存兼容：旧版纯文本缓存仍可读取，但无法确认参数匹配时应标记为旧缓存。

### 6.3 新增配置

```env
OCR_MODE=balanced
OCR_FAST_DPI=96
OCR_BALANCED_DPI=120
OCR_PRECISE_DPI=160
OCR_RETRY_LOW_CONFIDENCE=true
OCR_CONFIDENCE_THRESHOLD=0.72
OCR_MIN_VALID_CHARS=20
```

### 6.4 验收标准

- 清晰页不会无条件使用高 DPI；
- 模糊页能够触发精细重试；
- 重试前后的 DPI、置信度和选择结果可追踪；
- 质量档位变化不会错误复用不兼容缓存；
- 精细模式仍受图像队列和内存限制；
- 同一测试集的关键文字召回率不低于修改前。

## 7. 第四阶段：渐进式可用与增量索引

预计工期：2～3 个开发日。

### 7.1 修改范围

- `backend/app/worker/import_task.py`
- `backend/app/worker/tasks.py`
- 数据模型及迁移
- 全文检索和向量索引服务
- 文献库与任务中心界面

### 7.2 处理流程

```text
每完成 10～20 页 OCR
    ↓
清洗当前批次
    ↓
生成稳定的页级或章节分块
    ↓
幂等追加全文索引
    ↓
更新已识别页数和已索引页数
    ↓
后台继续处理剩余页面
```

### 7.3 状态设计

- `ocr_pending`：等待 OCR；
- `ocr_running`：正在识别；
- `partially_ready`：部分页面已经可以检索；
- `indexing`：正在补充索引；
- `ready`：全部完成；
- `ocr_partial_failed`：部分页面失败，成功内容仍可使用。

建议记录：

- `ocr_total_pages`；
- `ocr_completed_pages`；
- `ocr_failed_pages`；
- `indexed_through_page`；
- `ocr_mode`；
- `ocr_engine`；
- `ocr_started_at`；
- `ocr_updated_at`。

### 7.4 界面要求

任务中心应展示真实阶段，而不是长期停留在固定进度区间：

```text
已识别 86 / 420 页
已可检索 80 页
平均 2.1 秒/页
预计剩余约 12 分钟
```

全文检索在文档未完成时应标注：

```text
该资料当前完成 80 / 420 页，结果可能不完整。
```

### 7.5 验收标准

- 前 10～20 页完成后可检索已完成内容；
- 批次重试不会重复写入相同分块；
- 程序重启后可从缓存及持久化状态恢复；
- 暂停或取消后不删除成功页面；
- 搜索结果明确说明资料是否完整；
- 完成后自动转为完整可用；
- 旧文档、旧任务记录和旧索引继续可读。

## 8. 推荐初始参数

完成第二阶段后，24 逻辑核心的当前机器先使用以下保守配置（`OCR_WORKERS=1` 为安全默认，
2~4 为实验性并发，需以 §9 的真实基准确认收益后再开启）：

```env
OCR_PAGE_THRESHOLD=30
OCR_PAGE_TIMEOUT_SECONDS=180
OCR_RENDER_DPI=120
OCR_LARGE_DOCUMENT_DPI=96
OCR_USE_ANGLE_CLS=false

OCR_WORKERS=1
OCR_RENDER_AHEAD=3
OCR_MAX_IMAGE_QUEUE=4
OCR_ONNX_INTRA_THREADS=6
OCR_ONNX_INTER_THREADS=1
OCR_ENGINE_IDLE_SECONDS=300
OCR_SKIP_BLANK_PAGES=true
```

不能直接把 `OCR_WORKERS` 设置成 12 或 24。RapidOCR 推理实例内部也会使用多线程，过高并发可能导致线程争抢、内存增长和吞吐量下降。

## 9. 真实基准结果与后续复测方案

### 9.1 已完成实测（2026-09-12）

在本机使用一份 216 页、7,040,604 bytes、未加密的中文混合文本层 PDF，强制对全部
页面执行 OCR。三种配置分别在独立 Python 进程和全新临时 `DATA_DIR` 中串行运行，保持
RapidOCR 后端、120 DPI、180 秒页超时、0.008 空白阈值和相同页面集合不变；三轮缓存
命中均为 0。测试没有修改正式配置，报告只保留聚合指标和文本哈希，不保存正文。

环境：24 逻辑核心、15.7 GB 内存、`rapidocr_onnxruntime 1.2.3`、ONNX Runtime 1.28.0。
当前 RapidOCR 版本不支持下发 ONNX intra/inter 线程限制，单实例已能使用大部分 CPU。

| 指标 | workers=1 | workers=2 | workers=3 |
| --- | ---: | ---: | ---: |
| 总墙钟时间 | **472.64 s** | 570.26 s | 1019.59 s |
| 平均秒/页 | **2.188** | 2.640 | 4.720 |
| 吞吐量 | **27.42 页/分钟** | 22.73 页/分钟 | 12.71 页/分钟 |
| 相对 workers=1 | 1.000 倍 | **0.829 倍** | **0.464 倍** |
| 平均整机 CPU 占用 | 78.72% | 94.65% | 91.33% |
| 峰值 Working Set | 947.9 MB | 1120.4 MB | 1294.1 MB |
| OCR 成功/失败/超时 | 216/0/0 | 216/0/0 | 216/0/0 |
| 缓存命中 | 0 | 0 | 0 |

正确性核验：三轮均得到 215 个非空页和 1 个空文本页，识别字符总数均为 152,258；
216 页的文本哈希与页面状态逐页一致，没有发现并发导致的识别差异。

结论：workers=2 比 workers=1 慢 20.7%，workers=3 慢 115.7%，且峰值内存随实例数
增加。双 worker 的 1.5 倍目标未达到，方向反而为负。当前正式默认值应保持
`OCR_WORKERS=1`；只有在 ONNX 线程限制真正生效、推理后端或硬件环境改变后，才值得
重新评估多 worker。

适用边界：每种配置只测量一轮，覆盖 RapidOCR、120 DPI 和这一份 216 页样本；数值不应
外推为所有文档的固定性能承诺。但 0.829/0.464 倍的差距明显，足以支持当前默认值决策。
分阶段 `ocr` 指标是多个 worker 阶段墙钟时长之和，不应解释为操作系统 CPU 时间；CPU
数据来自独立的进程级采样。

### 9.2 后续复测方案

当 ONNX 线程控制、RapidOCR 版本、推理后端或硬件环境变化时，选择不含隐私、允许用于
测试的典型中文扫描 PDF（建议 100 页以上）重新执行基准。测试前记录文档特征：

- 页数；
- 页面尺寸；
- 平均扫描分辨率；
- 黑白或彩色；
- 是否包含小字、表格、倾斜页和空白页；
- 文件大小。

依次测试：

1. 原始串行实现；
2. 新实现，`OCR_WORKERS=1`；
3. 新实现，`OCR_WORKERS=2`；
4. 新实现，`OCR_WORKERS=3`；
5. 双 worker + 96 DPI；
6. 双 worker + 120 DPI；
7. 双 worker + 自适应重试。

每轮至少记录：

- 总耗时；
- 页面渲染耗时；
- OCR 推理耗时；
- 平均秒/页；
- 每分钟处理页数；
- 峰值内存；
- 平均 CPU 使用率；
- 成功、失败、缓存和重试页数；
- 识别字符总数；
- 固定样本页的人工准确率；
- 取消响应时间；
- 第二次运行的缓存命中耗时。

最终配置按“吞吐量、峰值内存、识别准确率”共同决定，不能只看 CPU 使用率。

## 10. 测试清单

### 单元测试

- 页面目标筛选；
- 空白页判断；
- 缓存读取、写入和版本匹配；
- 乱序识别结果按页码归并；
- 失败页不会覆盖成功缓存；
- 低置信度触发重试；
- 高质量结果不会被较差结果覆盖；
- worker 数和队列容量边界；
- 配置非法值回退或拒绝；
- 性能日志不含正文。

### 集成测试

- 纯扫描 PDF；
- 混合文本层 PDF；
- 包含空白页、封面和插图页的 PDF；
- 300 页以上大文档；
- 单页 OCR 超时；
- 单页异常但允许继续；
- 用户取消；
- 应用重启后续跑；
- 连续导入多份文件并复用模型；
- OCR 期间全文检索部分可用；
- OCR 完成后全文索引与页码一致。

### 回归测试

- PDF 阅读器仍可显示原文件；
- OCR 透明文字层坐标仍正确；
- 文献详情、目录、章节和全文检索页码不偏移；
- 任务中心不会阻塞交互式 AI；
- 非扫描 PDF 不会错误进入 OCR；
- Word 和 PPT 导入不受影响。

## 11. 安全与资源边界

- 图像队列必须有固定上限；
- 不记录 OCR 正文到性能日志；
- 不自动上传扫描内容到远程 OCR 服务；
- 不静默安装 OCR、GPU 或系统依赖；
- 不在超时后立即创建更多模型实例绕过卡死；
- 缓存写入使用临时文件后原子替换；
- 页面失败、取消和进程退出时必须关闭 PDF、图像和线程资源；
- 失败恢复不得删除原始 PDF；
- 清理缓存必须精确限定到对应文件哈希目录。

## 12. 回滚方案

第二阶段必须保留单 worker 路径。出现识别错误、内存异常或线程无法退出时，可通过以下配置回退：

```env
OCR_WORKERS=1
OCR_RENDER_AHEAD=1
OCR_MAX_IMAGE_QUEUE=1
OCR_RETRY_LOW_CONFIDENCE=false
OCR_MODE=balanced
```

回滚不得删除已生成的兼容页级文本缓存。若新版缓存加入元数据，应继续支持读取旧版 `page_NNNN.txt`。

## 13. 开发提交安排

建议拆分为独立提交，避免并发、质量策略和数据库迁移混在同一变更中：

1. `perf(ocr): add timing metrics and blank-page filtering`
2. `perf(ocr): reuse executor and keep engine warm while imports remain`
3. `perf(ocr): add bounded two-worker recognition pipeline`
4. `test(ocr): add throughput cancellation and cache regression coverage`
5. `feat(ocr): add fast balanced and precise quality modes`
6. `feat(ocr): retry only low-confidence pages at higher DPI`
7. `feat(import): persist partial OCR progress and incrementally index pages`
8. `feat(ui): expose OCR mode partial availability and accurate ETA`

每个阶段完成后单独运行基准测试和全量回归。第一、第二阶段稳定后再开始第三阶段；第四阶段涉及状态模型和索引一致性，应单独开发与审核。

## 14. 完成定义

本优化完成需要同时满足：

- 双 worker 在目标测试机上产生可重复的实际吞吐提升；
- OCR 结果顺序、页码和透明文字层坐标正确；
- 内存使用受到队列上限控制；
- 取消、超时、失败和重启均可恢复；
- 已完成页面可以续用，不重复计费或重复识别；
- 低质量页可精细重试，清晰页不承担高 DPI 成本；
- 大文档在全部完成前可以渐进式检索；
- UI 明确区分部分可用与全部完成；
- 全量后端、前端及大文档回归测试通过；
- 性能结论附带测试文档特征、参数和测量结果。
