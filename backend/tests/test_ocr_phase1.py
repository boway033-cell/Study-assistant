"""第一阶段 OCR 性能优化专项测试（低风险优化：共享执行器 / 延迟释放 / 空白页 / 指标）。

测试原则：
- 只用 fitz + PIL 现场合成的小型 PDF/图像，不碰真实私人 PDF；
- 用假引擎替换 RapidOCR，不加载 ONNX 模型、不联网；
- 缓存目录强制指向 tmp_path，不写用户 backend/data/ocr_cache。
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest

from backend.app.services.parser import ocr

CONTENT_TOKEN = "SCAN_PAGE_CONFIDENTIAL_BODY"


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolated_ocr_runtime():
    """每个用例结束后清掉共享执行器、引擎引用/引擎池、空闲定时器和 STUCK 标记。"""
    yield
    try:
        ocr.shutdown_ocr_runtime()
    finally:
        ocr._rapid_engine = None
        ocr._RAPID_POOL = None
        ocr._OCR_ENGINE_STUCK.clear()
        ocr._OCR_ENGINE_ACTIVE = 0
        ocr._OCR_IDLE_TIMER = None


@pytest.fixture()
def cache_root(tmp_path, monkeypatch):
    """把缓存根切到临时目录，避免污染真实 data/ocr_cache。"""
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


class FakeEngine:
    """假 RapidOCR：记录调用次数、并发峰值，返回旧 tuple 结果格式。"""

    def __init__(self, text: str = CONTENT_TOKEN, delay: float = 0.02):
        self.calls: list[int] = []
        self.text = text
        self.delay = delay
        self._lock = threading.Lock()
        self._running = 0
        self.max_concurrency = 0

    def __call__(self, image):
        with self._lock:
            self._running += 1
            self.max_concurrency = max(self.max_concurrency, self._running)
        try:
            self.calls.append(getattr(image, "shape", [0, 0])[0] if hasattr(image, "shape") else 0)
            time.sleep(self.delay)
            rows = [[[[0, 0], [1, 0], [1, 1], [0, 1]], self.text, 0.93]]
            return rows, []
        finally:
            with self._lock:
                self._running -= 1


def build_pdf(path: Path, inked_pages: set[int] = frozenset(), total: int = 3,
              size: tuple[int, int] = (300, 400)) -> Path:
    """合成 PDF：inked_pages 中的页面画黑色长条（模拟有字），其余为纯白页。"""
    import fitz

    doc = fitz.open()
    try:
        for number in range(total):
            page = doc.new_page(width=size[0], height=size[1])
            if number + 1 in inked_pages:
                page.draw_rect(
                    __import__("fitz").Rect(10, 10, size[0] - 10, 60),
                    color=(0, 0, 0), fill=(0, 0, 0),
                )
        doc.save(path)
    finally:
        doc.close()
    return path


def run_rapid(pdf: Path, cache_dir: Path, fake: FakeEngine, *, factory=None,
              workers: int = 1, **kwargs):
    """以假引擎跑一次 _ocr_rapid。

    默认 workers=1 走第一阶段串行路径（本文件验证的正是这条回退档）；
    workers>=2 的流水线路径在 test_ocr_phase2.py 里验证。
    """
    import backend.app.services.parser.ocr as module
    from unittest import mock
    from backend.app.core.config import settings
    from unittest.mock import patch

    create = factory or (lambda: fake)
    with mock.patch.object(module, "_initialize_rapid_engine", create), \
         mock.patch.object(module, "_create_rapid_engine", create), \
         patch.object(settings, "ocr_workers", workers):
        return module._ocr_rapid(pdf, cache_dir=cache_dir, **kwargs)


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --------------------------------------------------------------------------- #
# 1. 空白页检测
# --------------------------------------------------------------------------- #
def test_ink_ratio_distinguishes_blank_content_and_noise():
    from PIL import Image, ImageDraw

    white = Image.new("RGB", (300, 400), "white")
    assert ocr.estimate_ink_ratio(white) == 0.0

    inked = Image.new("RGB", (300, 400), "white")
    ImageDraw.Draw(inked).rectangle([0, 0, 299, 40], fill="black")
    assert 0.05 < ocr.estimate_ink_ratio(inked) < 0.15

    page_number_only = Image.new("RGB", (300, 400), "white")
    ImageDraw.Draw(page_number_only).rectangle([20, 20, 40, 34], fill="black")
    ratio = ocr.estimate_ink_ratio(page_number_only)
    assert ratio < 0.008


def test_blank_page_flag_uses_threshold_and_fails_safe():
    from PIL import Image, ImageDraw

    white = Image.new("RGB", (200, 200), "white")
    assert ocr.is_blank_page(white, 0.008) is True
    assert ocr.is_blank_page(white, 0.0) is True  # 阈值 0：只有零墨迹页才跳

    inked = Image.new("RGB", (200, 200), "white")
    ImageDraw.Draw(inked).rectangle([0, 0, 199, 30], fill="black")
    assert ocr.is_blank_page(inked, 0.008) is False

    # 只有页号的页面：默认阈值下判为空白（这是跳过的前提）
    noisy = Image.new("RGB", (300, 400), "white")
    ImageDraw.Draw(noisy).rectangle([20, 20, 40, 34], fill="black")
    assert ocr.is_blank_page(noisy, 0.008) is True
    assert ocr.is_blank_page(noisy, 0.0) is False  # 阈值收紧 → 不再跳过

    # 统计失败（不是图像）→ 保守地当作有内容，不跳过 OCR
    assert ocr.is_blank_page(object(), 0.008) is False


# --------------------------------------------------------------------------- #
# 2. 共享执行器：复用 + 串行 + 不被每页关闭
# --------------------------------------------------------------------------- #
def test_shared_executor_is_reused_and_single_worker():
    first = ocr.get_ocr_executor()
    second = ocr.get_ocr_executor()
    assert first is second
    assert first._max_workers == 1  # 第一阶段仍然单页串行


def test_shared_executor_runs_pages_serially():
    executor = ocr.get_ocr_executor()
    lock = threading.Lock()
    state = {"running": 0, "peak": 0}

    def _task():
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        time.sleep(0.03)
        with lock:
            state["running"] -= 1

    futures = [executor.submit(_task) for _ in range(4)]
    for future in futures:
        future.result(timeout=10)
    assert state["peak"] == 1


def test_run_with_timeout_keeps_shared_executor_alive():
    executor = ocr.get_ocr_executor()
    thread_name = ocr._run_with_timeout(
        lambda: threading.current_thread().name, 5, 1, executor=executor
    )
    assert thread_name.startswith("ocr-shared")
    assert not executor._shutdown
    assert ocr.get_ocr_executor() is executor


def test_private_executor_still_shuts_down_when_omitted():
    assert ocr._run_with_timeout(lambda: 42, 5, 2) == 42


# --------------------------------------------------------------------------- #
# 3. 超时 / 卡死快速失败
# --------------------------------------------------------------------------- #
def test_page_timeout_sets_stuck_flag_and_clears_after_thread_ends():
    assert not ocr._OCR_ENGINE_STUCK.is_set()
    with pytest.raises(ocr.OCRPageTimeout, match="第 3 页"):
        ocr._run_with_timeout(lambda: time.sleep(0.25), 0.05, 3,
                              executor=ocr.get_ocr_executor())
    assert ocr._OCR_ENGINE_STUCK.is_set()
    assert wait_until(lambda: not ocr._OCR_ENGINE_STUCK.is_set())
    assert not ocr._OCR_ENGINE_STUCK.is_set()


def test_stuck_engine_fast_fails_next_run_without_waiting(cache_root):
    pdf = build_pdf(cache_root / "stuck.pdf")
    ocr._OCR_ENGINE_STUCK.set()
    with pytest.raises(ocr.OCRPageTimeout):
        run_rapid(pdf, ocr._ocr_cache_dir(ocr._file_hash(pdf)), FakeEngine(),
                  page_numbers={1, 2, 3}, base_pages=["", "", ""], render_dpi=72)


def test_wait_callback_exception_propagates_as_cancellation():
    class TaskCancelled(RuntimeError):
        pass

    def _cancel_checkpoint():
        raise TaskCancelled("任务已由用户取消")

    with pytest.raises(TaskCancelled):
        # callable 故意跑满 3 秒：future.result() 会在 2 秒轮询窗口先超时，
        # 于是走到 on_wait → 取消检查点抛出的异常必须原样冒泡（不能被吞掉）。
        ocr._run_with_timeout(lambda: time.sleep(3.0), 5, 1,
                              on_wait=_cancel_checkpoint,
                              executor=ocr.get_ocr_executor())


def test_checkpoint_exception_stops_ocr_and_keeps_done_cache(cache_root):
    pdf = build_pdf(cache_root / "cancel.pdf", inked_pages={1, 2, 3})
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))

    calls: list[tuple[int, str]] = []

    def _checkpoint(page_no: int, phase: str) -> None:
        calls.append((page_no, phase))
        if page_no == 2 and phase == "recognizing":
            raise RuntimeError("任务已由用户取消")

    with pytest.raises(RuntimeError):
        run_rapid(pdf, cache_dir, FakeEngine(), page_numbers={1, 2, 3},
                  base_pages=["", "", ""], on_checkpoint=_checkpoint, render_dpi=72)

    assert (cache_dir / "page_0001.txt").exists()  # 已完成页面缓存保留
    assert not (cache_dir / "page_0002.txt").exists()
    assert not list(cache_dir.glob("*.tmp"))


# --------------------------------------------------------------------------- #
# 4. 空白页跳过 / 空缓存 / 进度与结构一致 / 续跑
# --------------------------------------------------------------------------- #
def test_blank_pages_skip_ocr_write_empty_cache_and_keep_pagination(cache_root):
    pdf = build_pdf(cache_root / "blank.pdf", inked_pages={2})
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    fake = FakeEngine()
    progress: list[tuple[int, int, bool]] = []

    texts = run_rapid(
        pdf, cache_dir, fake,
        page_numbers={1, 2, 3}, base_pages=["", "", ""],
        on_progress=lambda page, total, cached: progress.append((page, total, cached)),
        render_dpi=72,
    )

    assert len(fake.calls) == 1  # 只有第 2 页跑了完整 OCR
    assert texts == ["", CONTENT_TOKEN, ""]
    assert [page for page, _, _ in progress] == [1, 2, 3]
    assert all(total == 3 for _, total, _ in progress)
    assert (cache_dir / "page_0001.txt").read_text(encoding="utf-8") == ""
    assert (cache_dir / "page_0003.txt").read_text(encoding="utf-8") == ""
    assert not list(cache_dir.glob("*.tmp"))  # 原子写没留下临时文件


def test_blank_detection_can_be_disabled_by_config(cache_root, monkeypatch):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "ocr_skip_blank_pages", False)
    pdf = build_pdf(cache_root / "no-skip.pdf", inked_pages=set())
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    fake = FakeEngine()

    run_rapid(pdf, cache_dir, fake, page_numbers={1, 2}, base_pages=["", ""],
              render_dpi=72)
    assert len(fake.calls) == 2  # 关闭开关后空白页也照常识别


def test_cached_pages_including_blank_resume_without_recognition(cache_root):
    pdf = build_pdf(cache_root / "resume.pdf", inked_pages={2})
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))

    first = FakeEngine()
    texts_first = run_rapid(pdf, cache_dir, first, page_numbers={1, 2, 3},
                            base_pages=["", "", ""], render_dpi=72)
    assert len(first.calls) == 1

    second = FakeEngine()
    progress: list[tuple[int, bool]] = []
    summary: dict = {}
    texts_second = run_rapid(
        pdf, cache_dir, second, page_numbers={1, 2, 3}, base_pages=["", "", ""],
        on_progress=lambda page, total, cached: progress.append((page, cached)),
        on_metrics=summary.update, render_dpi=72,
    )

    assert len(second.calls) == 0  # 全部命中缓存，包括空白页的空缓存
    assert texts_second == texts_first
    assert all(cached for _, cached in progress)
    assert summary["pages"]["cached"] == 3
    assert summary["pages"]["recognized"] == 0
    assert summary["pages"]["blank"] == 0


def test_legacy_plain_text_cache_is_still_reused(cache_root):
    pdf = build_pdf(cache_root / "legacy.pdf", inked_pages={2})
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    # 旧版本只有纯文本 page_NNNN.txt，没有任何 layout/metadata 文件
    (cache_dir / "page_0001.txt").write_text("旧版缓存正文", encoding="utf-8")

    fake = FakeEngine()
    texts = run_rapid(pdf, cache_dir, fake, page_numbers={1, 2, 3},
                      base_pages=["", "", ""], render_dpi=72)

    assert texts[0] == "旧版缓存正文"
    assert len(fake.calls) == 1  # 只剩真正缺缓存的第 2 页


# --------------------------------------------------------------------------- #
# 5. 性能指标
# --------------------------------------------------------------------------- #
def test_metrics_summary_has_no_text_or_paths(cache_root):
    pdf = build_pdf(cache_root / "metrics.pdf", inked_pages={2})
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    captured: dict = {}

    run_rapid(pdf, cache_dir, FakeEngine(), page_numbers={1, 2, 3},
              base_pages=["", "", ""], render_dpi=72, on_metrics=captured.update)

    assert captured, "未提供指标回调时也应产生摘要（这里回调拿到了摘要）"
    dump = json.dumps(captured, ensure_ascii=False)
    # 1) 不含正文
    assert CONTENT_TOKEN not in dump
    # 2) 不含完整路径（绝对路径、盘符、路径分隔符）
    assert "/" not in dump and "\\" not in dump
    assert re.search(r"[A-Za-z]:", dump) is None
    # 3) 结构完整
    assert captured["pages"]["total"] == 3
    assert captured["pages"]["blank"] == 2
    assert captured["pages"]["recognized"] == 1
    assert set(captured["seconds"]) == {
        "total", "render", "convert", "ocr", "cache_read", "cache_write",
    }
    assert captured["seconds"]["total"] > 0
    assert captured["seconds"]["ocr"] > 0
    assert captured["avg_seconds_per_page"] > 0
    assert captured["pages_per_minute"] > 0


def test_metrics_can_be_disabled(cache_root, monkeypatch):
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "ocr_metrics_enabled", False)
    pdf = build_pdf(cache_root / "no-metrics.pdf", inked_pages={2})
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))
    seen: list[dict] = []

    texts = run_rapid(pdf, cache_dir, FakeEngine(), page_numbers={1, 2, 3},
                      base_pages=["", "", ""], render_dpi=72,
                      on_metrics=seen.append)
    assert texts == ["", CONTENT_TOKEN, ""]
    assert seen == []


# --------------------------------------------------------------------------- #
# 6. 引擎生命周期：延迟释放 / 活动中不释放 / 连续导入复用
# --------------------------------------------------------------------------- #
def test_engine_is_not_released_while_recognition_is_active(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(ocr, "_rapid_engine", sentinel)
    with ocr.ocr_engine_session():
        assert ocr.release_ocr_engine() is False
        assert ocr._rapid_engine is sentinel
    assert ocr.release_ocr_engine() is True
    assert ocr._rapid_engine is None


def test_engine_is_not_released_while_engine_lock_is_held(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(ocr, "_rapid_engine", sentinel)
    with ocr._OCR_ENGINE_LOCK:
        assert ocr.release_ocr_engine() is False
        assert ocr._rapid_engine is sentinel


def test_engine_released_after_idle_timeout(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(ocr, "_rapid_engine", sentinel)
    timer = ocr.schedule_ocr_engine_release(idle_seconds=0.15)
    assert timer is not None and timer.daemon is True  # 不阻止进程退出
    assert wait_until(lambda: ocr._rapid_engine is None)
    assert ocr._rapid_engine is None


def test_new_task_cancels_pending_idle_release(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(ocr, "_rapid_engine", sentinel)
    ocr.schedule_ocr_engine_release(idle_seconds=0.2)
    ocr.touch_ocr_engine()  # 下一次 OCR 开始征用引擎
    time.sleep(0.4)
    assert ocr._rapid_engine is sentinel


def test_idle_timer_retries_while_recognition_active(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(ocr, "_rapid_engine", sentinel)
    with ocr.ocr_engine_session():
        ocr.schedule_ocr_engine_release(idle_seconds=0.15)
        time.sleep(0.35)
        assert ocr._rapid_engine is sentinel  # 活动期间绝不卸载
    assert wait_until(lambda: ocr._rapid_engine is None)


def test_consecutive_imports_reuse_warm_engine(cache_root, monkeypatch):
    first_pdf = build_pdf(cache_root / "book-a.pdf", inked_pages={2})
    second_pdf = build_pdf(cache_root / "book-b.pdf", inked_pages={1, 3})
    fake = FakeEngine()
    initializations: list[object] = []

    def _factory():
        # 与真实 _initialize_rapid_engine 语义一致：命中全局单例就不重建模型
        if ocr._rapid_engine is None:
            initializations.append(fake)
            ocr._rapid_engine = fake
        return ocr._rapid_engine

    monkeypatch.setattr(ocr, "_initialize_rapid_engine", _factory)
    monkeypatch.setattr(ocr, "_rapid_engine", None)
    for pdf in (first_pdf, second_pdf):
        run_rapid(pdf, ocr._ocr_cache_dir(ocr._file_hash(pdf)), fake, factory=_factory,
                  page_numbers={1, 2, 3}, base_pages=["", "", ""], render_dpi=72)
        # 每份文档结束后只"预约"空闲释放，下一次导入会复用热模型
        ocr.schedule_ocr_engine_release(idle_seconds=300)

    assert len(initializations) == 1  # 第二份文档没有重新加载 ONNX 模型
    assert ocr._rapid_engine is fake


def test_shutdown_ocr_runtime_clears_engine_timer_and_executor(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(ocr, "_rapid_engine", sentinel)
    executor = ocr.get_ocr_executor()
    ocr.schedule_ocr_engine_release(idle_seconds=30)

    ocr.shutdown_ocr_runtime()

    assert ocr._rapid_engine is None
    assert ocr._OCR_IDLE_TIMER is None
    assert ocr._OCR_EXECUTOR is None
    assert executor._shutdown


# --------------------------------------------------------------------------- #
# 7. 一次任务内复用同一个执行器（不再每页 new ThreadPoolExecutor）
# --------------------------------------------------------------------------- #
def test_single_task_creates_only_one_executor(cache_root, monkeypatch):
    real_executor = ocr.ThreadPoolExecutor
    created: list[str] = []

    class CountingExecutor(real_executor):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            created.append(kwargs.get("thread_name_prefix") or "")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(ocr, "ThreadPoolExecutor", CountingExecutor)
    pdf = build_pdf(cache_root / "shared.pdf", inked_pages={1, 2, 3})
    cache_dir = ocr._ocr_cache_dir(ocr._file_hash(pdf))

    fake = FakeEngine()
    run_rapid(pdf, cache_dir, fake, page_numbers={1, 2, 3},
              base_pages=["", "", ""], render_dpi=72)

    assert created == ["ocr-shared"]  # 没有 ocr-page-N
    assert fake.max_concurrency == 1  # 页面仍严格串行
    assert len(fake.calls) == 3
