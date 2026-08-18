"""OCR 可插拔层：扫描版 PDF 识别

策略：
- 检测：页均文本量低于阈值 → 判定为扫描版（无文本层）
- 后端按需加载（不预装，避免内存占用）：
  1. pytesseract（需系统安装 Tesseract，轻量）
  2. PaddleOCR（pip 安装，较重，内存占用高，可选）

默认：无 OCR 后端时抛出明确错误，提示用户安装方案。
"""
from __future__ import annotations

from pathlib import Path

# 页均字符低于此值判定为扫描版（无文本层）
SCAN_THRESHOLD = 30


def detect_scanned(pages: list[str]) -> bool:
    """检测是否为扫描版（页均文本量过低）。"""
    if not pages:
        return True
    total_chars = sum(len(p.strip()) for p in pages)
    avg = total_chars / len(pages)
    return avg < SCAN_THRESHOLD


def has_ocr_engine() -> bool:
    """检查是否安装了可用的 OCR 引擎（pytesseract 或 paddleocr）。"""
    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import paddleocr  # noqa: F401
        return True
    except ImportError:
        pass
    return False


def ocr_pdf(path: str | Path) -> list[str]:
    """对扫描版 PDF 做 OCR，返回每页文本。

    按优先级尝试后端：pytesseract → paddleocr。
    均不可用时抛出 RuntimeError 并给出安装指引。
    """
    p = Path(path)
    if p.suffix.lower() != ".pdf":
        raise RuntimeError("OCR 仅支持 PDF 文件")

    # 尝试 pytesseract
    try:
        import pytesseract  # noqa: F401
        return _ocr_tesseract(p)
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        # tesseract 装了但二进制缺失等，继续尝试下一个
        pass

    # 尝试 paddleocr
    try:
        import paddleocr  # noqa: F401
        return _ocr_paddle(p)
    except ImportError:
        pass

    raise RuntimeError(
        "该 PDF 为扫描版（无文本层），且未检测到可用的 OCR 引擎。"
        "请任选其一安装：\n"
        "1) Tesseract：https://github.com/UB-Mannheim/tesseract/wiki 下载安装，"
        "勾选中文语言包，再 pip install pytesseract\n"
        "2) PaddleOCR：pip install paddlepaddle paddleocr（体积较大）"
    )


def _render_pdf_pages(p: Path) -> list:
    """渲染 PDF 每页为图片。返回 PIL Image 列表。"""
    import fitz  # PyMuPDF
    from PIL import Image

    doc = fitz.open(p)
    images = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(img)
    doc.close()
    return images


def _ocr_tesseract(p: Path) -> list[str]:
    import pytesseract
    from PIL import Image

    images = _render_pdf_pages(p)
    texts = []
    for img in images:
        txt = pytesseract.image_to_string(img, lang="chi_sim+eng")
        texts.append(txt)
    return texts


def _ocr_paddle(p: Path) -> list[str]:
    from paddleocr import PaddleOCR

    images = _render_pdf_pages(p)
    ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    texts = []
    for img in images:
        import numpy as np
        result = ocr.ocr(np.array(img), cls=True)
        lines = []
        if result and result[0]:
            for line in result[0]:
                txt = line[1][0] if len(line) > 1 else ""
                if txt:
                    lines.append(txt)
        texts.append("\n".join(lines))
    return texts
