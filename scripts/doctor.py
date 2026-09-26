"""安装前后的一键只读诊断。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app.services.diagnostics import collect_diagnostics  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raw = os.environ.get("DATA_DIR")
    data_dir = (Path(raw) if raw else ROOT / "backend" / "data")
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    result = collect_diagnostics(ROOT, data_dir.resolve())
    if "--json" in sys.argv:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result["checks"]:
            print(f"[{'OK' if item['ok'] else '需处理'}] {item['detail']}")
            if item["action"]:
                print(f"        {item['action']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
