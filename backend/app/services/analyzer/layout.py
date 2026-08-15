"""PDF 版面分析：识别标题 / 正文 / 页眉页脚 / 表格 / 公式（零模型内存）

方法：利用 PyMuPDF 提取的文本块字体、字号、位置（bbox）、加粗斜体等特征，
对每页内容做角色分类。这是 ChatPDF / Kimi 等主流 PDF 处理站点的通用做法，
比逐页跑视觉模型省资源、速度快，对文字版 PDF 效果已足够。

视觉模型可作为 P2 可选增强（本地 VLM 需 4-8GB 显存，不符合当前低内存约束）。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# 数学/公式常见字符（用于公式块判定）
_MATH_CHARS = set("∑∫∏√∂∇∞∈∉⊂⊆∪∩∀∃≈≠≤≥±×÷→←⇒⇔θαβγδελμπσφωΔΛΣΦΨΩξζηκντρς")

# 判定为"页眉页脚"的顶部/底部比例阈值
_HEADER_RATIO = 0.08   # 页面顶部 8%
_FOOTER_RATIO = 0.08   # 页面底部 8%


@dataclass
class BlockInfo:
    """一个文本块的分类结果。"""
    text: str
    size: float
    is_bold: bool
    is_italic: bool
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    block_type: str  # title / body / header / footer / table / formula / other
    page: int  # 1-based
    page_height: float


@dataclass
class LayoutResult:
    """版面分析结果。"""
    total_pages: int
    body_size: float  # 正文字号（全书众数）
    pages: list[list[BlockInfo]] = field(default_factory=list)  # 每页的块列表
    header_lines: set[str] = field(default_factory=set)   # 识别出的页眉文本
    footer_lines: set[str] = field(default_factory=set)   # 识别出的页脚文本
    title_lines: set[str] = field(default_factory=set)    # 识别出的标题文本
    table_pages: set[int] = field(default_factory=set)    # 含表格的页码

    def clean_page_text(self, page_idx: int) -> str:
        """返回去除页眉页脚/表格线后的正文文本（用于建索引）。"""
        parts = []
        for blk in self.pages[page_idx]:
            if blk.block_type in ("header", "footer"):
                continue
            parts.append(blk.text)
        return "\n".join(parts).strip()


def analyze_pdf(path: str | Path) -> LayoutResult:
    """分析 PDF 版面。"""
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    result = LayoutResult(total_pages=doc.page_count, body_size=12.0)

    # ---- 第一遍：收集所有字号，求正文字号（众数） ----
    size_counter: Counter = Counter()
    for page in doc:
        d = page.get_text("dict")
        for blk in d.get("blocks", []):
            if blk.get("type") != 0:
                continue
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    s = span.get("size", 12.0)
                    if s > 0:
                        size_counter[round(s, 1)] += 1
    if size_counter:
        result.body_size = size_counter.most_common(1)[0][0]

    # ---- 第二遍：逐块分类 ----
    all_blocks_by_text: dict[str, list[BlockInfo]] = {}
    for pno, page in enumerate(doc, start=1):
        ph = page.rect.height
        page_blocks: list[BlockInfo] = []
        d = page.get_text("dict")

        for blk in d.get("blocks", []):
            if blk.get("type") != 0:  # 图片块
                continue
            bbox = blk.get("bbox", (0, 0, 0, 0))
            x0, y0, x1, y1 = bbox
            # 聚合块内文本
            spans = []
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    spans.append(span)
            if not spans:
                continue

            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            # 块特征取第一个 span 的字号与字体标志
            size = spans[0].get("size", result.body_size)
            flags = spans[0].get("flags", 0)
            is_bold = bool(flags & 16)      # 2^4 = bold
            is_italic = bool(flags & 2)     # 2^1 = italic

            btype = _classify(text, size, is_bold, is_italic, bbox, ph, result.body_size)

            info = BlockInfo(
                text=text, size=size, is_bold=is_bold, is_italic=is_italic,
                bbox=(x0, y0, x1, y1), block_type=btype, page=pno, page_height=ph,
            )
            page_blocks.append(info)
            all_blocks_by_text.setdefault(text, []).append(info)

        # 表格检测（PyMuPDF find_tables）
        try:
            tables = page.find_tables()
            if tables.tables:
                result.table_pages.add(pno)
        except Exception:  # noqa: BLE001
            pass

        result.pages.append(page_blocks)

    # ---- 页眉页脚跨页识别：同一文本在 >=3 页的顶部/底部反复出现 ----
    for text, blocks in all_blocks_by_text.items():
        pages = {b.page for b in blocks}
        if len(pages) < 3 or len(text) > 60:
            continue
        # 至少 80% 出现在顶部或底部
        top = sum(1 for b in blocks if b.bbox[1] < b.page_height * _HEADER_RATIO)
        bottom = sum(1 for b in blocks if b.bbox[3] > b.page_height * (1 - _FOOTER_RATIO))
        if top >= 0.8 * len(blocks):
            result.header_lines.add(text)
            for b in blocks:
                b.block_type = "header"
        elif bottom >= 0.8 * len(blocks):
            result.footer_lines.add(text)
            for b in blocks:
                b.block_type = "footer"

    # ---- 标题集合 ----
    for blocks in result.pages:
        for b in blocks:
            if b.block_type == "title":
                result.title_lines.add(b.text)

    doc.close()
    return result


def _classify(text: str, size: float, is_bold: bool, is_italic: bool,
              bbox: tuple, page_height: float, body_size: float) -> str:
    """单块分类。"""
    x0, y0, x1, y1 = bbox
    # 页眉/页脚（顶部/底部边缘）
    if y0 < page_height * _HEADER_RATIO:
        return "header"
    if y1 > page_height * (1 - _FOOTER_RATIO):
        return "footer"
    # 公式：含大量数学符号，或斜体且居中
    math_ratio = sum(1 for c in text if c in _MATH_CHARS) / max(len(text), 1)
    if math_ratio > 0.05:
        return "formula"
    # 标题：字号显著大于正文，或加粗且文本较短
    if size >= body_size * 1.15 or (is_bold and len(text) <= 40 and size >= body_size * 1.05):
        return "title"
    # 孤立短行 + 居中（可能是图注/小标题），归 body 即可
    return "body"
