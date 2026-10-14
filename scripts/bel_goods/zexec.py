# -*- coding: utf-8 -*-
"""通用页面 JS 执行/探测工具（可指定 store-id / target-id / window 变量）。

用法：
  python zexec.py <store-id> <target-id|-> <js文件> [输出json] [NAME=VALUE ...]

末尾的 NAME=VALUE 会逐个写入页面的 window.NAME，供 js 文件读取
（避免把值硬编码进脚本、也避免顶层声明撞全局绑定）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ENTRY = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@ziniao-open" / "cli" / "scripts" / "run.js"


def run(args, timeout=180):
    p = subprocess.run(["node", str(ENTRY)] + args, capture_output=True,
                       text=True, encoding="utf-8", timeout=timeout)
    if not p.stdout.strip():
        raise RuntimeError(f"no output: rc={p.returncode} err={p.stderr[:400]}")
    return json.loads(p.stdout)


def exec_js(store_id, target_id, js, timeout=180):
    args = ["page", "exec", "--store-id", store_id]
    if target_id and target_id != "-":
        args += ["--target-id", target_id]
    inner = run(args + ["--script", js], timeout)["data"]["data"]
    if inner.get("exceptionDetails"):
        raise RuntimeError("page exception: " +
                           json.dumps(inner["exceptionDetails"], ensure_ascii=False)[:600])
    return inner["result"]


def main():
    store_id = sys.argv[1]
    target_id = sys.argv[2]
    js = Path(sys.argv[3]).read_text(encoding="utf-8").strip()

    out = None
    rest = sys.argv[4:]
    if rest and "=" not in rest[0]:
        out = Path(rest[0])
        rest = rest[1:]

    # 逐个预置 window 变量
    for item in rest:
        name, _, val = item.partition("=")
        exec_js(store_id, target_id,
                "window.%s = %s" % (name, json.dumps(val, ensure_ascii=False)))

    raw = exec_js(store_id, target_id, js)
    if out:
        out.write_text(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False),
                       encoding="utf-8")
        print("saved ->", out)
    else:
        print(raw)


if __name__ == "__main__":
    main()