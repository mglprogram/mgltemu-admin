# -*- coding: utf-8 -*-
"""复核：把「恒信仓」现值与目标 ⌈领星可用/2⌉ 逐条比对（只读，用现有标签页，稳）。

用法：python scripts/goods/verify_hx_half.py [--codes A,B] [--store BHY]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

SNAP = ROOT / "data" / "stock-snapshot"


def load_targets(store: str, codes: list, day: str) -> dict:
    d = json.loads((SNAP / ("%s.json" % day)).read_text(encoding="utf-8"))
    rows = ((d.get("stores") or {}).get(store) or {}).get("rows") or []
    out = {}
    for r in rows:
        if codes and r["code"] not in codes:
            continue
        out.setdefault(r["code"], {})[str(r["size"]).upper()] = int(r.get("hxHalf") or 0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--codes", default="")
    a = ap.parse_args()
    codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    tgt = load_targets(a.store, codes, a.date)
    led = {}
    p = ROOT / "data" / "hx-half-ledger.json"
    if p.exists():
        for it in (json.loads(p.read_text(encoding="utf-8")).get("items") or []):
            led.setdefault((it["store"], it["code"]), {})[it["size"]] = it["hx"]
    print("[%s] %d 个货号" % (a.store, len(tgt)))
    miss = 0
    for code, sizes in sorted(tgt.items()):
        wrote = led.get((a.store, code)) or {}
        if not wrote:
            continue
        same = all(str(wrote.get(sz)) == str(v) for sz, v in sizes.items())
        print("  %-24s 已写 %s %s" % (code, "✅" if same else "⚠", wrote))
        miss += 0 if same else 1
    print("已写入台账的货号 %d 个；与目标不一致 %d 个" % (len(led), miss))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
