# 大文档 OCR 性能优化开发方案

更新日期：2026-09-11  
适用项目：Study Assistant  
状态：第一、二阶段已完成（含真实测试结果，见 §4.5、§5.5）  
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

但大扫描文档仍然非常缓慢。当前运行环境有 24 个逻辑 CPU 核心，实际 OCR 后端为 RapidOCR + ONNX Runtime CPU，没有 GPU 执行器。OCR 页面仍然严格串行处理，因此总耗时基本随待识别页数线性增长。

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
- 默认从 2 个 OCR 工作实例开始，通过基准测试决定是否增加到 3 个。
- 页面图像队列必须有上限，禁止把整本文档渲染到内存。
- 每完成一页立即写入缓存，任何失败都不能破坏已经完成的页面。
- OCR 结果可乱序产生，但最终文本、结构数据和进度必须按页码归并。
- 准确率优先于单纯追求吞吐量；低分辨率结果不可靠时只重试问题页。
- 结构变更分阶段进行，先优化 OCR 内核，再改数据库状态和增量索引。
- 全部新增行为必须可通过环境变量关闭或降级到单线程路径。

## 4. 第一阶段：低风险优化

预计工期：0.5～1 个开发日。  
**实施状态：已完成（2026-09-11），第二～四阶段未开始。**

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
- 共享执行器在进程退出时最多等待当前页收尾（ThreadPoolExecutor 线程非 daemon）；
  已注册 atexit + 应用 lifespan 统一 `shutdown_ocr_runtime()`，最坏阻塞一页时长。
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

1. 增加 `OCR_WORKERS`，默认值为 2。
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
  协调器看门狗。`OCR_WORKERS` 经 `_env_int` 钳位到 [1, 4]，1 时完全不进入流水线
  （走第一阶段 `_ocr_rapid_serial` 串行路径，有专项用例锁定）。
- 引擎实例池 `get_rapid_pool(workers)` 跨任务复用，与第一阶段空闲延迟释放共用
  同一生命周期（`ocr_engine_session` 期间禁止释放；`release_ocr_engine` 同时清池）。
- 页级超时由协调器看门狗实现：超时页记 `OCRPageTimeout` 失败、不写缓存、废弃对应
  worker 实例（不补建，避免绕过卡死）；全部 worker 卡死时抛出 `OCRPageTimeout`，
  语义与串行路径一致。卡死线程真正结束后解除 `_OCR_ENGINE_STUCK`。
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
OCR_WORKERS=2
OCR_RENDER_AHEAD=3
OCR_MAX_IMAGE_QUEUE=4
OCR_ONNX_INTRA_THREADS=6
OCR_ONNX_INTER_THREADS=1
OCR_CONTINUE_ON_PAGE_ERROR=true
```

全部经 `_env_int/_env_bool` 钳位（workers ∈ [1,4]，render_ahead/queue ∈ [1,16]）。

### 5.4 验收标准

- 100 页测试文档完成 1、2、3 worker 基准测试；
- 默认双 worker 相对新单 worker 的目标提升不低于 1.5 倍；
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
| 重试跳过成功页 | ✅ 通过 | 失败页不写缓存：`test_page_error_continues_when_enabled`；续跑用例沿用第一阶段 |
| 1/2/3 worker 基准与 1.5 倍提升 | ⏳ 未做 | 需要 100 页以上真实扫描文档与耗时测量；当前仅验证了双 worker 并发事实（`test_pipeline_workers_run_concurrently`，max_concurrency=2）。基准测试留待拿到不含隐私的扫描样本后按 §9 执行。 |

### 5.5 真实测试结果（2026-09-11，本机）

| 测试范围 | 结果 |
| --- | --- |
| 第一阶段专项 `test_ocr_phase1.py` | 24 passed |
| 第二阶段专项 `test_ocr_phase2.py` | 15 passed |
| 两阶段合计 | 39 passed（约 21 s） |
| 后端全量（全新 basetemp） | 268 passed, 1 failed, 1 skipped（114 s） |

全量中唯一失败 `test_literature_workbench.py::test_real_powerpoint_render_when_available`
为环境性失败：该 PPT 渲染测试在全量长会话中触发了沙箱 safe-delete 的"单轮累计删除
>50 文件"守卫（自行清理 372 个渲染临时文件）；单独运行该文件 19 passed / 1 skipped
全部通过，与 OCR 改动无关。

### 5.6 第二阶段已知限制

- 多 worker 的真实吞吐提升未经基准测试确认（缺 100 页以上可用扫描样本）。
- 当前安装的 `rapidocr_onnxruntime` 不支持 intra/inter 线程数下发，配置暂为
  best-effort no-op；CPU 争抢仅靠 worker 数 ≤4 与 onnxruntime 默认行为兜底。
- 超时废弃的 worker 线程不可强杀（Python 限制），线程已 daemon 化，不阻塞进程退出；
  其引擎实例在被废弃期间不释放、不补建。
- tesseract / paddleocr 回退路径仍为串行单实例（第二阶段只覆盖 RapidOCR 路径）。
- 失败页只在指标与进度消息中体现（"失败 N 页，重跑会自动补识别"），未做任务中心
  结构化展示（属第四阶段范围）。

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

完成第二阶段后，24 逻辑核心的当前机器先使用以下保守配置：

```env
OCR_PAGE_THRESHOLD=30
OCR_PAGE_TIMEOUT_SECONDS=180
OCR_RENDER_DPI=120
OCR_LARGE_DOCUMENT_DPI=96
OCR_USE_ANGLE_CLS=false

OCR_WORKERS=2
OCR_RENDER_AHEAD=3
OCR_MAX_IMAGE_QUEUE=4
OCR_ONNX_INTRA_THREADS=6
OCR_ONNX_INTER_THREADS=1
OCR_ENGINE_IDLE_SECONDS=300
OCR_SKIP_BLANK_PAGES=true
```

不能直接把 `OCR_WORKERS` 设置成 12 或 24。RapidOCR 推理实例内部也会使用多线程，过高并发可能导致线程争抢、内存增长和吞吐量下降。

## 9. 基准测试方案

选择不含隐私、允许用于测试的一份典型中文扫描 PDF，建议 100 页以上。测试前记录文档特征：

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
