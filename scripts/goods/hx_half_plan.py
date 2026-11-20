# -*- coding: utf-8 -*-
"""恒信仓库存新口径（用户 2026-11-20 定）：

    恒信加州3仓 = max(0, ⌈领星可用 ÷ 2⌉ − 加州三仓已有)

即「**两仓合计不超过一半**」：先把加州三仓现有的算进去，只把差额补到恒信仓。
    例：203311-4SetA-S 领星 13 → 一半 7；加州三仓已有 28（≥7）⇒ 恒信写 0。

数据来源：
    · 领星可用 / 一半 ← `data/stock-snapshot/<date>.json` 的 `lxAvail` / `hxHalf`
    · 加州三仓已有     ← 现场从编辑页读（最准）；读不到时退回 `data/pw-stock.json`

用法：
    python scripts/goods/hx_half_plan.py                 # BHY，打印计划（只读）
    python scripts/goods/hx_half_plan.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
DATA = ROOT / "temu-admin" / "public" / "data"
SNAP = ROOT / "data" / "stock-snapshot"
CA3_KEYS = ("加州三仓", "加州3仓")


def load_targets(store: str, day: str) -> list:
    d = json.loads((SNAP / ("%s.json" % day)).read_text(encoding="utf-8"))
    return ((d.get("stores") or {}).get(store) or {}).get("rows") or []


def pw_stock() -> dict:
    """(款-套系大写, 尺码) → 帕沃尔两仓总件数（兜底用）。"""
    out: dict = {}
    try:
        d = json.loads((DATA / "pw-stock.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    for x in d.get("items") or []:
        code = str(x.get("code") or "").strip()
        var = str(x.get("variant") or "").strip()
        size = str(x.get("size") or "").upper()
        wh = str(x.get("wh") or x.get("warehouse") or "")
        if not code or not size:
            continue
        if wh and not any(k in wh for k in ("加州",)):
            continue
        k = (("%s-%s" % (code, var)).upper(), size)
        out[k] = out.get(k, 0) + float(x.get("stock") or 0)
    return out


def plan(store: str, day: str) -> list:
    rows = load_targets(store, day)
    pw = pw_stock()
    out = []
    for r in rows:
        base = str(r.get("variant") or "").upper()
        size = str(r.get("size") or "").upper()
        half = int(r.get("hxHalf") or 0)
        if half <= 0 and int(r.get("lxAvail") or 0) <= 0:
            continue
        ca3 = float(pw.get((base, size), 0))     # 兜底估计（真实值在跑的时候以编辑页为准）
        target = max(0, half - int(ca3))
        out.append({"store": store, "code": r["code"], "size": size,
                    "lxAvail": r.get("lxAvail"), "half": half,
                    "ca3Est": ca3, "target": target,
                    "cut": target < half})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    rows = plan(a.store, a.date)
    cut = [r for r in rows if r["cut"]]
    print("[%s] %d 个尺码；其中因为加州三仓已有货而被下调的 %d 个" % (a.store, len(rows), len(cut)))
    for r in cut[:15]:
        print("  %-24s %-4s 领星%-6s 一半%-5s 加州三仓(估)%-6s → 恒信写 %d"
              % (r["code"], r["size"], r["lxAvail"], r["half"], r["ca3Est"], r["target"]))
    if a.json:
        Path(a.json).write_text(json.dumps({"date": a.date, "rows": rows},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
        print("已写入:", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
