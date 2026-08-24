"""独立 PP-DocLayout-M ONNX worker；只执行版面区域检测，不启动 PP-StructureV3。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="PP-DocLayout-M")
    parser.add_argument("--cpu-threads", type=int, default=2)
    parser.add_argument("--image-size", type=int, default=960)
    args = parser.parse_args()

    from paddleocr import LayoutDetection

    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    configured_model_dir = os.environ.get("PP_DOCLAYOUT_MODEL_DIR")
    model_dir = str(Path(configured_model_dir).resolve()) if configured_model_dir else None
    model = LayoutDetection(
        model_name=args.model,
        model_dir=model_dir,
        device="cpu",
        engine="onnxruntime",
        engine_config={"cpu_threads": max(1, min(args.cpu_threads, 4))},
        img_size=max(640, min(args.image_size, 1280)),
    )
    results = model.predict(args.input, batch_size=1, layout_nms=True)
    for index, result in enumerate(results, start=1):
        result.save_to_json(save_path=str(output_dir / f"page_{index:04d}.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
