# -*- coding: utf-8 -*-
"""分析缺失章节（第3/4/9章）的标题页格式"""
import sys
sys.path.insert(0, ".")
import pymupdf
import re

doc = pymupdf.open("backend/data/uploads/130分 2020版公共管理学第三版(主看).pdf")
out = []
# 第2章 p42 到 第5章 p112 之间找第3/4章；第8章 p179 到 第10章 p188 找第9章
# 先全书找所有页的首行文本（前 3 行），看第3/4/9章标题怎么写的
for i in range(60, 200):
    txt = doc[i].get_text()
    lines = [l.strip() for l in txt.split("\n") if l.strip()]
    if not lines:
        continue
    head = " | ".join(lines[:3])[:80]
    # 找含"3"或"三"或"章"的页
    if re.search(r"(第[三四0-9]章|^3\s|章)", head):
        out.append(f"p{i+1}: {head}")
doc.close()
with open("backend/tests/missing_analysis.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out[:50]))
print("saved", len(out))
