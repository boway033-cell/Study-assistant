"""第二阶段 OCR 并发流水线专项测试（独立引擎实例池 + 有界队列 + 页级看门狗）。

原则与 test_ocr_phase1.py 相同：合成 PDF/假引擎，不加载 ONNX、不联网、
不碰真实用户 OCR 缓存。第一阶段串行回退路径的用例仍在 test_ocr_phase1.py。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from backend.app.services.parser import ocr

TOKEN_PREFIX = "PIPE"


# --------------------------------------------------------------------------- #
# fixtures / helpers（与 phase1 文件自洽，不互相 import，避免 pytest 导入模式耦合）
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolated_ocr_runtime():
    yield
    try:
        ocr.shutdown_ocr_runtime()
    finally:
        ocr._rapid_engine = None
        ocr._RAPID_POOL = None
        ocr._ONNX_THREAD_SUPPORT = None
        ocr._OCR_ENGINE_STUCK.clear()
        ocr._OCR_ENGINE_ACTIVE = 0
        ocr._OCR_IDLE_TIMER = None


@pytest.fixture()
def cache_root(tmp_path, monkeypatch):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


class FakeEngine:
    """假 RapidOCR：线程安全；支持按调用序号定制延迟/抛错；返回旧 tuple 格式。"""

    def __init__(self, text: str = TOKEN_PREFIX, delay: float = 0.02,
                 delays: dict[int, float] | None = None,
                 errors: dict[int, Exception] | None = None):
        self.text = text
        self.delay = delay
        self.delays = delays or {}
        self.errors = errors or {}
        self.calls: list[int] = []
        self._lock = threading.Lock()
        self._running = 0
        self.max_concurrency = 0

    def __call__(self, image):
        with self._lock:
            self._running += 1
            self.max_concurrency = max(self.max_concurrency, self._running)
            index = len(self.calls)
            self.calls.append(index)
        try:
            error = self.errors.get(index)
            if error is not None:
                raise error
            time.sleep(self.delays.get(index, self.delay))
            rows = [[[[0, 0], [1, 0], [1, 1], [0, 1]], f"{self.text}-{index}", 0.93]]
            return rows, []
        finally:
            with self._lock:
                self._running -= 1


def build_pdf(path: Path, inked_pages: set[int] = frozenset(), total: int = 3,
              marker_offsets: bool = False) -> Path:
    """合成 PDF。marker_offsets=True 时第 k 页黑条高度为 10+5k，供按图判页。"""
    import fitz

    doc = fitz.open()
    try:
        for number in range(total):
            page = doc.new_page(width=300, height=400)
            if number + 1 in inked_pages:
                bottom = 10 + 5 * (number + 1) if marker_offsets else 60
                page.draw_rect(
                    __import__("fitz").Rect(10, 10, 290, bottom),
                    color=(0, 0, 0), fill=(0, 0, 0),
                )
        doc.save(path)
    finally:
        doc.close()
    return path


def run_ocr(pdf: Path, cache_dir: Path, fake, *, factory=None, workers: int = 2,
            continue_on_error: bool = True, queue_size: int = 4,
            render_ahead: int = 3, **kwargs):
    """以假引擎跑一次 _ocr_rapid（默认 workers=2 走流水线路径）。"""
    import backend.app.services.parser.ocr as module
    from unittest import mock
    from backend.app.core.config import settings

    create = factory or (lambda: fake)
    with mock.patch.object(module, "_create_rapid_engine", create), \
         mock.patch.object(module, "_initialize_rapid_engine", create), \
         mock.patch.multiple(settings,
                             ocr_workers=workers,
                             ocr_continue_on_page_error=continue_on_error,
                             ocr_max_image_queue=queue_size,
                             ocr_render_ahead=render_ahead):
        return module._ocr_rapid(pdf, cache_dir=cache_dir, **kwargs)


def wait_until(predicate, timeout: float = 6.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def detect_page_marker(bgr) -> int:
    """从页图像反推 marker_offsets 合成页的页码（黑条底边行号 → k）。"""
    import numpy as np

    gray = bgr.mean(axis=2)
    dark_rows = np.where(gray.mean(axis=1) < 128)[0]
    if len(dark_rows) == 0:
        return 0
    return int((dark_rows.max() - 10) // 5) + 1


class MarkerEngine:
    """按输入图像判页的假引擎：可按页定制延迟/抛错，行为与 worker 分配无关。"""

    def __init__(self, delays: dict[int, float] | None = None,
                 errors: dict[int, Exception] | None = None, delay: float = 0.01):
        self.delays = delays or {}
        self.errors = errors or {}
        self.delay = delay
        self.pages: list[int] = []
        self._lock = threading.Lock()

    def __call__(self, image):
        page_no = detect_page_marker(image)
        with self._lock:
            self.pages.append(page_no)
        error = self.errors.get(page_no)
        if error is not None:
            raise error
        time.sleep(self.delays.get(page_no, self.delay))
        rows = [[[[0, 0], [1, 0], [1, 1], [0, 1]], f"page-{page_no}", 0.93]]
        return rows, []


# --------------------------------------------------------------------------- #
# 1. 配置与引擎构造
# --------------------------------------------------------------------------- #
def test_phase2_config_helpers_clamp_invalid_values(monkeypatch):
    from backend.app.core import config

    monkeypatch.setenv("OCR_WORKERS", "99")
    assert config._env_int("OCR_WORKERS", 2, 1, 4) == 4
    monkeypatch.setenv("OCR_WORKERS", "abc")
    assert config._env_int("OCR_WORKERS", 2, 1, 4) == 2
    monkeypatch.setenv("OCR_BLANK_PAGE_THRESHOLD", "2.0")
    assert config._env_float("OCR_BLANK_PAGE_THRESHOLD", 0.008, 0.0, 0.5) == 0.5
    monkeypatch.setenv("OCR_BLANK_PAGE_THRESHOLD", "nan")
    assert config._env_float("OCR_BLANK_PAGE_THRESHOLD", 0.008, 0.0, 0.5) == 0.008
    monkeypatch.setenv("OCR_CONTINUE_ON_PAGE_ERROR", "off")
    assert config._env_bool("OCR_CONTINUE_ON_PAGE_ERROR", True) is False
    monkeypatch.setenv("OCR_CONTINUE_ON_PAGE_ERROR", "1")
    assert config._env_bool("OCR_CONTINUE_ON_PAGE_ERROR", False) is True


def test_rapid_engine_kwargs_follow_support_detection(monkeypatch):
    from backend.app.core.config import settings

    monkeypatch.setattr(ocr, "_supports_onnx_thread_limits", lambda: False)
    assert ocr._rapid_engine_kwargs() == {"use_angle_cls": settings.ocr_use_angle_cls}

    monkeypatch.setattr(ocr, "_supports_onnx_thread_limits", lambda: True)
    monkeypatch.setattr(settings, "ocr_onnx_intra_threads", 6)
    monkeypatch.setattr(settings, "ocr_onnx_inter_threads", 1)
    kwargs = ocr._rapid_engine_kwargs()
    assert kwargs["intra_op_num_threads"] == 6
    assert kwargs["inter_op_num_threads"] == 1

    monkeypatch.setattr(settings, "ocr_onnx_intra_threads", 0)  # 0 = 用 onnx 默认，不下发
    assert "intra_op_num_threads" not in ocr._rapid_engine_kwargs()


def test_each_worker_gets_its_own_engine_instance(monkeypatch):
    made: list[FakeEngine] = []

    def factory():
        engine = FakeEngine()
        made.append(engine)
        return engine

    monkeypatch.setattr(ocr, "_create_rapid_engine", factory)
    pool = ocr.get_rapid_pool(2)
    assert len(made) == 2
    assert pool[0] is not pool[1]  # 推理状态非线程安全，绝不共享实例
    ocr.get_rapid_pool(2)
    assert len(made) == 2  # 再次获取不重建（跨任务复用）


# --------------------------------------------------------------------------- #
# 2. 流水线正确性
# --------------------------------------------------------------------------- #
def test_pipeline_merges_out_of_order_results_by_page_number(cache_root):
    pdf = build_pdf(cache_root / "merge.pdf", inked_pages={1, 2, 3, 4},
                    total=4, marker_offsets=True)
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    marker = MarkerEngine(delays={1: 0.3})  # 第 1 页慢，制造乱序完成

    texts = run_ocr(pdf, cache_dir, marker, workers=2,
                    page_numbers={1, 2, 3, 4}, base_pages=["", "", "", ""],
                    render_dpi=72)

    # 第 1 页最后完成，但归并必须按 1-based 页码，而不是完成顺序
    assert texts == ["page-1", "page-2", "page-3", "page-4"]


def test_pipeline_workers_run_concurrently(cache_root):
    pdf = build_pdf(cache_root / "concurrency.pdf", inked_pages={1, 2, 3, 4}, total=4)
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    fake = FakeEngine(delay=0.15)

    run_ocr(pdf, cache_dir, fake, workers=2,
            page_numbers={1, 2, 3, 4}, base_pages=["", "", "", ""], render_dpi=72)

    assert fake.max_concurrency == 2  # 两个 worker 确实同时在识别


def test_pipeline_image_queue_is_bounded(cache_root, monkeypatch):
    import queue as queue_mod

    created: list[int] = []
    real_queue = queue_mod.Queue

    class SpyQueue(real_queue):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            created.append(kwargs.get("maxsize", args[0] if args else 0))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(queue_mod, "Queue", SpyQueue)
    pdf = build_pdf(cache_root / "bounded.pdf", inked_pages={1, 2, 3, 4}, total=4)
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))

    run_ocr(pdf, cache_dir, FakeEngine(delay=0.1), workers=2, queue_size=2,
            page_numbers={1, 2, 3, 4}, base_pages=["", "", "", ""], render_dpi=72)

    assert created == [2]  # 图像队列容量受 OCR_MAX_IMAGE_QUEUE 控制


def test_pipeline_blank_pages_settled_by_producer(cache_root):
    pdf = build_pdf(cache_root / "blank.pdf", inked_pages={2})
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    fake = FakeEngine()
    progress: list[int] = []
    summary: dict = {}

    texts = run_ocr(pdf, cache_dir, fake, workers=2,
                    page_numbers={1, 2, 3}, base_pages=["", "", ""],
                    on_progress=lambda page, total, cached: progress.append(page),
                    on_metrics=summary.update, render_dpi=72)

    assert len(fake.calls) == 1
    assert texts[0] == "" and texts[2] == ""
    assert sorted(progress) == [1, 2, 3]
    assert summary["pages"]["blank"] == 2
    assert summary["pages"]["recognized"] == 1
    assert (cache_dir / "page_0001.txt").read_text(encoding="utf-8") == ""


# --------------------------------------------------------------------------- #
# 3. 超时与失败策略
# --------------------------------------------------------------------------- #
def test_timeout_page_fails_but_others_complete(cache_root, monkeypatch):
    stuck_marks: list[float] = []

    class SpyEvent(threading.Event):
        def set(self):
            stuck_marks.append(time.monotonic())
            super().set()

    monkeypatch.setattr(ocr, "_OCR_ENGINE_STUCK", SpyEvent())
    pdf = build_pdf(cache_root / "timeout.pdf", inked_pages={1, 2, 3},
                    marker_offsets=True)
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    fake = MarkerEngine(delays={1: 2.5})  # 第 1 页卡 2.5 秒，与分到哪个 worker 无关

    texts = run_ocr(pdf, cache_dir, fake, workers=2, continue_on_error=True,
                    page_numbers={1, 2, 3}, base_pages=["", "", ""],
                    page_timeout_seconds=1, render_dpi=72)

    assert texts == ["", "page-2", "page-3"]
    assert not (cache_dir / "page_0001.txt").exists()  # 超时页不写缓存
    assert (cache_dir / "page_0002.txt").exists()
    assert (cache_dir / "page_0003.txt").exists()
    assert stuck_marks  # 看门狗确实把卡死引擎标成"占用中"（后续任务快速失败）
    # 卡死的 worker 线程真正结束后必须解除标记
    assert wait_until(lambda: not ocr._OCR_ENGINE_STUCK.is_set())


def test_all_workers_stuck_raises_timeout_fast(cache_root):
    pdf = build_pdf(cache_root / "stuck-all.pdf", inked_pages={1, 2, 3})
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    fake = FakeEngine(delay=2.5)  # 两个 worker 都卡死

    with pytest.raises(ocr.OCRPageTimeout):
        run_ocr(pdf, cache_dir, fake, workers=2, continue_on_error=True,
                page_numbers={1, 2, 3}, base_pages=["", "", ""],
                page_timeout_seconds=1, render_dpi=72)

    assert wait_until(lambda: not ocr._OCR_ENGINE_STUCK.is_set())


def test_page_error_continues_when_enabled(cache_root):
    pdf = build_pdf(cache_root / "continue.pdf", inked_pages={1, 2, 3, 4}, total=4,
                    marker_offsets=True)
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    fake = MarkerEngine(errors={2: ValueError("page exploded")})
    summary: dict = {}

    texts = run_ocr(pdf, cache_dir, fake, workers=2, continue_on_error=True,
                    page_numbers={1, 2, 3, 4}, base_pages=["", "", "", ""],
                    on_metrics=summary.update, render_dpi=72)

    assert texts == ["page-1", "", "page-3", "page-4"]
    assert not (cache_dir / "page_0002.txt").exists()  # 失败页不写缓存，重跑可补
    assert (cache_dir / "page_0001.txt").exists()
    assert summary["pages"]["failed"] == 1
    assert summary["pages"]["recognized"] == 3


def test_page_error_aborts_when_disabled(cache_root):
    pdf = build_pdf(cache_root / "abort.pdf", inked_pages={1, 2, 3, 4}, total=4,
                    marker_offsets=True)
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    # 第 1 页慢一点，保证第 2 页先抛错：出错后停止领页的行为才可确定性断言
    fake = MarkerEngine(delays={1: 0.15}, errors={2: ValueError("page exploded")})

    with pytest.raises(ValueError):
        run_ocr(pdf, cache_dir, fake, workers=2, continue_on_error=False,
                page_numbers={1, 2, 3, 4}, base_pages=["", "", "", ""],
                render_dpi=72)

    assert not (cache_dir / "page_0002.txt").exists()
    assert not (cache_dir / "page_0003.txt").exists()  # 出错后不再领新页
    assert not (cache_dir / "page_0004.txt").exists()


def test_cancellation_stops_pipeline_and_keeps_completed_cache(cache_root):
    pdf = build_pdf(cache_root / "cancel.pdf", inked_pages={1, 2, 3, 4}, total=4)
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    fake = FakeEngine(delay=0.02)

    def _checkpoint(page_no: int, phase: str) -> None:
        if page_no == 2 and phase == "recognizing":
            raise RuntimeError("任务已由用户取消")

    with pytest.raises(RuntimeError, match="取消"):
        run_ocr(pdf, cache_dir, fake, workers=2,
                page_numbers={1, 2, 3, 4}, base_pages=["", "", "", ""],
                on_checkpoint=_checkpoint, render_dpi=72)

    assert (cache_dir / "page_0001.txt").exists()  # 已完成页面缓存保留
    assert not (cache_dir / "page_0003.txt").exists()  # 取消后不再领新页
    assert not list(cache_dir.glob("*.tmp"))


# --------------------------------------------------------------------------- #
# 4. 引擎池生命周期
# --------------------------------------------------------------------------- #
def test_engine_pool_reused_across_consecutive_imports(cache_root, monkeypatch):
    first_pdf = build_pdf(cache_root / "book-a.pdf", inked_pages={1, 2})
    second_pdf = build_pdf(cache_root / "book-b.pdf", inked_pages={1, 3})
    creations: list[FakeEngine] = []

    def factory():
        engine = FakeEngine()
        creations.append(engine)
        return engine

    for pdf in (first_pdf, second_pdf):
        run_ocr(pdf, ocr._ocr_cache_dir(ocr._file_hash(pdf)), None,
                factory=factory, workers=2,
                page_numbers={1, 2, 3}, base_pages=["", "", ""], render_dpi=72)
        ocr.schedule_ocr_engine_release(idle_seconds=300)

    assert len(creations) == 2  # 两个实例都在第一份文档创建，第二份零冷启动
    assert ocr._RAPID_POOL is not None and len(ocr._RAPID_POOL) == 2


def test_fully_cached_document_loads_no_engine(cache_root, monkeypatch):
    """第一阶段遗留缺陷修复：全部命中缓存时不再加载模型。"""
    pdf = build_pdf(cache_root / "cached.pdf", inked_pages={2})
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    run_ocr(pdf, cache_dir, FakeEngine(), workers=2,
            page_numbers={1, 2, 3}, base_pages=["", "", ""], render_dpi=72)

    creations: list[object] = []

    def factory():
        creations.append(object())
        return FakeEngine()

    texts = run_ocr(pdf, cache_dir, FakeEngine(), factory=factory, workers=2,
                    page_numbers={1, 2, 3}, base_pages=["", "", ""], render_dpi=72)

    assert creations == []  # 没有任何引擎创建/加载
    assert texts[1] != ""


def test_workers_one_uses_serial_fallback_not_pipeline(cache_root, monkeypatch):
    called: list[bool] = []

    real_pipeline = ocr._run_rapid_pipeline

    def _spy(*args, **kwargs):
        called.append(True)
        return real_pipeline(*args, **kwargs)

    monkeypatch.setattr(ocr, "_run_rapid_pipeline", _spy)
    pdf = build_pdf(cache_root / "fallback.pdf", inked_pages={1, 2, 3})
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))

    texts = run_ocr(pdf, cache_dir, FakeEngine(), workers=1,
                    page_numbers={1, 2, 3}, base_pages=["", "", ""], render_dpi=72)

    assert called == []  # OCR_WORKERS=1 走串行路径，不碰流水线
    assert all(texts)


# --------------------------------------------------------------------------- #
# 5. 审查修正：超时 worker 退役 / STUCK 精确语义 / 生命周期竞态 / 进度单调
# --------------------------------------------------------------------------- #
def test_retired_worker_drops_late_result_and_stops_claiming(cache_root):
    """超时 worker 必须真正退役：迟到结果不写文本/缓存，也不补建/重试该页。

    行为按页号（marker）而非 worker 实例判定，故与线程调度无关、确定性可复现。
    """
    release_page1 = threading.Event()
    pages_seen: list[int] = []

    class GatedMarkerEngine:
        def __init__(self):
            self._lock = threading.Lock()

        def __call__(self, image):
            page_no = detect_page_marker(image)
            with self._lock:
                pages_seen.append(page_no)
            if page_no == 1:
                release_page1.wait(timeout=30.0)  # 第 1 页卡死，直到测试放行
            rows = [[[[0, 0], [1, 0], [1, 1], [0, 1]], f"page-{page_no}", 0.93]]
            return rows, []

    pdf = build_pdf(cache_root / "retire.pdf", inked_pages={1, 2, 3, 4}, total=4,
                    marker_offsets=True)
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))

    texts = run_ocr(pdf, cache_dir, GatedMarkerEngine(), workers=2,
                    continue_on_error=True, page_numbers={1, 2, 3, 4},
                    base_pages=["", "", "", ""], page_timeout_seconds=1, render_dpi=72)

    # 超时页不写文本、不写缓存；其余页由健康 worker 完成
    assert texts == ["", "page-2", "page-3", "page-4"]
    assert not (cache_dir / "page_0001.txt").exists()
    assert (cache_dir / "page_0002.txt").exists()
    # 卡死 worker 的迟到调用尚未返回 → STUCK 仍置位（健康 worker 完成不解除它）
    assert ocr._OCR_ENGINE_STUCK.is_set()
    # 退役 worker 不补建、不重试：第 1 页只被识别一次
    assert pages_seen.count(1) == 1

    release_page1.set()  # 放行迟到调用
    assert wait_until(lambda: not ocr._OCR_ENGINE_STUCK.is_set())
    # 迟到结果仍不得写回（退役 worker 的提交被丢弃）
    assert texts[0] == ""
    assert not (cache_dir / "page_0001.txt").exists()


def test_stuck_flag_clears_only_after_all_stuck_workers_return(cache_root):
    """一个卡死 worker 恢复不能误清其他仍卡住的 worker 的 STUCK 标记。"""
    gates = [threading.Event(), threading.Event()]
    made = []

    class GatedEngine:
        def __init__(self, idx):
            self.idx = idx
            self.done = threading.Event()  # 该实例的识别调用真正返回

        def __call__(self, image):
            gates[self.idx].wait(timeout=30.0)  # 两个实例各自卡死
            rows = [[[[0, 0], [1, 0], [1, 1], [0, 1]], f"e{self.idx}", 0.93]]
            self.done.set()
            return rows, []

    def factory():
        engine = GatedEngine(len(made))
        made.append(engine)
        return engine

    pdf = build_pdf(cache_root / "stuck-all-gated.pdf", inked_pages={1, 2, 3}, total=3)
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))

    with pytest.raises(ocr.OCRPageTimeout):
        run_ocr(pdf, cache_dir, None, factory=factory, workers=2,
                continue_on_error=True, page_numbers={1, 2, 3},
                base_pages=["", "", ""], page_timeout_seconds=1, render_dpi=72)

    assert len(made) == 2
    assert ocr._OCR_ENGINE_STUCK.is_set()

    gates[0].set()                        # 仅一个卡死 worker 恢复
    assert made[0].done.wait(timeout=5)    # 确定性等待其迟到调用返回（无 sleep）
    assert ocr._OCR_ENGINE_STUCK.is_set()  # 另一个仍卡死，标记不得清除

    gates[1].set()
    assert wait_until(lambda: not ocr._OCR_ENGINE_STUCK.is_set())


def test_release_ocr_engine_refuses_during_active_session(cache_root, monkeypatch):
    """引擎生命周期竞态：会话内（ACTIVE>0）非 force 释放必须拒绝并保留实例。"""
    made = []

    def factory():
        engine = FakeEngine()
        made.append(engine)
        return engine

    monkeypatch.setattr(ocr, "_create_rapid_engine", factory)

    with ocr.ocr_engine_session():
        engine = ocr._get_rapid_engine()
        assert ocr._rapid_engine is engine
        # 会话内释放（非 force）→ 拒绝，实例仍在
        assert ocr.release_ocr_engine() is False
        assert ocr._rapid_engine is engine

    # 会话结束后可释放
    assert ocr.release_ocr_engine() is True
    assert ocr._rapid_engine is None
    assert len(made) == 1  # 全程只冷加载一次，没有因竞态重复加载


def test_ocr_progress_reporter_is_monotonic_under_concurrency(monkeypatch):
    """多 worker 乱序回调下，OCR 进度写入必须单调不回退（审查修正 #2）。"""
    from backend.app.worker import import_task
    from backend.app.worker.tasks import TaskRecord

    record = TaskRecord(id="progress-test")
    record.progress = 0.15
    written: list[float] = []

    def fake_update(r, progress, stage="", message="", *, force=False):
        written.append(progress)
        r.progress = progress

    monkeypatch.setattr(import_task, "update_progress", fake_update)
    reporter = import_task._OcrProgressReporter(record, weak_total=40)

    def worker(offset):
        for i in range(20):
            reporter.progress(offset + i, 40, cached=(i % 3 == 0))

    threads = [threading.Thread(target=worker, args=(o,)) for o in (0, 20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert written == sorted(written)  # 写入值单调非降，无 UI 倒退
    assert record.progress == written[-1]
    assert 0.15 <= record.progress <= 0.30


# --------------------------------------------------------------------------- #
# 6. 审查修正 #1：超时结算与退役的原子性（TOCTOU）+ 生命周期确定性测试
# --------------------------------------------------------------------------- #
def test_finish_worker_call_never_observes_settled_without_retired():
    """结算与退役在单把锁下原子完成：并发 finish 看不到"已结算但未退役"的撕裂态。

    用 Barrier 让 finish 在 settle 持锁期间就绪并阻塞在同一把锁上；二者共用
    state_lock 时，finish 只能在 settle 的整个临界区（删页+结算+退役+STUCK）完成后
    执行，观察到的是"已结算且已退役"的一致状态。旧实现把结算与退役拆成两把锁，
    本断言会捕获那种中间态。
    """
    lock = threading.Lock()
    pending = {1, 2}
    in_flight = {1: (0.0, 0)}
    failed_pages: dict[int, str] = {}
    retired: set[int] = set()
    stuck: set[int] = set()
    ocr._OCR_ENGINE_STUCK.clear()

    observed: dict = {}
    barrier = threading.Barrier(2)

    def finish():
        barrier.wait()  # 与 settle 临界区并发开始
        with lock:
            observed["settled"] = (1 not in in_flight) and (1 not in pending)
            observed["retired"] = 0 in retired
            observed["should_retire"] = ocr._finish_worker_call_locked(
                1, 0, in_flight=in_flight, retired_workers=retired, worker_stuck=stuck)

    with lock:
        t = threading.Thread(target=finish)
        t.start()
        barrier.wait()  # finish 已就绪，正阻塞在 lock 上
        settled, no_usable = ocr._settle_page_timeout_locked(
            1, 0, pending=pending, in_flight=in_flight, failed_pages=failed_pages,
            retired_workers=retired, worker_stuck=stuck, workers=2)
        assert settled is True
        assert no_usable is False
        assert 0 in retired and 0 in stuck
        assert ocr._OCR_ENGINE_STUCK.is_set()
    t.join()

    # 一致状态：已结算 ⇒ 已退役，绝无"已结算但未退役"
    assert observed["settled"] is True
    assert observed["retired"] is True
    assert observed["should_retire"] is True
    assert not stuck                      # 唯一卡死 worker 的调用已返回
    assert not ocr._OCR_ENGINE_STUCK.is_set()  # STUCK 不永久残留


def test_settle_page_timeout_rechecks_stale_snapshot():
    """过期超时快照不得误结算/误退役：页已自行提交时返回 settled=False。"""
    pending = {1, 2}
    in_flight: dict = {}  # 页已被 worker 自行提交（in_flight 无此页）
    failed_pages: dict = {}
    retired: set = set()
    stuck: set = set()
    ocr._OCR_ENGINE_STUCK.clear()

    settled, no_usable = ocr._settle_page_timeout_locked(
        1, 0, pending=pending, in_flight=in_flight, failed_pages=failed_pages,
        retired_workers=retired, worker_stuck=stuck, workers=2)
    assert settled is False
    assert no_usable is False
    assert 0 not in retired
    assert 1 in pending
    assert not ocr._OCR_ENGINE_STUCK.is_set()


def test_idle_timer_does_not_release_engine_during_active_session(monkeypatch):
    """活动 session 期间空闲定时器到期（_on_idle_timeout）不能释放引擎。"""
    made = []

    def factory():
        engine = FakeEngine()
        made.append(engine)
        return engine

    monkeypatch.setattr(ocr, "_create_rapid_engine", factory)

    with ocr.ocr_engine_session():
        engine = ocr._get_rapid_engine()
        assert ocr._rapid_engine is engine
        # 直接触发空闲定时器到点：会话内必须拒绝释放，实例保留
        ocr._on_idle_timeout()
        assert ocr._rapid_engine is engine
        assert ocr._OCR_ENGINE_ACTIVE >= 1
        # 重试一次仍不得释放
        ocr._on_idle_timeout()
        assert ocr._rapid_engine is engine

    # 会话结束后可释放，全程只冷加载一次
    assert ocr.release_ocr_engine() is True
    assert ocr._rapid_engine is None
    assert len(made) == 1


def test_get_rapid_pool_concurrent_release_no_loss_or_duplicate(monkeypatch):
    """会话内并发 release 与 get_rapid_pool：不丢失、不重复加载实例。"""
    made = []
    make_lock = threading.Lock()

    def factory():
        with make_lock:
            engine = FakeEngine()
            made.append(engine)
            return engine

    monkeypatch.setattr(ocr, "_create_rapid_engine", factory)

    with ocr.ocr_engine_session():
        pool = ocr.get_rapid_pool(2)
        assert len(pool) == 2
        outcomes: list[tuple[str, bool]] = []

        def worker():
            outcomes.append(("release", ocr.release_ocr_engine()))
            outcomes.append(("pool", len(ocr.get_rapid_pool(2)) == 2))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 会话内所有 release 都被拒绝；池始终有 2 个实例
        assert all(ok is False for kind, ok in outcomes if kind == "release")
        assert all(ok for kind, ok in outcomes if kind == "pool")
    assert len(made) == 2  # 全程只加载 2 个实例，无重复


def test_shutdown_ocr_runtime_is_idempotent(monkeypatch):
    """shutdown_ocr_runtime / release 多次调用保持幂等，不抛异常。"""
    monkeypatch.setattr(ocr, "_create_rapid_engine", lambda: FakeEngine())
    ocr.get_rapid_pool(2)  # 预加载一个池

    for _ in range(3):
        ocr.shutdown_ocr_runtime()  # 重复调用幂等

    assert ocr._RAPID_POOL is None
    assert ocr._rapid_engine is None
    assert ocr.release_ocr_engine() is True          # 已空，返回允许
    assert ocr.release_ocr_engine(force=True) is True  # force 幂等
