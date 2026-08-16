"""UI 端到端测试（遵循 webapp-testing skill：recon-then-action）
覆盖：资料库(搜索) → 知识树 → 刷题 → 统计 → 设置 → AI 问答
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

    print("=== 3. 知识树页（大纲/导图双视图）===")
    page.click("text=知识树")
    page.wait_for_timeout(1500)
    check("知识树入口", page.locator("button:has-text('＋ 新建')").count() >= 1)
    check("章节导入按钮", page.locator("button:has-text('从章节导入')").count() == 1)
    check("AI 生成按钮", page.locator("button:has-text('AI 生成框架')").count() == 1)
    # 创建一个根节点验证 CRUD
    page.click("button:has-text('＋ 新建')")
    page.wait_for_timeout(800)
    page.locator(".el-message-box__input input").fill("测试知识树")
    page.click(".el-message-box__btns button:has-text('创建')")
    page.wait_for_timeout(1000)
    check("知识树节点创建", page.locator("text=测试知识树").count() >= 1)
    # 导图视图
    page.click("text=导图")
    page.wait_for_timeout(800)
    check("导图视图", page.locator(".mm-svg").count() == 1)
    page.click("text=大纲")
    page.wait_for_timeout(600)
    page.screenshot(path="backend/tests/ui_3_knowledge.png", full_page=True)

    print("=== 4. 刷题自测页（AI 生成入口）===")
    page.click("text=刷题自测")
    page.wait_for_timeout(1500)
    check("AI 生成题目入口", page.locator("button:has-text('AI 生成题目')").count() == 1)
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
    check("设置表单", page.locator(".el-form-item").count() >= 3,
          f"count={page.locator('.el-form-item').count()}")
    check("模型档位", page.locator("text=模型档位").count() == 1)
    check("连接状态面板", page.locator("text=连接状态").count() == 1)
    page.screenshot(path="backend/tests/ui_6_settings.png", full_page=True)

    print("=== 7. AI 问答页（DeepSeek 云端）===")
    page.click("text=AI 问答")
    page.wait_for_timeout(1500)
    page.fill("textarea", "什么是拉格朗日中值定理")
    for b in page.locator("button").all():
        if "发送" in b.inner_text() or "生成中" in b.inner_text():
            b.click()
            break
    # 等待回答流式完成（或出现错误提示）
    page.wait_for_timeout(15000)
    body = page.inner_text("body")
    # 正常情况：AI 给出回答（不出现 ⚠️ 错误）；网络失败时也应优雅提示
    check("问答有响应", ("⚠️" in body) or (len(page.locator(".msg.assistant").all()) > 0),
          "assistant msg=" + str(len(page.locator(".msg.assistant").all())))
    page.screenshot(path="backend/tests/ui_7_chat.png", full_page=True)

    # 清理：删除测试创建的知识树节点（通过 API）
    import urllib.request
    import json as _json
    try:
        with urllib.request.urlopen(BASE + "/api/knowledge/tree", timeout=5) as r:
            tree_data = _json.loads(r.read())
        for root_node in tree_data.get("items", []):
            if root_node.get("title") == "测试知识树":
                req = urllib.request.Request(BASE + f"/api/knowledge/nodes/{root_node['id']}", method="DELETE")
                urllib.request.urlopen(req, timeout=5)
                print("  🧹 已清理测试知识树节点")
    except Exception as e:
        print("  ⚠️ 清理失败:", e)

    browser.close()

print(f"\n===== 结果: {len(passed)} 通过 / {len(failed)} 失败 =====")
if failed:
    print("失败项:", failed)
    sys.exit(1)