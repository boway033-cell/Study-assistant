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
        for page_no in range(1, 361):
            page = document.new_page(width=595, height=842)
            page.insert_text((64, 92), f"Synthetic long document - page {page_no}", fontsize=18)
            page.insert_text((64, 132), "PDF rendering regression fixture", fontsize=11)
        document.save(pdf_path)
        document.close()

        sys.path.insert(0, str(ROOT))
        from backend.app.core.database import Base, SessionLocal, engine
        from backend.app.models import Book

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
                browser.close()
            print({"pages": 360, "opened": 176, "initial": initial_state, "jumped": 348,
                   "final": jumped_state, "canvas": canvas_size, "loading_overlay": False})
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
