# -*- coding: utf-8 -*-
"""检查当前 LLM 配置与书籍状态"""
import sys
sys.path.insert(0, ".")
from sqlalchemy import create_engine, text

eng = create_engine("sqlite:///backend/data/study.db")
with eng.connect() as c:
    rows = c.execute(text("SELECT key, value FROM settings")).all()
    for k, v in rows:
        if k == "deepseek_api_key" and v:
            v = v[:6] + "***" + v[-4:]
        print(f"{k} = {v}")
    print("--- books ---")
    books = c.execute(text("SELECT id, title, status, total_pages FROM books")).all()
    for b in books:
        print(f"  {b}")
    print("--- chapters of book 4 ---")
    chs = c.execute(text("SELECT id, title, start_page, end_page FROM chapters WHERE book_id=4 ORDER BY order_index")).all()
    for ch in chs:
        print(f"  {ch}")
