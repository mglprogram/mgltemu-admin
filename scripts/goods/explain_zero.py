# -*- coding: utf-8 -*-
"""帮人判断「哪些该清零」：把 待清零 逐条摊开成人能看懂的证据。

用户 2026-11-20：「我也不知道哪些要清零？」
⇒ 本脚本不做决定，只把**判断依据**摆出来：线上库存 / 可售天数 / 有没有在途 / 在途什么时候上架 /
   最新物流节点 / 同款其它尺码状态，并给一句「建议动作 + 理由」。

用法：
    python scripts/goods/explain_zero.py                 # BHY，读当天快照
    python scripts/goods/explain_zero.py --store BEL
    python scripts/goods/explain_zero.py --limit 50
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAP = ROOT / "data" / "stock-snapshot"
DATA = ROOT / "temu-admin" / "public" / "data"


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()
    import sys  # noqa: PLC0415

    sys.path.insert(0, str(ROOT / "scripts" / "goods"))
    import plan_v4 as V4  # noqa: PLC0415

    snap = _load(SNAP / ("%s.json" % a.date))
    rows = ((snap.get("stores") or {}).get(a.store) or {}).get("rows") or []
    if not rows:
        print("没有快照：%s（先跑 daily_stock_snapshot.py）" % (SNAP / ("%s.json" % a.date)))
        return 1
    tmap = V4.transit_index()
    today = date.today()
    todo = [r for r in rows if r["class"] == "待清零"]
    print("[%s] 待清零 %d 条 —— 逐条证据（清零=把 TEMU 线上库存改 0）：\n" % (a.store, len(todo)))
    by_style: dict = {}
    for r in rows:
        by_style.setdefault(r["code"], []).append(r)
    for r in todo[:a.limit]:
        base = r["variant"].upper()
        t = tmap.get((base, r["size"].upper())) or {}
        shelf = t.get("okShelf") or t.get("any")
        days = (shelf - today).days if shelf else None
        d0 = (r["lxAvail"] / r["daily"]) if r["daily"] else 0
        others = ["%s%s" % (x["size"], "✓" if x["class"] == "待开预售" else "")
                  for x in by_style.get(r["code"], []) if x["size"] != r["size"]]
        if r["lxTransit"] > 0 and days is not None and days <= 6:
            adv, why = "可清零（同款可开预售）", "有在途且 %d 天能上架，靠预售接单" % days
        elif r["lxTransit"] > 0:
            adv, why = "可清零", "有在途但 %s 天才上架（>6 天），这段没货可卖" % (days if days is not None else "?")
        else:
            adv, why = "可清零（无在途）", "仓里和路上都没有，留着库存也发不出"
        print("%-22s %-4s 线上%-5s 可售%-5s天 日均%-6s 在途%-5s 上架%-11s 节点:%s"
              % (r["code"], r["size"], r["temuStock"], round(d0, 2), r["daily"],
                 r["lxTransit"], str(shelf or "-"), (t.get("node") or "无")[:16]))
        print("    → %s｜%s" % (adv, why))
        print("    同款其它尺码：%s" % ("，".join(others) or "无"))
    print("\n判据（v4）：可售 ≤2 天 → 该处理；有在途且 ≤6 天能上架 → 清零+开预售；"
          "否则清零（或等货到再加回）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
