# -*- coding: utf-8 -*-
"""给 scripts\\tasks\\run_*.cmd 统一加「一律直连」的环境清理块（幂等）。

背景（2026-11-08 实测）：用户换了代理软件，注册表 `HTTP(S)_PROXY` 还指着
`127.0.0.1:7897`，但那个端口没人监听 -> 子进程照它连就 WinError 10061，
任务集体「失败」，日志只会写「网络/代理不通」。本机是国内 IP，直连即可。

只在 `@echo off` 之后插入一段 ASCII 的 `set "HTTP_PROXY="`，已存在则跳过。
用法：
    python scripts/tasks/ensure_direct_net_cmd.py            # 预览
    python scripts/tasks/ensure_direct_net_cmd.py --apply    # 真写
"""
from __future__ import annotations

import glob
import os
import sys

BLOCK = [
    "REM ---- DIRECT NETWORK (2026-11-08) ----",
    "REM Proxy is optional here: this box is on a domestic IP. A stale",
    "REM HTTP(S)_PROXY pointing at a dead 127.0.0.1:7897 made every child",
    "REM fail with WinError 10061. Clear it so children go direct.",
    'set "HTTP_PROXY="',
    'set "HTTPS_PROXY="',
    'set "http_proxy="',
    'set "https_proxy="',
    'set "NO_PROXY=*"',
    'set "no_proxy=*"',
]
MARK = "DIRECT NETWORK (2026-11-08)"
HERE = os.path.dirname(os.path.abspath(__file__))


def patch(path: str, apply: bool) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if MARK in text:
        return "skip (already)"
    lines = text.splitlines()
    idx = 0
    for i, ln in enumerate(lines[:10]):
        if ln.strip().lower().startswith("@echo off"):
            idx = i + 1
            break
    new = lines[:idx] + BLOCK + lines[idx:]
    if apply:
        with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write("\n".join(new) + "\n")
    return "patched"


def main(argv: list) -> int:
    apply = "--apply" in argv
    files = sorted(glob.glob(os.path.join(HERE, "run_*.cmd")))
    for p in files:
        if os.path.basename(p).lower() == "run_via_dsh.cmd":
            continue
        print("%-42s %s" % (os.path.basename(p), patch(p, apply)))
    print("(预览；加 --apply 生效)" if not apply else "(已写入)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
