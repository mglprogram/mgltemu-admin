# -*- coding: utf-8 -*-
"""只看：库存跟踪里「在途」行的预测/上架日（口径与页面那一列一致）。

页面那一列 = 头程跟踪表的「预测 / 上架」列：
  有到港日 → 到港日 + 渠道中位天数（美森 ~4 / 以星 ~10 / 盐田 ~11 / 未知 ~9）
  无到港日 → OMS 自己给的预计到货（expectedDate）
本脚本把它和「在售款明细（goods.json）」对上，标出 **天数 ≤ N 且 TEMU 库存为 0** 的行
—— 这就是「点开预售」的候选。只读，不动任何页面。

    python scripts/goods/presale_candidates.py            # 默认 ≤7 天
    python scripts/goods/presale_candidates.py --days 7 --all
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:\AI-workplace\TEMU")
DATA = ROOT / "temu-admin" / "public" / "data"


def load(name: str) -> dict:
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def daily_of() -> dict:
    """「款·变体·尺码」→ 7 天日均（库存跟踪尺码行的 daily）；拿不到就是 0。

    ⚠ 用户口径（2026-11-09）：**看这个款-尺码有没有 7 天日均**（不是看商品名像不像），
      有日均 → 在途预售库存 = round(日均 × 7)；没有日均（缺失或 0）→ 默认 20。
    """
    track = load("stock-track.json")
    out = {}
    for src in (track.get("sources") or {}).values():
        for st in src.get("styles") or []:
            for v in st.get("variants") or []:
                for r in v.get("rows") or []:
                    k = ("%s-%s" % (st["code"], v["variant"]), r["size"])
                    d = float(r.get("daily") or 0)
                    if d > out.get(k, 0):
                        out[k] = d
    return out


def presale_qty(base: str, size: str, daily_map: dict, default: int = 20) -> tuple[int, str]:
    d = daily_map.get((base, size)) or 0
    if d <= 0:
        return default, "无 7 天日均 → 默认 %d" % default
    import math
    q = max(1, math.ceil(d * 7))
    return q, "日均 %.2f × 7 = %.2f → %d" % (d, d * 7, q)


def classify(eta: str, today: date):
    try:
        d = datetime.strptime(eta[:10], "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return None
    return (d - today).days


def build(days: int) -> list:
    ship = load("shipment-track.json")
    goods = load("goods.json")
    lead = ship.get("leadTime") or {}
    med = int((lead.get("overall") or {}).get("median") or 8)
    today = date.today()

    # ① 在途：尺码 → 最远预测上架日 + 件数
    eta = {}
    for r in ship.get("rows") or []:
        if r.get("status") in (4, 5):          # 已上架 / 已取消
            continue
        e = ""
        if r.get("arriveDate"):
            try:
                e = (datetime.strptime(r["arriveDate"][:10], "%Y-%m-%d")
                     + timedelta(days=med)).strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                e = ""
        e = e or (r.get("expectedDate") or "")
        for s in r.get("skus") or []:
            k = ((s.get("style") or "").replace("BS2-", "").replace("BH-", ""), s.get("size"))
            cur = eta.setdefault(k, {"eta": "", "qty": 0})
            if e and e > cur["eta"]:
                cur["eta"] = e
            cur["qty"] += int(s.get("qty") or 0)

    out = []
    for st, s in (goods.get("stores") or {}).items():
        for it in s.get("items") or []:
            code = it.get("code") or ""
            base = code.split("-", 1)[1] if "-" in code else code
            for size in ("S", "M", "L", "XL", "XXL", "3XL", "4XL"):
                k = (base, size)
                if k not in eta or not eta[k]["eta"]:
                    continue
                d = classify(eta[k]["eta"], today)
                if d is None or d > days:
                    continue
                out.append({"store": st, "code": code, "size": size, "spu": it.get("spuId"),
                            "temuStock": it.get("stock"), "eta": eta[k]["eta"], "days": d,
                            "inboundQty": eta[k]["qty"]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--all", action="store_true", help="连 TEMU 库存 >0 的也列出")
    a = ap.parse_args()
    rows = build(a.days)
    if not a.all:
        rows = [r for r in rows if not r["temuStock"]]
    print("%-26s %-5s %-11s %-5s %-10s %-6s %s" % ("货号", "尺码", "SPU", "天数", "预测上架", "TEMU", "在途件"))
    for r in sorted(rows, key=lambda x: (x["days"], x["code"])):
        print("%-26s %-5s %-11s %-5s %-10s %-6s %s" % (r["code"], r["size"], r["spu"], r["days"],
                                                     r["eta"], r["temuStock"], r["inboundQty"]))
    print("共 %d 行（口径：在途预测上架日 ≤ %d 天%s）" % (len(rows), a.days,
                                                "" if a.all else " 且 TEMU 库存=0"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
