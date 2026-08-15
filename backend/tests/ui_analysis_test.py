"""UI 测试：智能分析面板展示 + 宽定位问答（无 LLM 时验证检索与错误处理）"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:8000", wait_until="networkidle")
    page.wait_for_selector("text=sample_math", timeout=10000)

    # 1. 打开书籍详情，验证智能分析面板
    page.click("text=详情")
    page.wait_for_timeout(1500)
    body = page.inner_text("body")
    print("智能分析标题:", "智能分析" in body)
    print("关键词显示:", "关键词" in body)
    print("定理/公式显示:", "定理" in body or "公式" in body)
    page.screenshot(path="backend/tests/ui_analysis.png")

    # 2. 点击关键词应触发搜索（keyword-chip 原生元素）
    page.wait_for_selector(".keyword-chip", timeout=10000)
    page.locator(".keyword-chip").first.click()
    page.wait_for_timeout(3000)
    body2 = page.inner_text("body")
    print("点击关键词搜索:", "共" in body2 and "条结果" in body2)
    page.screenshot(path="backend/tests/ui_analysis_search.png")

    # 3. AI 问答页宽定位（无 LLM 时应走目录兜底/错误提示而非崩溃）
    page.click("text=AI 问答")
    page.wait_for_timeout(1200)
    page.fill("textarea", "牛顿莱布尼茨公式是什么")
    for b in page.locator("button").all():
        if "发送" in b.inner_text() or "生成中" in b.inner_text():
            b.click()
            break
    page.wait_for_timeout(3000)
    body3 = page.inner_text("body")
    print("问答页正常响应(错误提示或回答):", "⚠️" in body3)
    page.screenshot(path="backend/tests/ui_chat_wide.png")

    browser.close()
    print("DONE")
