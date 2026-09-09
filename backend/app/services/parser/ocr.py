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
import threading
import time


class OCRPageTimeout(RuntimeError):
    """A page produced no result within the configured watchdog interval."""


def _raise_control_exception(exc: Exception) -> None:
    # Avoid importing worker.tasks here (it imports the parser through import_task).
    if isinstance(exc, OCRPageTimeout) or exc.__class__.__name__ == "TaskCancelled":
        raise exc


def _checkpoint(on_checkpoint, page_no: int, phase: str) -> None:
    if on_checkpoint:
        on_checkpoint(page_no, phase)


# 看门狗超时后，被放弃的识别线程仍持有 _OCR_ENGINE_LOCK（Python 无法强制终止本地线程）。
# 在此期间新任务会在锁上白等到再次超时；用此标记改为快速失败并给出明确原因。
_OCR_ENGINE_STUCK = threading.Event()


def _on_ocr_future_done(future) -> None:
    """识别线程真正结束后清除"引擎占用中"标记（无论成功、失败或被放弃）。"""
    _OCR_ENGINE_STUCK.clear()


def _run_with_timeout(callable_, timeout_seconds: int, page_no: int, on_wait=None):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ocr-page-{page_no}")
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
                    on_wait()
    finally:
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
            render_dpi: int | None = None) -> list[str]:
    """对扫描版 PDF 做 OCR，返回每页文本。

    on_progress(page_no, total, cached)：每页完成后回调（cached=True 表示命中缓存）。
    支持断点续跑：每页结果缓存到 data/ocr_cache/{file_hash}/，中断后重跑自动跳过已识别页。

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
                          page_timeout_seconds=page_timeout_seconds, render_dpi=render_dpi)
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
                              page_timeout_seconds=page_timeout_seconds, render_dpi=render_dpi)
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
                           page_timeout_seconds=page_timeout_seconds, render_dpi=render_dpi)
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
                          dpi: int = 180) -> Iterator[tuple[int, int, object]]:
    """逐页渲染并立即释放上一页，禁止把整本扫描书的位图同时放进内存。"""
    import fitz  # PyMuPDF
    from PIL import Image

    doc = fitz.open(p)
    total = doc.page_count
    try:
        targets = sorted(page_numbers) if page_numbers is not None else range(1, total + 1)
        for page_no in targets:
            if not 1 <= page_no <= total:
                continue
            page = doc.load_page(page_no - 1)
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            del pix
            yield page_no, total, image
            image.close()
            del image
    finally:
        doc.close()


_rapid_engine = None
_OCR_ENGINE_LOCK = threading.Lock()
_OCR_INIT_LOCK = threading.Lock()


def _get_rapid_engine():
    """缓存 RapidOCR 引擎实例（首次加载模型，之后复用）。"""
    global _rapid_engine
    with _OCR_INIT_LOCK:
        return _initialize_rapid_engine()


def _initialize_rapid_engine():
    global _rapid_engine
    if _rapid_engine is None:
        from backend.app.core.config import settings
        try:
            from rapidocr import RapidOCR
        except ImportError:
            from rapidocr_onnxruntime import RapidOCR
        _rapid_engine = RapidOCR(use_angle_cls=settings.ocr_use_angle_cls)
    return _rapid_engine


def release_ocr_engine() -> None:
    """任务结束后释放 OCR 模型；以少量下次冷启动换取更低的空闲内存。"""
    global _rapid_engine
    # Python 无法强制终止正在运行的本地 OCR 线程。看门狗超时时保留同一引擎引用，
    # 避免随后又加载第二份模型；原调用返回后，下一次正常收尾会负责释放。
    if _OCR_ENGINE_LOCK.locked():
        return
    _rapid_engine = None
    try:
        import gc
        gc.collect()
    except Exception:  # noqa: BLE001
        pass


def _initial_pages(total: int, base_pages: list[str] | None) -> list[str]:
    pages = list(base_pages or [])
    if len(pages) < total:
        pages.extend([""] * (total - len(pages)))
    return pages[:total]


def _cached_text(cache_dir: Path | None, page_no: int) -> tuple[Path | None, str | None]:
    if cache_dir is None:
        return None, None
    cache_file = cache_dir / f"page_{page_no:04d}.txt"
    if cache_file.exists():
        return cache_file, cache_file.read_text(encoding="utf-8")
    return cache_file, None


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
) -> set[int]:
    """在渲染 PDF 前读取页缓存，避免命中缓存时仍生成整页位图。"""
    targets = set(range(1, total + 1)) if page_numbers is None else {
        page for page in page_numbers if 1 <= page <= total
    }
    missing: set[int] = set()
    for page_no in sorted(targets):
        _checkpoint(on_checkpoint, page_no, "cache")
        _cache_file, cached_text = _cached_text(cache_dir, page_no)
        if cached_text is None:
            missing.add(page_no)
            continue
        texts[page_no - 1] = cached_text
        if on_progress:
            on_progress(page_no, total, cached=True)
        if on_page_result:
            on_page_result(page_no, cached_text, [], True)
    return missing


def _ocr_rapid(p: Path, cache_dir: Path | None = None,
                on_progress=None, page_numbers: set[int] | None = None,
                base_pages: list[str] | None = None, on_page_result=None,
                on_checkpoint=None, page_timeout_seconds: int = 180,
                render_dpi: int = 144) -> list[str]:
    """用 RapidOCR 识别每页（中文效果好，CPU 可跑）。

    每页结果缓存到 cache_dir/page_NNNN.txt：中断后重跑命中缓存直接读取（断点续 OCR）。
    """
    if _OCR_ENGINE_STUCK.is_set():
        raise OCRPageTimeout(
            "上一次 OCR 超时后识别引擎仍被占用，本次已中止（已识别页面的缓存会保留）。"
            "请稍候重试，或在 .env 中调整 OCR_PAGE_TIMEOUT_SECONDS。"
        )
    import numpy as np
    import cv2

    engine = _get_rapid_engine()
    import fitz
    with fitz.open(p) as doc:
        total = doc.page_count
    texts = _initial_pages(total, base_pages)
    missing_pages = _prepare_cached_targets(
        total, texts, cache_dir, page_numbers, on_progress, on_page_result, on_checkpoint
    )
    for i, total, img in _iter_pdf_page_images(p, missing_pages, dpi=render_dpi):
        _checkpoint(on_checkpoint, i, "recognizing")
        cache_file, cached_text = _cached_text(cache_dir, i)
        # 2. 真正 OCR
        arr = np.array(img)
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        def _recognize():
            with _OCR_ENGINE_LOCK:
                return engine(bgr)

        response = _run_with_timeout(
            _recognize, page_timeout_seconds, i,
            on_wait=lambda: _checkpoint(on_checkpoint, i, "recognizing"),
        )
        result = rapid_result_rows(response)
        if result:
            lines = [str(item[1]) for item in result]
            text = "\n".join(lines)
        else:
            text = ""
        texts[i - 1] = text
        _cache_rapid_layout(cache_dir, i, result or [], img.width, img.height)
        # 3. 写缓存
        if cache_file is not None:
            try:
                cache_file.write_text(text, encoding="utf-8")
            except OSError:
                pass
        if on_progress:
            on_progress(i, total, cached=False)
        if on_page_result:
            on_page_result(i, text, result or [], False)
    return texts


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
                   render_dpi: int = 144) -> list[str]:
    import pytesseract
    from PIL import Image

    import fitz
    with fitz.open(p) as doc:
        total = doc.page_count
    texts = _initial_pages(total, base_pages)
    missing_pages = _prepare_cached_targets(
        total, texts, cache_dir, page_numbers, on_progress, on_page_result, on_checkpoint
    )
    for i, total, img in _iter_pdf_page_images(p, missing_pages, dpi=render_dpi):
        _checkpoint(on_checkpoint, i, "recognizing")
        cache_file, cached_text = _cached_text(cache_dir, i)
        txt = _run_with_timeout(lambda: pytesseract.image_to_string(img, lang="chi_sim+eng"),
                                page_timeout_seconds, i)
        texts[i - 1] = txt
        if cache_file is not None:
            try:
                cache_file.write_text(txt, encoding="utf-8")
            except OSError:
                pass
        if on_progress:
            on_progress(i, total, cached=False)
        if on_page_result:
            on_page_result(i, txt, [], False)
    return texts


def _ocr_paddle(p: Path, cache_dir: Path | None = None,
                  on_progress=None, page_numbers: set[int] | None = None,
                  base_pages: list[str] | None = None, on_page_result=None,
                  on_checkpoint=None, page_timeout_seconds: int = 180,
                  render_dpi: int = 144) -> list[str]:
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    import fitz
    with fitz.open(p) as doc:
        total = doc.page_count
    texts = _initial_pages(total, base_pages)
    missing_pages = _prepare_cached_targets(
        total, texts, cache_dir, page_numbers, on_progress, on_page_result, on_checkpoint
    )
    for i, total, img in _iter_pdf_page_images(p, missing_pages, dpi=render_dpi):
        _checkpoint(on_checkpoint, i, "recognizing")
        cache_file, cached_text = _cached_text(cache_dir, i)
        import numpy as np
        result = _run_with_timeout(lambda: ocr.ocr(np.array(img), cls=True), page_timeout_seconds, i)
        lines = []
        if result and result[0]:
            for line in result[0]:
                txt = line[1][0] if len(line) > 1 else ""
                if txt:
                    lines.append(txt)
        text = "\n".join(lines)
        texts[i - 1] = text
        if cache_file is not None:
            try:
                cache_file.write_text(text, encoding="utf-8")
            except OSError:
                pass
        if on_progress:
            on_progress(i, total, cached=False)
        if on_page_result:
            on_page_result(i, text, result or [], False)
    return texts
