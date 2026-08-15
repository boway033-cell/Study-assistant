# -*- coding: utf-8 -*-
"""GCS 多线程分块下载（绕过单连接限速）"""
import concurrent.futures as cf
import os
import sys

import httpx

URL = "https://storage.googleapis.com/qdrant-fastembed/fast-bge-small-zh-v1.5.tar.gz"
DEST = "backend/data/models/tmp/model.tar.gz"
CHUNK_MB = 4
WORKERS = 8


def get_size():
    with httpx.Client(timeout=30) as c:
        r = c.head(URL)
        return int(r.headers["content-length"])


def download_range(start, end, idx):
    headers = {"Range": f"bytes={start}-{end}"}
    for attempt in range(5):
        try:
            with httpx.Client(timeout=120) as c:
                r = c.get(URL, headers=headers)
                if r.status_code in (200, 206):
                    return idx, r.content
        except Exception:
            pass
    raise RuntimeError(f"range {start}-{end} failed")


def main():
    total = get_size()
    print(f"total: {total / 1e6:.1f} MB")
    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    if os.path.exists(DEST):
        os.remove(DEST)

    parts = []
    start = 0
    idx = 0
    while start < total:
        end = min(start + CHUNK_MB * 1024 * 1024 - 1, total - 1)
        parts.append((start, end, idx))
        start = end + 1
        idx += 1

    results = {}
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(download_range, s, e, i): i for s, e, i in parts}
        done = 0
        for fut in cf.as_completed(futs):
            i, data = fut.result()
            results[i] = data
            done += 1
            if done % 5 == 0 or done == len(parts):
                print(f"progress: {done}/{len(parts)} parts")

    with open(DEST, "wb") as f:
        for i in sorted(results):
            f.write(results[i])
    print(f"done, size={os.path.getsize(DEST)}")


if __name__ == "__main__":
    main()
