"""OCR 可插拔层：扫描版 PDF 识别

策略：
- 检测：页均文本量低于阈值 → 判定为扫描版（无文本层）
- 后端按需加载（不预装，避免内存占用）：
  1. pytesseract（需系统安装 Tesseract，轻量）
  2. PaddleOCR（pip 安装，较重，内存占用高，可选）

默认：无 OCR 后端时抛出明确错误，提示用户安装方案。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator
import atexit
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager


class OCRPageTimeout(RuntimeError):
    """A page produced no result within the configured watchdog interval."""


def _raise_control_exception(exc: Exception) -> None:
    # Avoid importing worker.tasks here (it imports the parser through import_task).
    if isinstance(exc, OCRPageTimeout) or exc.__class__.__name__ == "TaskCancelled":
        raise exc


def _checkpoint(on_checkpoint, page_no: int, phase: str) -> None:
    if on_checkpoint:
        on_checkpoint(page_no, phase)


# ---------------------------------------------------------------------------
# 第一阶段：任务级共享执行器
# ---------------------------------------------------------------------------
# 旧实现每页新建/销毁一个 ThreadPoolExecutor：页切换要反复创建线程，
# 且 executor.shutdown() 无法复用。第一阶段改为整个 OCR 进程共享一个执行器。
# max_workers=1 是有意保留的：第一阶段仍然严格单页串行，多 worker 属于第二阶段。
_OCR_EXECUTOR: ThreadPoolExecutor | None = None
_OCR_EXECUTOR_LOCK = threading.Lock()


def get_ocr_executor() -> ThreadPoolExecutor:
    """返回共享的单 worker 执行器（第一阶段语义：一次只跑一页）。"""
    global _OCR_EXECUTOR
    with _OCR_EXECUTOR_LOCK:
        if _OCR_EXECUTOR is None:
            _OCR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr-shared")
        return _OCR_EXECUTOR


def shutdown_ocr_executor(wait: bool = False) -> None:
    """关闭共享执行器；wait=False 时不阻塞正在跑的那一页。"""
    global _OCR_EXECUTOR
    with _OCR_EXECUTOR_LOCK:
        executor, _OCR_EXECUTOR = _OCR_EXECUTOR, None
    if executor is not None:
        executor.shutdown(wait=wait, cancel_futures=True)


# ---------------------------------------------------------------------------
# 第一阶段：RapidOCR 模型延迟释放
# ---------------------------------------------------------------------------
# 旧实现每份导入任务结束就释放模型，连续导入要反复冷加载 ONNX 模型。
# 现在改为"空闲 OCR_ENGINE_IDLE_SECONDS 秒后释放"：
#   - 有识别正在进行（_OCR_ENGINE_ACTIVE > 0）绝不释放；
#   - 定时器是 daemon 且在 atexit 里取消，不会拖住进程退出；
#   - release_ocr_engine() 仍保留立即释放语义（向后兼容，供 tests/故障恢复使用）。
_OCR_ENGINE_ACTIVE = 0
_OCR_IDLE_TIMER: threading.Timer | None = None
_OCR_IDLE_SECONDS_ARMED: float | None = None  # 本次定时器采用的空闲间隔（重试沿用）
_OCR_LIFECYCLE_LOCK = threading.RLock()
_OCR_EXIT_HOOK_READY = False


def _register_exit_cleanup() -> None:
    """注册进程退出清理：取消定时器并释放模型（幂等）。"""
    global _OCR_EXIT_HOOK_READY
    if _OCR_EXIT_HOOK_READY:
        return
    _OCR_EXIT_HOOK_READY = True
    atexit.register(shutdown_ocr_runtime)


def _cancel_scheduled_release() -> None:
    """取消待执行的空闲释放定时器（新任务开始或引擎被复用时调用）。"""
    global _OCR_IDLE_TIMER
    with _OCR_LIFECYCLE_LOCK:
        timer, _OCR_IDLE_TIMER = _OCR_IDLE_TIMER, None
    if timer is not None:
        timer.cancel()


def touch_ocr_engine() -> None:
    """标记引擎刚被使用：取消待执行的空闲释放。"""
    _cancel_scheduled_release()


@contextmanager
def ocr_engine_session():
    """一段"正在使用引擎"的区间（整个 OCR 任务），期间禁止释放模型。"""
    global _OCR_ENGINE_ACTIVE
    touch_ocr_engine()
    with _OCR_LIFECYCLE_LOCK:
        _OCR_ENGINE_ACTIVE += 1
    try:
        yield
    finally:
        with _OCR_LIFECYCLE_LOCK:
            _OCR_ENGINE_ACTIVE = max(0, _OCR_ENGINE_ACTIVE - 1)


def _arm_idle_release(idle_seconds: float) -> None:
    global _OCR_IDLE_TIMER, _OCR_IDLE_SECONDS_ARMED
    if idle_seconds <= 0:
        release_ocr_engine()
        return
    _cancel_scheduled_release()
    timer = threading.Timer(idle_seconds, _on_idle_timeout)
    timer.daemon = True  # 不阻止进程退出
    with _OCR_LIFECYCLE_LOCK:
        _OCR_IDLE_TIMER = timer
        _OCR_IDLE_SECONDS_ARMED = float(idle_seconds)
    timer.start()


def _on_idle_timeout() -> None:
    """空闲定时器到点：能释放就释放，还在使用则按本次 armed 间隔短暂重试。"""
    global _OCR_IDLE_TIMER
    with _OCR_LIFECYCLE_LOCK:
        _OCR_IDLE_TIMER = None  # 本次定时器已耗尽
        armed = _OCR_IDLE_SECONDS_ARMED or _current_idle_seconds()
    if not release_ocr_engine():
        _arm_idle_release(max(0.2, min(armed, 5.0)))


def _current_idle_seconds() -> float:
    try:
        from backend.app.core.config import settings
        return float(getattr(settings, "ocr_engine_idle_seconds", 300))
    except Exception:  # noqa: BLE001
        return 300.0


def schedule_ocr_engine_release(idle_seconds: float | None = None):
    """OCR 任务结束后调用：空闲一段时间再释放模型（连续导入可直接复用）。

    返回已排定的定时器（测试用；None 表示 0 秒已立即释放）。
    """
    _register_exit_cleanup()
    if idle_seconds is None:
        idle_seconds = _current_idle_seconds()
    if idle_seconds <= 0:
        release_ocr_engine()
        return None
    _arm_idle_release(idle_seconds)
    return _OCR_IDLE_TIMER


def shutdown_ocr_runtime() -> None:
    """进程/应用退出时的统一清理：取消定时器 → 释放模型 → 关闭共享执行器。"""
    _cancel_scheduled_release()
    try:
        release_ocr_engine(force=True)
    except Exception:  # noqa: BLE001
        pass
    shutdown_ocr_executor(wait=False)


# ---------------------------------------------------------------------------
# 第一阶段：轻量性能指标
# ---------------------------------------------------------------------------
# 只累计数值。刻意不收集：页正文、文件路径、文件名、API 密钥。
class OcrMetrics:
    """OCR 分阶段耗时与页数统计（纯数值，可直接进日志/回调）。"""

    _PHASES = ("render", "convert", "ocr", "cache_read", "cache_write")
    _COUNTS = ("cached", "blank", "recognized", "failed")

    def __init__(self) -> None:
        self.seconds: dict[str, float] = {name: 0.0 for name in self._PHASES}
        self.counts: dict[str, int] = {name: 0 for name in self._COUNTS}
        self.total_seconds: float = 0.0
        self._started_at: float | None = None
        # 第二阶段：生产者/多个 worker 线程会并发 observe/bump，dict 的 += 不是原子操作。
        self._lock = threading.Lock()

    def start(self) -> "OcrMetrics":
        if self._started_at is None:
            self._started_at = time.perf_counter()
        return self

    def stop(self) -> None:
        if self._started_at is not None:
            self.total_seconds = max(0.0, time.perf_counter() - self._started_at)
            self._started_at = None

    @contextmanager
    def stage(self, name: str):
        """统计某个阶段的耗时（阶段内异常也计入，避免静默少统计）。"""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, time.perf_counter() - started)

    def observe(self, name: str, seconds: float) -> None:
        if seconds > 0:
            with self._lock:
                if name in self.seconds:
                    self.seconds[name] += seconds

    def bump(self, name: str, amount: int = 1) -> None:
        with self._lock:
            if name in self.counts:
                self.counts[name] += amount

    def summary(self) -> dict:
        """结构化摘要（可 JSON 序列化，不含正文/路径/密钥）。"""
        if self._started_at is not None:
            self.stop()
        with self._lock:
            counts = dict(self.counts)
            seconds = dict(self.seconds)
        handled = sum(counts.values())
        total = max(self.total_seconds, 0.0)
        return {
            "pages": {"total": handled, **counts},
            "seconds": {
                "total": round(total, 3),
                **{name: round(value, 3) for name, value in seconds.items()},
            },
            "avg_seconds_per_page": round(total / handled, 3) if handled else 0.0,
            "pages_per_minute": round(handled / total * 60, 2) if total > 0 else 0.0,
        }


# ---------------------------------------------------------------------------
# 第一阶段：空白页检测
# ---------------------------------------------------------------------------
# 先看缩略图灰度直方图，成本远低于一次完整 OCR 推理。
_BLANK_THUMB_WIDTH = 96
_BLANK_INK_LEVEL = 245  # 灰度低于该值算"有墨"，容忍扫描灰底但不能容忍空白页


def estimate_ink_ratio(image) -> float:
    """估算非白像素比例（0~1）。统计失败时返回 1.0 → 保守地执行 OCR。

    先按宽度缩到 96px 再做灰度统计：整页转灰度在 150+ DPI 位图上要几兆次
    像素运算，先降采样把成本压到两个数量级以下（这才算得上"低成本统计"）。
    """
    try:
        width, height = image.size
        if width <= 0 or height <= 0:
            return 1.0
        scale = min(1.0, _BLANK_THUMB_WIDTH / float(width))
        small = (
            image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
            if scale < 1.0 else image
        )
        gray = small.convert("L")
        histogram = gray.histogram()
        total = sum(histogram)
        if total <= 0:
            return 1.0
        ink = sum(histogram[:_BLANK_INK_LEVEL])
        return ink / float(total)
    except Exception:  # noqa: BLE001
        return 1.0


def is_blank_page(image, threshold: float) -> bool:
    """非白像素比例低于阈值即判定为空白页（默认阈值极低，宁可少跳）。"""
    return estimate_ink_ratio(image) <= max(0.0, threshold)


# 看门狗超时后，被放弃的识别线程仍持有 _OCR_ENGINE_LOCK（Python 无法强制终止本地线程）。
# 在此期间新任务会在锁上白等到再次超时；用此标记改为快速失败并给出明确原因。
_OCR_ENGINE_STUCK = threading.Event()


def _on_ocr_future_done(future) -> None:
    """识别线程真正结束后清除"引擎占用中"标记（无论成功、失败或被放弃）。"""
    _OCR_ENGINE_STUCK.clear()


def _run_with_timeout(callable_, timeout_seconds: int, page_no: int, on_wait=None,
                     executor: ThreadPoolExecutor | None = None):
    """在（共享的）执行器上跑一次识别，超时后快速失败。

    executor=None 时临时创建私有执行器并在收尾关闭（向后兼容旧调用）；
    传入共享执行器时不会 shutdown，交给 shutdown_ocr_runtime 统一释放。
    注意：timeout 只是看门狗，Python 不能真正终止本地 OCR 线程；
    线程仍在跑时由 _OCR_ENGINE_STUCK 让后续页面快速失败而不是再空等一轮。
    """
    owns_executor = executor is None
    if owns_executor:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ocr-page-{page_no}")
    elif executor._shutdown:  # pragma: no cover - 共享执行器只在退出时关闭
        executor = get_ocr_executor()
    future = executor.submit(callable_)
    # 看门狗无法真正中断本地 OCR 线程：超时后它仍持有引擎锁。
    # 先标记"引擎占用中"，让后续任务快速失败而不是再空等一整个超时周期，
    # 线程真正结束后由回调自动清除。
    future.add_done_callback(_on_ocr_future_done)
    started_at = time.monotonic()
    try:
        while True:
            remaining = timeout_seconds - (time.monotonic() - started_at)
            if remaining <= 0:
                future.cancel()
                _OCR_ENGINE_STUCK.set()
                raise OCRPageTimeout(
                    f"PDF 第 {page_no} 页连续 {timeout_seconds} 秒没有 OCR 结果；已完成页面缓存会保留"
                )
            try:
                return future.result(timeout=min(2.0, remaining))
            except TimeoutError as exc:
                if time.monotonic() - started_at >= timeout_seconds:
                    future.cancel()
                    _OCR_ENGINE_STUCK.set()
                    raise OCRPageTimeout(
                        f"PDF 第 {page_no} 页连续 {timeout_seconds} 秒没有 OCR 结果；已完成页面缓存会保留"
                    ) from exc
                if on_wait:
                    on_wait()  # 取消检查点：update_progress 在此抛 TaskCancelled
    finally:
        # 共享执行器不能在这里 shutdown，否则下一页就没 executor 可用了。
        if owns_executor:
            executor.shutdown(wait=False, cancel_futures=True)

# 页均字符低于此值判定为扫描版（无文本层）
SCAN_THRESHOLD = 30


def detect_scanned(pages: list[str]) -> bool:
    """检测是否为扫描版（页均文本量过低）。"""
    if not pages:
        return True
    total_chars = sum(len(p.strip()) for p in pages)
    avg = total_chars / len(pages)
    return avg < SCAN_THRESHOLD


def pages_requiring_ocr(pages: list[str], threshold: int = SCAN_THRESHOLD) -> list[int]:
    """返回文本层不足的 1-based 页码，供混合 PDF 仅识别必要页面。"""
    return [index for index, text in enumerate(pages, start=1) if len(text.strip()) < threshold]


def has_ocr_engine() -> bool:
    """检查是否安装了可用的 OCR 引擎（rapidocr / pytesseract / paddleocr）。"""
    try:
        from rapidocr import RapidOCR  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import paddleocr  # noqa: F401
        return True
    except ImportError:
        pass
    return False


from functools import lru_cache


def _file_hash(path: Path) -> str:
    path = Path(path).resolve()
    stat = path.stat()
    return _file_hash_cached(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=64)
def _file_hash_cached(path: str, size: int, modified: int, changed: int) -> str:
    """流式计算文件 SHA-256（前 16 位，作 OCR 缓存键）。"""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _ocr_cache_dir(file_hash: str) -> Path:
    """OCR 页级缓存目录（data/ocr_cache/{hash}/page_NNNN.txt）。"""
    from backend.app.core.config import settings
    d = settings.data_dir / "ocr_cache" / file_hash
    d.mkdir(parents=True, exist_ok=True)
    return d


def ocr_pdf(path: str | Path, on_progress=None, *, page_numbers: set[int] | None = None,
            base_pages: list[str] | None = None, on_page_result=None,
            on_checkpoint=None, page_timeout_seconds: int = 180,
            render_dpi: int | None = None, on_metrics=None) -> list[str]:
    """对扫描版 PDF 做 OCR，返回每页文本。

    on_progress(page_no, total, cached)：每页完成后回调（cached=True 表示命中缓存）。
    支持断点续跑：每页结果缓存到 data/ocr_cache/{file_hash}/，中断后重跑自动跳过已识别页。
    on_metrics(summary dict)：可选，接收本轮结构化性能摘要（纯数值，无正文/路径）。

    按优先级尝试后端：rapidocr（中文最佳，纯 pip）→ pytesseract → paddleocr。
    均不可用时抛出 RuntimeError 并给出安装指引。
    """
    p = Path(path)
    if p.suffix.lower() != ".pdf":
        raise RuntimeError("OCR 仅支持 PDF 文件")

    # 页级缓存目录（文件内容不变则缓存有效）
    try:
        cache_dir = _ocr_cache_dir(_file_hash(p))
    except Exception:  # noqa: BLE001
        cache_dir = None

    from backend.app.core.config import settings
    if render_dpi is None:
        target_count = len(page_numbers) if page_numbers is not None else 0
        render_dpi = (
            settings.ocr_large_document_dpi if target_count >= 200 else settings.ocr_render_dpi
        )

    # 1. RapidOCR（onnxruntime，中文效果好，纯 pip 安装）
    try:
        try:
            from rapidocr import RapidOCR  # noqa: F401
        except ImportError:
            from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        return _ocr_rapid(p, cache_dir=cache_dir, on_progress=on_progress,
                          page_numbers=page_numbers, base_pages=base_pages,
                          on_page_result=on_page_result, on_checkpoint=on_checkpoint,
                          page_timeout_seconds=page_timeout_seconds, render_dpi=render_dpi,
                          on_metrics=on_metrics)
    except ImportError:
        pass
    except OCRPageTimeout:
        raise
    except Exception as e:  # noqa: BLE001
        _raise_control_exception(e)

    # 2. pytesseract
    try:
        import pytesseract  # noqa: F401
        return _ocr_tesseract(p, cache_dir=cache_dir, on_progress=on_progress,
                              page_numbers=page_numbers, base_pages=base_pages,
                              on_page_result=on_page_result, on_checkpoint=on_checkpoint,
                              page_timeout_seconds=page_timeout_seconds, render_dpi=render_dpi,
                              on_metrics=on_metrics)
    except ImportError:
        pass
    except OCRPageTimeout:
        raise
    except Exception as e:  # noqa: BLE001
        _raise_control_exception(e)

    # 3. paddleocr
    try:
        import paddleocr  # noqa: F401
        return _ocr_paddle(p, cache_dir=cache_dir, on_progress=on_progress,
                           page_numbers=page_numbers, base_pages=base_pages,
                           on_page_result=on_page_result, on_checkpoint=on_checkpoint,
                           page_timeout_seconds=page_timeout_seconds, render_dpi=render_dpi,
                           on_metrics=on_metrics)
    except ImportError:
        pass
    except OCRPageTimeout:
        raise

    raise RuntimeError(
        "该 PDF 为扫描版（无文本层），且未检测到可用的 OCR 引擎。"
        "请任选其一安装：\n"
        "1) RapidOCR：pip install rapidocr onnxruntime（推荐，中文效果好）\n"
        "2) Tesseract：https://github.com/UB-Mannheim/tesseract/wiki 下载安装，"
        "勾选中文语言包，再 pip install pytesseract\n"
        "3) PaddleOCR：pip install paddlepaddle paddleocr（体积较大，需 Python≤3.12）"
    )


def _iter_pdf_page_images(p: Path, page_numbers: set[int] | None = None,
                          dpi: int = 180, metrics: OcrMetrics | None = None) -> Iterator[tuple[int, int, object]]:
    """逐页渲染；图像所有权随 yield 转移给消费方，由消费方负责 close()。

    注意：生成器不得在继续推进时替消费方关闭上一页——串行消费虽然安全，
    但在第二阶段流水线里图像会先进入有界队列，生成器提前 close 会让
    worker 拿到已关闭的图像（所有页静默识别失败）。内存上界由调用方保证：
    串行路径逐页处理后立即 close；流水线路径由 OCR_RENDER_AHEAD +
    OCR_MAX_IMAGE_QUEUE + worker 数共同封顶。
    """
    import fitz  # PyMuPDF
    from PIL import Image

    doc = fitz.open(p)
    total = doc.page_count
    try:
        targets = sorted(page_numbers) if page_numbers is not None else range(1, total + 1)
        for page_no in targets:
            if not 1 <= page_no <= total:
                continue
            started_at = time.perf_counter()
            page = doc.load_page(page_no - 1)
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            del pix
            if metrics is not None:
                metrics.observe("render", time.perf_counter() - started_at)
            yield page_no, total, image
    finally:
        doc.close()


_rapid_engine = None
_OCR_ENGINE_LOCK = threading.Lock()
_OCR_INIT_LOCK = threading.Lock()
# 第二阶段：每个 worker 持有独立引擎实例（RapidOCR 推理状态非线程安全，不能跨线程共享）。
_RAPID_POOL: list | None = None
_RAPID_POOL_LOCK = threading.Lock()
_ONNX_THREAD_SUPPORT: bool | None = None


def _supports_onnx_thread_limits() -> bool:
    """探测当前安装的 rapidocr 版本是否支持 intra/inter 线程数参数（结果缓存）。"""
    global _ONNX_THREAD_SUPPORT
    if _ONNX_THREAD_SUPPORT is not None:
        return _ONNX_THREAD_SUPPORT
    supported = False
    try:
        import inspect
        try:
            from rapidocr_onnxruntime.utils import OrtInferSession
        except ImportError:
            OrtInferSession = None
        if OrtInferSession is not None:
            supported = "intra_op_num_threads" in inspect.getsource(
                OrtInferSession.__init__
            )
    except Exception:  # noqa: BLE001
        supported = False
    _ONNX_THREAD_SUPPORT = supported
    if not supported:
        try:
            import logging
            logging.getLogger(__name__).info(
                "当前 rapidocr 版本不支持 intra/inter 线程数参数，"
                "OCR_ONNX_INTRA_THREADS/OCR_ONNX_INTER_THREADS 将被忽略"
            )
        except Exception:  # noqa: BLE001
            pass
    return supported


def _rapid_engine_kwargs() -> dict:
    """构造 RapidOCR 的关键字参数（线程数只在安装版本支持时下发）。"""
    from backend.app.core.config import settings
    kwargs: dict = {"use_angle_cls": settings.ocr_use_angle_cls}
    if _supports_onnx_thread_limits():
        if getattr(settings, "ocr_onnx_intra_threads", 0) > 0:
            kwargs["intra_op_num_threads"] = settings.ocr_onnx_intra_threads
        if getattr(settings, "ocr_onnx_inter_threads", 0) > 0:
            kwargs["inter_op_num_threads"] = settings.ocr_onnx_inter_threads
    return kwargs


def _create_rapid_engine():
    """创建一个全新的 RapidOCR 实例（worker 池用；不读全局缓存）。"""
    try:
        from rapidocr import RapidOCR
    except ImportError:
        from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(**_rapid_engine_kwargs())


def _get_rapid_engine():
    """缓存 RapidOCR 引擎实例（首次加载模型，之后复用；单 worker 串行路径用）。"""
    global _rapid_engine
    with _OCR_INIT_LOCK:
        return _initialize_rapid_engine()


def _initialize_rapid_engine():
    global _rapid_engine
    if _rapid_engine is None:
        _rapid_engine = _create_rapid_engine()
    else:
        # 命中热引擎：取消待执行的空闲释放，连续导入不会中途丢模型。
        touch_ocr_engine()
    return _rapid_engine


def get_rapid_pool(workers: int) -> list:
    """按 worker 数准备独立引擎实例；跨任务复用，空闲后由 release_ocr_engine 统一释放。"""
    global _RAPID_POOL
    workers = max(1, int(workers))
    touch_ocr_engine()
    with _RAPID_POOL_LOCK:
        pool = _RAPID_POOL
        if pool is None:
            pool = []
            _RAPID_POOL = pool
        while len(pool) < workers:
            pool.append(_create_rapid_engine())
        return pool[:workers]


def release_ocr_engine(*, force: bool = False) -> bool:
    """释放 OCR 模型（单例 + worker 池）；以少量下次冷启动换取更低的空闲内存。

    返回是否真正释放。以下情况拒绝释放（force=True 可强制）：
    - 仍有识别调用在进行（_OCR_ENGINE_ACTIVE > 0）；
    - 看门狗超时后仍有线程持有引擎锁。
    """
    global _rapid_engine, _RAPID_POOL
    # Python 无法强制终止正在运行的本地 OCR 线程。看门狗超时时保留同一引擎引用，
    # 避免随后又加载第二份模型；原调用返回后，下一次正常收尾会负责释放。
    if not force and (_OCR_ENGINE_ACTIVE > 0 or _OCR_ENGINE_LOCK.locked()):
        return False
    if _rapid_engine is None and _RAPID_POOL is None:
        return True
    _rapid_engine = None
    _RAPID_POOL = None
    try:
        import gc
        gc.collect()
    except Exception:  # noqa: BLE001
        pass
    return True


def _initial_pages(total: int, base_pages: list[str] | None) -> list[str]:
    pages = list(base_pages or [])
    if len(pages) < total:
        pages.extend([""] * (total - len(pages)))
    return pages[:total]


def _cached_text(cache_dir: Path | None, page_no: int,
                 metrics: OcrMetrics | None = None) -> tuple[Path | None, str | None]:
    if cache_dir is None:
        return None, None
    started_at = time.perf_counter()
    cache_file = cache_dir / f"page_{page_no:04d}.txt"
    cached_text = None
    if cache_file.exists():
        cached_text = cache_file.read_text(encoding="utf-8")
    if metrics is not None:
        metrics.observe("cache_read", time.perf_counter() - started_at)
    return cache_file, cached_text


def _write_page_cache(cache_file: Path | None, text: str,
                      metrics: OcrMetrics | None = None) -> None:
    """原子写页缓存：临时文件 + replace，避免中断留下半截文件污染缓存。"""
    if cache_file is None:
        return
    started_at = time.perf_counter()
    temporary = cache_file.with_name(f"{cache_file.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(cache_file)
    except OSError:
        temporary.unlink(missing_ok=True)
    finally:
        if metrics is not None:
            metrics.observe("cache_write", time.perf_counter() - started_at)


def _cache_rapid_layout(cache_dir: Path | None, page_no: int, result, width: int, height: int) -> None:
    """保留 RapidOCR 已产生的坐标，供阅读器构建透明文字层。"""
    if cache_dir is None or not result or width <= 0 or height <= 0:
        return
    import json
    items = []
    for row in result:
        try:
            points, text = row[0], str(row[1] or "").strip()
            if not points or not text:
                continue
            xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
            x0, x1 = max(0.0, min(xs)), min(float(width), max(xs))
            y0, y1 = max(0.0, min(ys)), min(float(height), max(ys))
            if x1 <= x0 or y1 <= y0:
                continue
            score = float(row[2]) if len(row) > 2 and row[2] is not None else None
            items.append({
                "text": text, "confidence": round(score, 4) if score is not None else None,
                "x": round(x0 / width, 6), "y": round(y0 / height, 6),
                "w": round((x1 - x0) / width, 6), "h": round((y1 - y0) / height, 6),
            })
        except (TypeError, ValueError, IndexError):
            continue
    payload = {"version": 1, "page": page_no, "source": "ocr", "status": "ready",
               "image_width": width, "image_height": height, "cached": False, "items": items}
    target = cache_dir / f"page_{page_no:04d}.layout.json"
    temporary = target.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(target)
    except OSError:
        temporary.unlink(missing_ok=True)


def _prepare_cached_targets(
    total: int,
    texts: list[str],
    cache_dir: Path | None,
    page_numbers: set[int] | None,
    on_progress=None,
    on_page_result=None,
    on_checkpoint=None,
    metrics: OcrMetrics | None = None,
) -> set[int]:
    """在渲染 PDF 前读取页缓存，避免命中缓存时仍生成整页位图。"""
    targets = set(range(1, total + 1)) if page_numbers is None else {
        page for page in page_numbers if 1 <= page <= total
    }
    missing: set[int] = set()
    for page_no in sorted(targets):
        _checkpoint(on_checkpoint, page_no, "cache")
        _cache_file, cached_text = _cached_text(cache_dir, page_no, metrics)
        if cached_text is None:
            missing.add(page_no)
            continue
        texts[page_no - 1] = cached_text
        if metrics is not None:
            metrics.bump("cached")
        if on_progress:
            on_progress(page_no, total, cached=True)
        if on_page_result:
            on_page_result(page_no, cached_text, [], True)
    return missing


def _ocr_rapid(p: Path, cache_dir: Path | None = None,
                on_progress=None, page_numbers: set[int] | None = None,
                base_pages: list[str] | None = None, on_page_result=None,
                on_checkpoint=None, page_timeout_seconds: int = 180,
                render_dpi: int = 144, on_metrics=None) -> list[str]:
    """用 RapidOCR 识别每页（中文效果好，CPU 可跑）。

    每页结果缓存到 cache_dir/page_NNNN.txt：中断后重跑命中缓存直接读取（断点续 OCR）。
    - OCR_WORKERS=1：单页串行路径（第一阶段实现，也是回退档）；
    - OCR_WORKERS>=2：有限并发流水线（第二阶段：独立引擎实例 + 有界队列）。
    全部命中缓存时不加载模型。
    """
    if _OCR_ENGINE_STUCK.is_set():
        raise OCRPageTimeout(
            "上一次 OCR 超时后识别引擎仍被占用，本次已中止（已识别页面的缓存会保留）。"
            "请稍候重试，或在 .env 中调整 OCR_PAGE_TIMEOUT_SECONDS。"
        )
    from backend.app.core.config import settings
    metrics = OcrMetrics().start() if getattr(settings, "ocr_metrics_enabled", True) else None
    skip_blank = bool(getattr(settings, "ocr_skip_blank_pages", True))
    blank_threshold = float(getattr(settings, "ocr_blank_page_threshold", 0.008))

    import fitz
    with fitz.open(p) as doc:
        total = doc.page_count
    texts = _initial_pages(total, base_pages)
    missing_pages = _prepare_cached_targets(
        total, texts, cache_dir, page_numbers, on_progress, on_page_result,
        on_checkpoint, metrics,
    )
    if missing_pages:
        # 只有真的需要识别时才加载模型：全缓存续跑不再为 ONNX 冷启动买单。
        with ocr_engine_session():
            workers = max(1, int(getattr(settings, "ocr_workers", 1) or 1))
            if workers <= 1:
                _ocr_rapid_serial(
                    p, cache_dir, missing_pages, texts,
                    on_progress=on_progress, on_page_result=on_page_result,
                    on_checkpoint=on_checkpoint,
                    page_timeout_seconds=page_timeout_seconds, render_dpi=render_dpi,
                    metrics=metrics, skip_blank=skip_blank,
                    blank_threshold=blank_threshold,
                )
            else:
                _run_rapid_pipeline(
                    p, cache_dir, missing_pages, texts, total,
                    on_progress=on_progress, on_page_result=on_page_result,
                    on_checkpoint=on_checkpoint,
                    page_timeout_seconds=page_timeout_seconds, render_dpi=render_dpi,
                    metrics=metrics,
                    workers=workers,
                    render_ahead=int(getattr(settings, "ocr_render_ahead", 3) or 3),
                    queue_size=int(getattr(settings, "ocr_max_image_queue", 4) or 4),
                    continue_on_error=bool(
                        getattr(settings, "ocr_continue_on_page_error", True)
                    ),
                    skip_blank=skip_blank, blank_threshold=blank_threshold,
                )
    _emit_metrics(on_metrics, metrics)
    return texts


def _ocr_rapid_serial(p: Path, cache_dir: Path | None, missing_pages: set[int],
                      texts: list[str], *, on_progress=None, on_page_result=None,
                      on_checkpoint=None, page_timeout_seconds: int = 180,
                      render_dpi: int = 144, metrics: OcrMetrics | None = None,
                      skip_blank: bool = True, blank_threshold: float = 0.008) -> None:
    """第一阶段串行路径：整份任务共用一个单 worker 执行器，逐页识别。"""
    import numpy as np
    import cv2

    touch_ocr_engine()  # 本次任务要复用/加载引擎，取消待执行的空闲释放
    engine = _get_rapid_engine()
    for i, total, img in _iter_pdf_page_images(p, missing_pages, dpi=render_dpi, metrics=metrics):
        try:
            _checkpoint(on_checkpoint, i, "recognizing")
            cache_file, _ignored = _cached_text(cache_dir, i, metrics)
            if skip_blank and is_blank_page(img, blank_threshold):
                # 空白页（扫描留白/分隔页）：写空缓存、正常推进进度，不跑完整 OCR。
                if metrics is not None:
                    metrics.bump("blank")
                texts[i - 1] = ""
                _write_page_cache(cache_file, "", metrics)
                if on_progress:
                    on_progress(i, total, cached=False)
                if on_page_result:
                    on_page_result(i, "", [], False)
                continue
            with _stage(metrics, "convert"):
                arr = np.array(img)
                bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

            def _recognize():
                with _OCR_ENGINE_LOCK:
                    return engine(bgr)

            with _stage(metrics, "ocr"):
                response = _run_with_timeout(
                    _recognize, page_timeout_seconds, i,
                    on_wait=lambda: _checkpoint(on_checkpoint, i, "recognizing"),
                    executor=get_ocr_executor(),
                )
            result = rapid_result_rows(response)
            if result:
                lines = [str(item[1]) for item in result]
                text = "\n".join(lines)
            else:
                text = ""
            texts[i - 1] = text
            _cache_rapid_layout(cache_dir, i, result or [], img.width, img.height)
            _write_page_cache(cache_file, text, metrics)
            if metrics is not None:
                metrics.bump("recognized")
            if on_progress:
                on_progress(i, total, cached=False)
            if on_page_result:
                on_page_result(i, text, result or [], False)
        finally:
            img.close()  # 图像所有权已随生成器 yield 转移给本循环


def _is_control_exception(exc: BaseException) -> bool:
    """取消/超时这类控制流异常：任何失败策略下都必须立即停任务并向上抛。"""
    return isinstance(exc, OCRPageTimeout) or exc.__class__.__name__ == "TaskCancelled"


def _run_rapid_pipeline(p: Path, cache_dir: Path | None, missing_pages: set[int],
                        texts: list[str], total_pages: int, *,
                        on_progress=None, on_page_result=None, on_checkpoint=None,
                        page_timeout_seconds: int = 180, render_dpi: int = 144,
                        metrics: OcrMetrics | None = None, workers: int = 2,
                        render_ahead: int = 3, queue_size: int = 4,
                        continue_on_error: bool = True, skip_blank: bool = True,
                        blank_threshold: float = 0.008) -> dict[int, str]:
    """第二阶段：单路生产者 + N 个独立引擎实例的消费者流水线。

    内存上界：OCR_RENDER_AHEAD 控制生产者超前页数，OCR_MAX_IMAGE_QUEUE 控制
    队列中未消费图像数量，两者都是有界的，不随总页数增长。
    超时语义：看门狗把连续 page_timeout_seconds 秒无结果的页标为失败（不写缓存），
    只废弃对应 worker 实例；所有 worker 都卡死时才抛出 OCRPageTimeout（快速失败）。
    归并：结果写入 texts[page_no-1]，按 1-based 页码对齐，与完成顺序无关。
    返回 {页码: 错误类别}。
    """
    import queue as queue_mod
    import numpy as np
    import cv2

    workers = max(1, int(workers))
    stop_event = threading.Event()
    producer_done = threading.Event()
    render_gate = threading.Semaphore(max(1, int(render_ahead)))
    image_queue: queue_mod.Queue = queue_mod.Queue(maxsize=max(1, int(queue_size)))
    errors: list[BaseException] = []
    errors_lock = threading.Lock()
    state_lock = threading.Lock()          # 保护 pending / in_flight / failed_pages
    pending: set[int] = set(missing_pages)
    in_flight: dict[int, tuple[float, int]] = {}
    failed_pages: dict[int, str] = {}
    worker_stuck: set[int] = set()
    worker_stuck_lock = threading.Lock()

    def _record_error(exc: BaseException) -> None:
        with errors_lock:
            if not errors:
                errors.append(exc)
        stop_event.set()

    def _report(page_no: int, page_total: int, text: str, result: list, cached: bool) -> None:
        """进度回调里的 TaskCancelled 也要转化为控制流错误，不能在 worker 线程里吞掉。"""
        try:
            if on_progress:
                on_progress(page_no, page_total, cached)
            if on_page_result:
                on_page_result(page_no, text, result, cached)
        except Exception as exc:  # noqa: BLE001
            _record_error(exc)

    def _settle(page_no: int) -> None:
        with state_lock:
            pending.discard(page_no)

    def _producer() -> None:
        try:
            for page_no, page_total, img in _iter_pdf_page_images(
                p, missing_pages, dpi=render_dpi, metrics=metrics
            ):
                if stop_event.is_set():
                    img.close()
                    return
                try:
                    _checkpoint(on_checkpoint, page_no, "recognizing")
                except Exception as exc:  # noqa: BLE001
                    img.close()
                    _record_error(exc)
                    return
                if skip_blank and is_blank_page(img, blank_threshold):
                    # 空白页在生产侧直接结算，不占用 worker 时间。
                    if metrics is not None:
                        metrics.bump("blank")
                    texts[page_no - 1] = ""
                    cache_file, _ignored = _cached_text(cache_dir, page_no, metrics)
                    _write_page_cache(cache_file, "", metrics)
                    _report(page_no, page_total, "", [], False)
                    _settle(page_no)
                    img.close()
                    continue
                # 渲染超前闸门：可中断等待，取消时不被信号量永久堵住
                acquired = False
                while not stop_event.is_set():
                    acquired = render_gate.acquire(timeout=0.5)
                    if acquired:
                        break
                if not acquired:
                    img.close()
                    return
                enqueued = False
                while not stop_event.is_set():
                    try:
                        image_queue.put((page_no, page_total, img), timeout=0.5)
                        enqueued = True
                        break
                    except queue_mod.Full:
                        continue
                if not enqueued:
                    img.close()
                    render_gate.release()
                    return
        except Exception as exc:  # noqa: BLE001 - 渲染失败/取消都要终止任务
            _record_error(exc)
        finally:
            producer_done.set()

    def _worker(worker_id: int, engine) -> None:
        while not stop_event.is_set():
            try:
                item = image_queue.get(timeout=0.5)
            except queue_mod.Empty:
                if producer_done.is_set():
                    return
                continue
            page_no, page_total, img = item
            with state_lock:
                in_flight[page_no] = (time.monotonic(), worker_id)
            try:
                with _stage(metrics, "convert"):
                    arr = np.array(img)
                    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                with _stage(metrics, "ocr"):
                    response = engine(bgr)
                result = rapid_result_rows(response)
                if result:
                    text = "\n".join(str(entry[1]) for entry in result)
                else:
                    text = ""
                with state_lock:
                    abandoned = page_no not in in_flight  # 已被看门狗按超时结算
                if not abandoned:
                    texts[page_no - 1] = text
                    _cache_rapid_layout(cache_dir, page_no, result or [],
                                        img.width, img.height)
                    cache_file, _ignored = _cached_text(cache_dir, page_no, metrics)
                    _write_page_cache(cache_file, text, metrics)
                    if metrics is not None:
                        metrics.bump("recognized")
                    _report(page_no, page_total, text, result or [], False)
                    _settle(page_no)
            except Exception as exc:  # noqa: BLE001
                with state_lock:
                    abandoned = page_no not in in_flight
                if abandoned:
                    pass  # 超时已被看门狗结算，迟到的结果直接丢弃，不写缓存
                elif _is_control_exception(exc):
                    _record_error(exc)
                elif continue_on_error:
                    with state_lock:
                        failed_pages[page_no] = type(exc).__name__
                    if metrics is not None:
                        metrics.bump("failed")
                    _report(page_no, page_total, "", [], False)
                    _settle(page_no)
                else:
                    _record_error(exc)
            finally:
                img.close()
                image_queue.task_done()
                render_gate.release()
                with state_lock:
                    in_flight.pop(page_no, None)
                # 卡死的 worker 被看门狗废弃后，线程一旦真正结束就解除 STUCK
                with worker_stuck_lock:
                    if worker_id in worker_stuck:
                        worker_stuck.discard(worker_id)
                        if not worker_stuck:
                            _OCR_ENGINE_STUCK.clear()

    engines = get_rapid_pool(workers)
    producer_thread = threading.Thread(target=_producer, name="ocr-producer", daemon=True)
    worker_threads = [
        threading.Thread(target=_worker, args=(wid, engines[wid]),
                         name=f"ocr-worker-{wid}", daemon=True)
        for wid in range(workers)
    ]
    producer_thread.start()
    for thread in worker_threads:
        thread.start()

    poll = max(0.2, min(1.0, page_timeout_seconds / 10.0))
    while True:
        with errors_lock:
            has_error = bool(errors)
        with state_lock:
            remaining = len(pending)
        if has_error or remaining == 0:
            break
        time.sleep(poll)
        now = time.monotonic()
        with state_lock:
            expired = [
                (page_no, worker_id)
                for page_no, (started, worker_id) in in_flight.items()
                if now - started >= page_timeout_seconds
            ]
        for page_no, worker_id in expired:
            with state_lock:
                if page_no not in pending:
                    continue
                in_flight.pop(page_no, None)
                pending.discard(page_no)
                failed_pages[page_no] = "OCRPageTimeout"
            with worker_stuck_lock:
                worker_stuck.add(worker_id)
                all_stuck = len(worker_stuck) >= workers
            # 与单页串行一致：超时引擎在线程真正结束前让后续任务快速失败
            _OCR_ENGINE_STUCK.set()
            if metrics is not None:
                metrics.bump("failed")
            _report(page_no, total_pages, "", [], False)
            if all_stuck or not continue_on_error:
                _record_error(OCRPageTimeout(
                    f"PDF 第 {page_no} 页连续 {page_timeout_seconds} 秒没有 OCR 结果；"
                    f"已完成页面缓存会保留"
                ))

    stop_event.set()
    producer_done.set()
    producer_thread.join(timeout=5.0)
    for thread in worker_threads:
        thread.join(timeout=2.0)
    # 清空队列中未消费的图像，避免 PIL 图像悬挂（卡死线程已 daemon 化，不阻塞退出）
    while True:
        try:
            _pn, _pt, _img = image_queue.get_nowait()
            _img.close()
        except queue_mod.Empty:
            break
    with errors_lock:
        if errors:
            raise errors[0]
    with state_lock:
        return dict(failed_pages)


@contextmanager
def _stage(metrics: OcrMetrics | None, phase: str):
    """指标开关关闭时不产生任何计时空转。"""
    if metrics is None:
        yield
        return
    with metrics.stage(phase):
        yield


def _emit_metrics(on_metrics, metrics: OcrMetrics | None) -> dict | None:
    """把结构化性能摘要交给回调；回调异常不能影响 OCR 结果。"""
    if metrics is None:
        return None
    metrics.stop()
    summary = metrics.summary()
    if on_metrics is None:
        return summary
    try:
        on_metrics(summary)
    except Exception:  # noqa: BLE001 - 统计上报失败不应让整份导入失败
        pass
    return summary


def rapid_result_rows(response) -> list:
    """兼容旧 tuple 与新版数组结果，避免把坐标数组误当成文字记录。"""
    if isinstance(response, tuple):
        return list(response[0]) if response[0] is not None else []
    boxes = getattr(response, "boxes", None)
    texts = getattr(response, "txts", None)
    scores = getattr(response, "scores", None)
    if boxes is None or texts is None:
        return []
    return [[box, text, float(scores[i]) if scores is not None else None]
            for i, (box, text) in enumerate(zip(boxes, texts))]


def _ocr_tesseract(p: Path, cache_dir: Path | None = None,
                   on_progress=None, page_numbers: set[int] | None = None,
                   base_pages: list[str] | None = None, on_page_result=None,
                   on_checkpoint=None, page_timeout_seconds: int = 180,
                   render_dpi: int = 144, on_metrics=None) -> list[str]:
    from backend.app.core.config import settings
    metrics = OcrMetrics().start() if getattr(settings, "ocr_metrics_enabled", True) else None
    import pytesseract
    from PIL import Image

    import fitz
    with fitz.open(p) as doc:
        total = doc.page_count
    texts = _initial_pages(total, base_pages)
    missing_pages = _prepare_cached_targets(
        total, texts, cache_dir, page_numbers, on_progress, on_page_result,
        on_checkpoint, metrics,
    )
    for i, total, img in _iter_pdf_page_images(p, missing_pages, dpi=render_dpi, metrics=metrics):
        try:
            _checkpoint(on_checkpoint, i, "recognizing")
            cache_file, cached_text = _cached_text(cache_dir, i, metrics)
            with _stage(metrics, "ocr"):
                txt = _run_with_timeout(lambda: pytesseract.image_to_string(img, lang="chi_sim+eng"),
                                        page_timeout_seconds, i, executor=get_ocr_executor())
            texts[i - 1] = txt
            _write_page_cache(cache_file, txt, metrics)
            if metrics is not None:
                metrics.bump("recognized")
            if on_progress:
                on_progress(i, total, cached=False)
            if on_page_result:
                on_page_result(i, txt, [], False)
        finally:
            img.close()
    _emit_metrics(on_metrics, metrics)
    return texts


def _ocr_paddle(p: Path, cache_dir: Path | None = None,
                  on_progress=None, page_numbers: set[int] | None = None,
                  base_pages: list[str] | None = None, on_page_result=None,
                  on_checkpoint=None, page_timeout_seconds: int = 180,
                  render_dpi: int = 144, on_metrics=None) -> list[str]:
    from backend.app.core.config import settings
    metrics = OcrMetrics().start() if getattr(settings, "ocr_metrics_enabled", True) else None
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    import fitz
    with fitz.open(p) as doc:
        total = doc.page_count
    texts = _initial_pages(total, base_pages)
    missing_pages = _prepare_cached_targets(
        total, texts, cache_dir, page_numbers, on_progress, on_page_result,
        on_checkpoint, metrics,
    )
    for i, total, img in _iter_pdf_page_images(p, missing_pages, dpi=render_dpi, metrics=metrics):
        try:
            _checkpoint(on_checkpoint, i, "recognizing")
            cache_file, cached_text = _cached_text(cache_dir, i, metrics)
            with _stage(metrics, "ocr"):
                import numpy as np
                result = _run_with_timeout(lambda: ocr.ocr(np.array(img), cls=True),
                                           page_timeout_seconds, i, executor=get_ocr_executor())
            lines = []
            if result and result[0]:
                for line in result[0]:
                    txt = line[1][0] if len(line) > 1 else ""
                    if txt:
                        lines.append(txt)
            text = "\n".join(lines)
            texts[i - 1] = text
            _write_page_cache(cache_file, text, metrics)
            if metrics is not None:
                metrics.bump("recognized")
            if on_progress:
                on_progress(i, total, cached=False)
            if on_page_result:
                on_page_result(i, text, result or [], False)
        finally:
            img.close()
    _emit_metrics(on_metrics, metrics)
    return texts
