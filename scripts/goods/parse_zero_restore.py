# -*- coding: utf-8 -*-
"""从 _logs/api_hx_bhy.log 的「计划」行还原 加州三仓 / Kansas 的**改前数量**。

计划行格式：
    计划： [('XXL', '恒信加州3', 119, 234, 115), ('XXL', '加州三仓', 30, 0, -30), ...]
                 (尺码,  仓名,       改前, 目标, 差额)

输出：_logs/restore_zero_wh.json  → {code: {wh: {size: 原数量}}}
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "_logs"
SRC = LOGS / "api_hx_bhy.log"
OUT = LOGS / "restore_zero_wh.json"


def read_log(p: Path) -> str:
    raw = p.read_bytes()
    if b"\x00" in raw[:400]:
        return raw.decode("utf-16-le", "replace").replace("\x00", "")
    return raw.decode("utf-8", "replace")


def main():
    txt = read_log(SRC)
    cur = ""
    out: dict = {}
    for line in txt.splitlines():
        m = re.match(r"^== (\S+)（SPU", line.strip())
        if m:
            cur = m.group(1)
            continue
        if "计划" not in line or not cur:
            continue
        i = line.find("[")
        if i < 0:
            continue
        try:
            items = ast.literal_eval(line[i:].strip())
        except Exception:
            continue
        for sz, wh, before, target, diff in items:
            if "恒信" in wh:
                continue          # 只关心被清零的仓
            if int(before) == int(target):
                continue
            out.setdefault(cur, {}).setdefault(wh, {})[sz] = int(before)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(v) for c in out.values() for v in c.values())
    print("货号 %d 个 / 尺码-仓 %d 条" % (len(out), n))
    for c in list(out)[:5]:
        print(" ", c, out[c])
    print("→", OUT)


if __name__ == "__main__":
    main()
