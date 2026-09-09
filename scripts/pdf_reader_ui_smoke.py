"""Isolated 360-page PDF reader regression smoke test.

The fixture is synthetic and never reads the user's knowledge-base files.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(url: str, timeout: float = 25) -> None:
    from urllib.request import urlopen

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:  # noqa: S310 - loopback only
                if response.status == 200:
                    return
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    raise RuntimeError("isolated test server did not become ready")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="study_pdf_ui_") as temporary:
        data_dir = Path(temporary)
        uploads = data_dir / "uploads"
        uploads.mkdir(parents=True)
        os.environ["DATA_DIR"] = str(data_dir)

        import pymupdf

        pdf_path = uploads / "synthetic-large.pdf"
        document = pymupdf.open()
        scan_rows = {}
        for page_no in range(1, 361):
            scan = pymupdf.open() if page_no in (350, 351) else None
            page = (scan if scan is not None else document).new_page(width=595, height=842)
            page.insert_text((64, 92), f"Synthetic long document - page {page_no}", fontsize=18)
            page.insert_text((64, 132), "PDF rendering regression fixture", fontsize=11)
            if scan is not None:
                scan_rows[page_no] = []
                for block in page.get_text('dict')['blocks']:
                    for line in block.get('lines', []):
                        for span in line['spans']:
                            x0, y0, x1, y1 = span['bbox']
                            scan_rows[page_no].append([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]], span['text'], .99])
                image = page.get_pixmap().tobytes('png')
                document.new_page(width=595, height=842).insert_image(pymupdf.Rect(0, 0, 595, 842), stream=image)
                scan.close()
        document.set_toc([[1, 'Introduction', 1], [1, 'Methods', 121], [1, 'Conclusion', 241]])
        document.save(pdf_path)
        document.close()

        sys.path.insert(0, str(ROOT))
        from backend.app.core.database import Base, SessionLocal, engine
        from backend.app.models import Book
        from backend.app.services.parser.ocr import _file_hash, _ocr_cache_dir, _cache_rapid_layout
        cache_dir = _ocr_cache_dir(_file_hash(pdf_path))
        for page_no, rows in scan_rows.items():
            _cache_rapid_layout(cache_dir, page_no, rows, 595, 842)

        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            book = Book(
                title="Synthetic PDF regression",
                file_path=pdf_path.name,
                file_type="pdf",
                file_size=pdf_path.stat().st_size,
                status="ready",
                total_pages=360,
            )
            db.add(book)
            db.commit()
            db.refresh(book)
            book_id = book.id
        finally:
            db.close()
        from backend.app.services.parser.structured import extract_structured_pdf
        from backend.app.core.config import settings
        extract_structured_pdf(pdf_path, prefer_pdftext=False).save_json(settings.structured_dir / f'{book_id}.json')

        port = _free_port()
        environment = os.environ.copy()
        environment.update({"DATA_DIR": str(data_dir), "PORT": str(port)})
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        try:
            _wait_ready(f"http://127.0.0.1:{port}/api/health")
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
                page.goto(f"http://127.0.0.1:{port}/reader/{book_id}?page=176", wait_until="domcontentloaded")
                page.wait_for_selector('.pr-page[data-page="176"] canvas', timeout=20_000)
                page.wait_for_function(
                    """() => {
                      const canvas = document.querySelector('.pr-page[data-page="176"] canvas')
                      if (!canvas || canvas.width <= 1 || canvas.height <= 1) return false
                      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, Math.min(canvas.height, 320)).data
                      for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3] > 0 && (pixels[i] < 240 || pixels[i + 1] < 240 || pixels[i + 2] < 240)) return true
                      return false
                    }""",
                    timeout=20_000,
                )
                page.wait_for_selector(".pr-loading", state="detached", timeout=20_000)
                canvas_size = page.locator('.pr-page[data-page="176"] canvas').evaluate(
                    "canvas => [canvas.width, canvas.height]"
                )
                initial_state = page.evaluate(
                    """() => {
                      const scroller = document.querySelector('.pr-body')
                      const visible = document.elementFromPoint(window.innerWidth - 180, 320)?.closest('.pr-page')
                      const target = document.querySelector('.pr-page[data-page="176"]')?.getBoundingClientRect()
                      return { visiblePage: visible?.dataset.page || null, targetTop: target?.top, scrollTop: scroller?.scrollTop }
                    }"""
                )

                page.locator(".pr-pageinfo input").fill("348")
                page.locator(".pr-pageinfo input").press("Tab")
                page.wait_for_function(
                    """() => {
                      const canvas = document.querySelector('.pr-page[data-page="348"] canvas')
                      if (!canvas || canvas.width <= 1 || canvas.height <= 1) return false
                      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, Math.min(canvas.height, 320)).data
                      for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3] > 0 && (pixels[i] < 240 || pixels[i + 1] < 240 || pixels[i + 2] < 240)) return true
                      return false
                    }""",
                    timeout=20_000,
                )
                page.wait_for_function(
                    """() => {
                      const visible = document.elementFromPoint(window.innerWidth - 180, 320)?.closest('.pr-page')
                      const target = document.querySelector('.pr-page[data-page="348"]')?.getBoundingClientRect()
                      return visible?.dataset.page === '348' && target && target.top < 320 && target.bottom > 320
                    }""",
                    timeout=20_000,
                )
                jumped_state = page.evaluate(
                    """() => {
                      const scroller = document.querySelector('.pr-body')
                      const visible = document.elementFromPoint(window.innerWidth - 180, 320)?.closest('.pr-page')
                      const target = document.querySelector('.pr-page[data-page="348"]')?.getBoundingClientRect()
                      return { visiblePage: visible?.dataset.page || null, targetTop: target?.top,
                        targetBottom: target?.bottom, scrollTop: scroller?.scrollTop }
                    }"""
                )
                screenshot = os.environ.get("PDF_UI_SCREENSHOT")
                if screenshot:
                    Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=screenshot, full_page=False)
                # Partial text selection must save only the selected characters.
                text_span = page.locator('.pr-page[data-page="348"] .text-layer span').first
                text_span.wait_for()
                points = text_span.evaluate('''span => {
                  const node = span.firstChild, range = document.createRange();
                  range.setStart(node, 4); range.setEnd(node, 12);
                  const rect = range.getBoundingClientRect();
                  return {x: rect.left + .2, y: rect.top + rect.height / 2, end: rect.right - .2};
                }''')
                page.mouse.move(points['x'], points['y'])
                page.mouse.down()
                page.mouse.move(points['end'], points['y'], steps=12)
                page.mouse.up()
                page.locator('.pr-sel-bar').get_by_role('button', name='高亮', exact=True).click()
                page.wait_for_selector('.pr-page[data-page="348"] .pr-hl')
                highlight_width = page.locator('.pr-page[data-page="348"] .pr-hl').first.evaluate(
                    'el => el.getBoundingClientRect().width / el.parentElement.getBoundingClientRect().width')
                assert 0 < highlight_width < .2, highlight_width
                # A tall two-page spread must scroll without changing page number.
                page.locator('.pr-mode .el-radio-button').filter(has_text='双页').click()
                page.wait_for_selector('.pr-mode-double')
                for _ in range(4):
                    page.get_by_role('button', name='放大', exact=True).click()
                page.wait_for_function("() => document.querySelector('.pr-body').scrollHeight > document.querySelector('.pr-body').clientHeight + 100")
                page.locator('.pr-body').evaluate('el => el.scrollTop = 0')
                page.mouse.move(900, 550)
                page.mouse.wheel(0, 180)
                page.wait_for_function("() => document.querySelector('.pr-body').scrollTop > 30")
                assert page.locator('.pr-pageinfo input').input_value() == '348'
                page.locator('.pr-body').evaluate('el => el.scrollTop = el.scrollHeight')
                page.wait_for_timeout(300)
                page.mouse.wheel(0, 80)
                page.wait_for_timeout(300)
                page.mouse.wheel(0, 80)
                page.wait_for_function("() => document.querySelector('.pr-pageinfo input').value === '350'")
                page.get_by_role('button', name='适应页', exact=True).click()
                for scan_no in (350, 351):
                    page.wait_for_selector(f'.pr-page[data-page="{scan_no}"] span[data-source="ocr"]')
                right_span = page.locator('.pr-page[data-page="351"] span[data-source="ocr"]').first
                points = right_span.evaluate('''span => {
                  const range = document.createRange(); range.setStart(span.firstChild, 4); range.setEnd(span.firstChild, 12);
                  const rect = range.getBoundingClientRect();
                  return {x: rect.left + .2, y: rect.top + rect.height / 2, end: rect.right - .2};
                }''')
                page.mouse.move(points['x'], points['y'])
                page.mouse.down()
                page.mouse.move(points['end'], points['y'], steps=12)
                page.mouse.up()
                page.locator('.pr-sel-bar').get_by_role('button', name='高亮', exact=True).click()
                page.wait_for_selector('.pr-page[data-page="351"] .pr-hl')
                scan_highlight_width = page.locator('.pr-page[data-page="351"] .pr-hl').first.evaluate(
                    'el => el.getBoundingClientRect().width / el.parentElement.getBoundingClientRect().width')
                assert 0 < scan_highlight_width < .2, scan_highlight_width
                page.locator('.toc-review-btn').click()
                page.get_by_role('button', name='从已解析数据重识别', exact=True).click()
                page.get_by_role('button', name='重新识别', exact=True).click()
                page.get_by_text('目录已原位更新，可在修订记录中恢复旧版本', exact=True).wait_for()
                assert page.get_by_role('dialog', name='目录结构工作台').is_visible()
                assert page.locator('.pr-pageinfo input').input_value() == '350'
                browser.close()
            print({"pages": 360, "opened": 176, "initial": initial_state, "jumped": 348,
                   "final": jumped_state, "canvas": canvas_size, "loading_overlay": False,
                   "partial_highlight_width": highlight_width, "scan_right_highlight_width": scan_highlight_width,
                   "spread_scroll_then_turn": "passed", "single_book_toc_rebuild": "passed"})
        finally:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
            engine.dispose()
            # Windows may release the SQLite file handle a fraction after process exit.
            time.sleep(1)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
