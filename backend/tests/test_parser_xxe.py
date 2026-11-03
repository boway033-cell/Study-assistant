"""解析器 XXE 防护回归：恶意 docx 不得读取本地文件内容。

python-docx 的 XMLParser 使用 resolve_entities=False，本测试构造一个带外部实体引用的
docx，确认解析结果不包含实体指向的本地文件内容（即使解析直接失败也算安全）。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from docx import Document


MALICIOUS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!DOCTYPE w:document [
  <!ENTITY xxe SYSTEM "{uri}">
]>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>虚构正常正文</w:t></w:r></w:p>
    <w:p><w:r><w:t>&xxe;</w:t></w:r></w:p>
  </w:body>
</w:document>
"""


def test_docx_external_entity_is_not_resolved(tmp_path: Path):
    secret = tmp_path / "fictional-xxe-secret.txt"
    marker = "fictional-xxe-marker-9f3a"
    secret.write_text(marker, encoding="utf-8")

    source = tmp_path / "fictional.docx"
    document = Document()
    document.add_paragraph("虚构正常正文")
    document.save(source)

    malicious = MALICIOUS_XML.format(uri=secret.as_uri())
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(
        tmp_path / "fictional-patched.docx", "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                data = malicious.encode("utf-8")
            zout.writestr(item, data)

    from backend.app.services.parser import ParseError, parse_document

    try:
        result = parse_document(tmp_path / "fictional-patched.docx")
    except (ParseError, ValueError, KeyError, OSError):
        return  # 解析阶段直接拒绝同样安全
    text = "\n".join(result.pages)
    assert marker not in text
