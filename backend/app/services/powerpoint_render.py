"""按需调用真实 Microsoft PowerPoint 渲染，并执行轻量视觉回归。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from backend.app.core.config import PROJECT_ROOT, settings


def powerpoint_available() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Classes\PowerPoint.Application\CLSID"):
            return True
    except OSError:
        return False


def _natural_pngs(folder: Path) -> list[Path]:
    def key(path: Path):
        match = re.search(r"(\d+)", path.stem)
        return int(match.group(1)) if match else 10**9
    return sorted((path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".png"), key=key) if folder.exists() else []


def _image_difference(left: Path, right: Path) -> float:
    with Image.open(left) as a, Image.open(right) as b:
        a = a.convert("RGB").resize((320, 180)); b = b.convert("RGB").resize((320, 180))
        diff = ImageChops.difference(a, b)
        return round(sum(ImageStat.Stat(diff).mean) / (3 * 255), 6)


def render_powerpoint_preview(pptx_path: Path, deck_id: int, timeout: int = 90) -> dict:
    if not powerpoint_available():
        return {"ok": False, "engine": "unavailable", "issues": ["未检测到 Microsoft PowerPoint，跳过真实渲染"]}
    script = PROJECT_ROOT / "scripts" / "render_powerpoint.ps1"
    if not script.exists():
        return {"ok": False, "engine": "unavailable", "issues": ["PowerPoint 渲染脚本缺失"]}
    previews_root = settings.presentations_dir / "previews"
    previews_root.mkdir(parents=True, exist_ok=True)
    destination = (previews_root / str(deck_id)).resolve()
    if destination.parent != previews_root.resolve():
        raise ValueError("预览目录越界")
    with tempfile.TemporaryDirectory(prefix=f"deck-{deck_id}-", dir=previews_root) as temp:
        output = Path(temp) / "rendered"; output.mkdir()
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(script), "-InputPptx", str(pptx_path.resolve()),
                   "-OutputDir", str(output.resolve()), "-Width", "1600", "-Height", "900"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                    creationflags=flags, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "engine": "powerpoint", "issues": [f"真实渲染超过 {timeout} 秒"]}
        rendered = _natural_pngs(output)
        if result.returncode != 0 or not rendered:
            message = (result.stderr or result.stdout or "PowerPoint 未导出页面")[-800:]
            return {"ok": False, "engine": "powerpoint", "issues": [message]}
        previous = _natural_pngs(destination) if destination.exists() else []
        changed = []
        differences = []
        for index, current in enumerate(rendered):
            score = _image_difference(previous[index], current) if index < len(previous) else 1.0
            differences.append(score)
            if score > .015:
                changed.append(index + 1)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(output, destination)
    files = _natural_pngs(destination)
    return {"ok": True, "engine": "microsoft-powerpoint", "slide_count": len(files),
            "preview_files": [path.name for path in files], "changed_slides": changed,
            "difference_scores": differences, "baseline": bool(previous), "issues": []}
