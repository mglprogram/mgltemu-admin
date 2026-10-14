# -*- coding: utf-8 -*-
"""通用 probe：把指定 JS 文件的结果写到 UTF-8 文件再打印，避免控制台乱码。"""
import json
import os
import subprocess
import sys
from pathlib import Path

ENTRY = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"
STORE_ID = os.environ.get("STORE_ID", "27697436126374")
TARGET_ID = os.environ.get("TARGET_ID", "E23FAD857ABE7424E74B1D8E13F39A0C")


def run(args, timeout=180):
    p = subprocess.run(["node", str(ENTRY)] + args, capture_output=True,
                       text=True, encoding="utf-8", timeout=timeout)
    if not p.stdout.strip():
        raise RuntimeError(f"no output: rc={p.returncode} err={p.stderr[:400]}")
    return json.loads(p.stdout)


def exec_js(js, timeout=180):
    args = ["page", "exec", "--store-id", STORE_ID]
    if TARGET_ID:
        args += ["--target-id", TARGET_ID]
    inner = run(args + ["--script", js], timeout)["data"]["data"]
    if inner.get("exceptionDetails"):
        raise RuntimeError("page exception: " + json.dumps(inner["exceptionDetails"], ensure_ascii=False)[:800])
    return inner["result"]


def exec_json(js):
    raw = exec_js(js)
    return json.loads(raw) if isinstance(raw, str) else raw


def main():
    js_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    js = js_path.read_text(encoding="utf-8").strip()
    raw = exec_js(js)
    if out_path:
        out_path.write_text(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False),
                            encoding="utf-8")
        print("saved ->", out_path)
    else:
        print(raw)


if __name__ == "__main__":
    main()