"""UI 测试：原文定位面板（搜索结果 → 查看原文 → 抽屉显示）"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:8000", wait_until="networkidle")
    page.wait_for_selector("text=sample_math", timeout=10000)

    # 1. 搜索
    page.fill("input[placeholder*='输入关键词']", "拉格朗日")
    page.locator("button:has-text('搜索')").first.click()
    page.wait_for_timeout(2000)
    print("搜索结果:", page.locator(".result-item").count())

    # 2. 点击查看原文
    page.locator("button:has-text('查看原文')").first.click()
    page.wait_for_timeout(2000)
    body = page.inner_text("body")
    print("抽屉打开:", "原文定位" in body)
    print("显示原文文本:", "拉格朗日中值定理" in body)
    print("显示页码:", "第" in body and "页" in body)
    page.screenshot(path="backend/tests/ui_original.png")

    browser.close()
    print("DONE")
