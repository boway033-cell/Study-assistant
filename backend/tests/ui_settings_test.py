"""设置页 E2E：切换云端 + 填 Key + 保存 + 探测（验证云端切换 bug 修复）"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:8000/#/settings", wait_until="networkidle")
    page.wait_for_timeout(1500)

    # 1. 切到云端
    page.click("text=云端（DeepSeek）")
    page.wait_for_timeout(500)

    # 2. 填 Key
    page.fill("input[placeholder*='sk-']", "sk-e2e-test-9999")

    # 3. 保存
    for b in page.locator("button").all():
        if "保存设置" in b.inner_text():
            b.click()
            break
    page.wait_for_timeout(2000)

    # 4. 验证提示
    body = page.inner_text("body")
    print("保存提示出现:", "设置已保存" in body)

    # 5. 探测结果（DeepSeek 应"已配置"；Ollama 未装应"未连接"）
    page.wait_for_timeout(1000)
    body2 = page.inner_text("body")
    print("DeepSeek 已配置:", "已配置" in body2)
    print("Ollama 未连接:", "未连接" in body2)
    page.screenshot(path="backend/tests/ui_settings_cloud.png")

    # 6. 切回本地（清理测试状态）
    page.click("text=本地（Ollama）")
    for b in page.locator("button").all():
        if "保存设置" in b.inner_text():
            b.click()
            break
    page.wait_for_timeout(1500)
    print("切回本地成功:", "设置已保存" in page.inner_text("body"))

    browser.close()
    print("DONE")
