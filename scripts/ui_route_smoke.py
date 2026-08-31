"""Read-only Playwright smoke test for key SPA routes and responsive overflow."""
from __future__ import annotations

import argparse
import json
import sys
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROUTES = [
    "/library", "/literature-workbench", "/chat", "/knowledge-hub?view=notes",
    "/knowledge", "/graph", "/study", "/writing", "/knowledge-health",
    "/quiz", "/draw", "/stats", "/settings",
]
VIEWPORTS = [{"name": "desktop", "width": 1440, "height": 900},
             {"name": "mobile", "width": 390, "height": 844}]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    with urlopen(base + "/api/health", timeout=5) as response:
        health = json.loads(response.read())
    if health.get("app") != "study-assistant":
        raise RuntimeError("目标服务不是 study-assistant")
    failures = []
    checks = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for viewport in VIEWPORTS:
            page = browser.new_page(viewport={"width": viewport["width"], "height": viewport["height"]})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            for route in ROUTES:
                errors.clear()
                response = page.goto(base + route, wait_until="networkidle", timeout=20000)
                page.wait_for_timeout(250)
                body = page.locator("body").inner_text().strip()
                overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
                checks += 1
                if response is None or response.status >= 400 or not body or overflow or errors:
                    failures.append({"viewport": viewport["name"], "route": route,
                                     "status": response.status if response else None,
                                     "empty": not bool(body), "horizontal_overflow": overflow,
                                     "errors": list(errors)})
            page.close()
        browser.close()
    print(json.dumps({"checks": checks, "passed": checks - len(failures), "failures": failures},
                     ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
