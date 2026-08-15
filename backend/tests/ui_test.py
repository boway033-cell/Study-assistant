"""UI 端到端测试（遵循 webapp-testing skill：recon-then-action）
覆盖：资料库(搜索) → 卡片复习 → 刷题 → 统计 → 设置
"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
passed = []
failed = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        passed.append(name)
        print(f"  ✅ {name}")
    else:
        failed.append(name)
        print(f"  ❌ {name} {detail}")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    console_errors = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))

    print("=== 1. 首页/资料库 ===")
    page.goto(BASE, wait_until="networkidle", timeout=15000)
    check("页面标题", "保研复习助手" in page.title())
    check("侧边栏菜单", page.locator(".el-menu-item").count() >= 5,
          f"count={page.locator('.el-menu-item').count()}")
    page.wait_for_selector("text=sample_math", timeout=10000)
    check("书籍列表显示", page.locator("text=sample_math").count() >= 1)
    page.screenshot(path="backend/tests/ui_1_library.png", full_page=True)

    print("=== 2. 全文搜索 ===")
    page.fill("input[placeholder*='输入关键词']", "拉格朗日")
    page.wait_for_timeout(300)
    # 用 button:has-text 精确定位搜索按钮
    page.locator("button:has-text('搜索')").first.click()
    page.wait_for_timeout(2000)
    check("搜索结果", page.locator(".result-item").count() >= 1,
          f"count={page.locator('.result-item').count()}")
    page.screenshot(path="backend/tests/ui_2_search.png", full_page=True)

    print("=== 3. 卡片复习页 ===")
    page.click("text=卡片复习")
    page.wait_for_timeout(1500)
    page.wait_for_selector(".el-table__row", timeout=10000)
    check("卡片列表", page.locator(".el-table__row").count() >= 2,
          f"count={page.locator('.el-table__row').count()}")
    page.screenshot(path="backend/tests/ui_3_review.png", full_page=True)

    print("=== 4. 刷题自测页 ===")
    page.click("text=刷题自测")
    page.wait_for_timeout(1500)
    page.wait_for_selector(".el-table__row", timeout=10000)
    check("题目列表", page.locator(".el-table__row").count() >= 2,
          f"count={page.locator('.el-table__row').count()}")
    # 作答第一题（选择题）
    page.locator(".el-table__row").first.locator("text=作答").click()
    page.wait_for_timeout(800)
    check("题目显示", page.locator(".quiz-question").count() == 1)
    # 选择答案 A（该题答案是 B，用于验证判分）
    page.locator(".choice-item").first.click()
    page.click("text=提交答案")
    page.wait_for_timeout(800)
    check("判分反馈", page.locator(".result-box").count() == 1)
    page.screenshot(path="backend/tests/ui_4_quiz.png", full_page=True)

    print("=== 5. 统计页 ===")
    page.click("text=学习统计")
    page.wait_for_timeout(2000)
    check("统计卡片", page.locator(".el-statistic").count() >= 4,
          f"count={page.locator('.el-statistic').count()}")
    page.screenshot(path="backend/tests/ui_5_stats.png", full_page=True)

    print("=== 6. 设置页 ===")
    page.click("text=设置")
    page.wait_for_timeout(1500)
    check("设置表单", page.locator(".el-form-item").count() >= 5,
          f"count={page.locator('.el-form-item').count()}")
    check("连接状态面板", page.locator("text=连接状态").count() == 1)
    page.screenshot(path="backend/tests/ui_6_settings.png", full_page=True)

    print("=== 7. AI 问答页（错误处理）===")
    page.click("text=AI 问答")
    page.wait_for_timeout(1500)
    page.fill("textarea", "什么是拉格朗日中值定理")
    for b in page.locator("button").all():
        if "发送" in b.inner_text() or "生成中" in b.inner_text():
            b.click()
            break
    page.wait_for_timeout(3000)
    body = page.inner_text("body")
    check("错误提示显示", "⚠️" in body)
    page.screenshot(path="backend/tests/ui_7_chat.png", full_page=True)

    browser.close()

print(f"\n===== 结果: {len(passed)} 通过 / {len(failed)} 失败 =====")
if failed:
    print("失败项:", failed)
    sys.exit(1)
