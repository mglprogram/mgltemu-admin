# -*- coding: utf-8 -*-
"""presale_plan.py — 按「在售款明细 + 领星总库存」算出今天该清零/该开预售的待办。

用户 2026-11-13 定的口径（v3）：
    在库天数 D0 = (恒信可用 + 帕沃尔加州3 + Kansas) ÷ 7天日均
    交期 L      = 该尺码最早一票在途的预测上架日 − 今天
    D0 ≤ 1 天（硬线）            → 清零；在途 7 天内到 → 同时开预售
    D0 ≥ L + 2                   → 不动
    D0 < L + 2 且 ETA ≤ 7 天     → 清零 + 开预售
    D0 < L + 2 且 ETA > 7/无     → 只清零
    无在途                        → 只清零
预售量 = min(在途, max(5, ceil(本店日均 × 3)))

只算数、不碰浏览器 —— 给面板/飞书用。
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "temu-admin" / "public" / "data"
PRESALE_QTY_DAYS = 3.0
BUFFER_DAYS = 2.0
HARD_DAYS = 1.0
KEEP_DAYS = 7.0  # 没有在途时，可用（含替发）够卖这么久就不清零

# 替发/拆解规则库（config/temu_stock_rules.json）：本款吃紧时，同组别款有货也算「可卖」
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # noqa: SIM105
    import substitute_rules as SUB  # noqa: E402
except Exception as _e:  # noqa: BLE001
    SUB = None
    print("[sub] 替发规则不可用：%s" % _e)


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _idx() -> tuple[dict, dict, dict]:
    """(款-套系, 尺码) → 恒信可用/在途/日均；帕沃尔现货；最近 ETA。"""
    lx_idx, pw_idx, eta_idx = {}, {}, {}
    for x in (_load(DATA / "linxing-stock.json").get("items") or []):
        code, var, size = str(x.get("code") or ""), str(x.get("variant") or ""), str(x.get("size") or "")
        if not code or not size:
            continue
        k = ("%s-%s" % (code, var) if var else code, size)
        prev = lx_idx.get(k) or {"avail": 0.0, "transit": 0.0, "daily": 0.0}
        lx_idx[k] = {
            "avail": prev["avail"] + float(x.get("avail") or 0),
            "transit": max(prev["transit"], float(x.get("transit") or 0)),
            "daily": max(prev["daily"], float(x.get("daily") or 0)),
        }
    for x in (_load(DATA / "pw-stock.json").get("items") or []):
        code, var, size = str(x.get("code") or ""), str(x.get("variant") or ""), str(x.get("size") or "")
        if not code or not size:
            continue
        k = ("%s-%s" % (code, var) if var else code, size)
        pw_idx[k] = pw_idx.get(k, 0.0) + float(x.get("stock") or 0)
    today = date.today()
    for row in (_load(DATA / "shipment-track.json").get("rows") or []):
        s = str(row.get("predictedShelf") or "")[:10]
        if not s:
            continue
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < today:
            continue
        for sk in row.get("skus") or []:
            name = str(sk.get("productName") or sk.get("style") or "")
            for pre in ("BS2-", "BH2-", "BH-", "BS-"):
                if name.startswith(pre):
                    name = name[len(pre):]
                    break
            if "-" not in name:
                continue
            base, size = name.rsplit("-", 1)
            k = (base, size)
            if k not in eta_idx or d < eta_idx[k]:
                eta_idx[k] = d
    return lx_idx, pw_idx, eta_idx


def build(stores=("BEL", "BHY"), use_sub: bool = True, keep_days: float | None = None) -> list:
    goods = _load(DATA / "goods.json")
    lx, pw, etas = _idx()
    cat = SUB.catalog() if (SUB and use_sub) else set()
    keep = KEEP_DAYS if keep_days is None else float(keep_days)

    def stock_fn(st: str, sz: str) -> float:
        """(款号, 尺码) → 可用件数（恒信可用 + 帕沃尔两仓现货）。"""
        k = (SUB.style_of(st) if SUB else st, str(sz).upper())
        return float((lx.get(k) or {}).get("avail") or 0) + float(pw.get(k) or 0)

    out = []
    for st in stores:
        for it in ((goods.get("stores") or {}).get(st) or {}).get("items") or []:
            if it.get("status") != "在售中":
                continue
            code = it.get("code") or ""
            base = code.split("-", 1)[1] if "-" in code else code
            for sku in it.get("skus") or []:
                size = str(sku.get("size") or "").upper()
                k = (base, size)
                l = lx.get(k) or {"avail": 0.0, "transit": 0.0, "daily": 0.0}
                avail_raw = float(l["avail"]) + float(pw.get(k, 0.0))
                transit = float(l["transit"])
                daily = float(l["daily"])
                if daily <= 0:
                    continue
                # ★ 2026-11-18 用户给的替发/拆解规则：本款吃紧（可用<=0 或 可售<=3 天）时，
                #   同组别款有货 / 能拆出来，也算这尺码有货 —— 取 max、不相加。
                #   作用：别再把「被同组别款顶着的款」误判成要清零/开预售。
                avail, sub_from = avail_raw, ""
                if SUB and use_sub:
                    eff, src = SUB.RULES.best(base, size, stock_fn, catalog=cat,
                                              own=avail_raw, daily=daily)
                    if src and eff > avail_raw:
                        avail, sub_from = eff, src
                d0 = avail / daily
                eta = etas.get(k)
                L = (eta - date.today()).days if eta else None
                if d0 <= HARD_DAYS:
                    action = "开预售" if (transit > 0 and L is not None and L <= 7) else "只清零"
                elif L is not None and d0 >= L + BUFFER_DAYS:
                    continue
                elif d0 >= keep:
                    # ★ 2026-11-18：没有「近到货」可等，而仓里（含同组替发）够卖一周以上 ⇒ 不动。
                    #   旧口径漏了这条：在途=0 时一律「只清零」，把 1091-1B / 205304-5SetA /
                    #   102105-3BGR 这类还有 200~1100 天库存的款也列进了待清零（82 条垃圾待办）。
                    continue
                elif transit <= 0:
                    action = "只清零"
                elif L is not None and L <= 7:
                    action = "开预售"
                else:
                    action = "只清零"
                qty = 0
                if action == "开预售":
                    qty = min(int(transit) if transit > 0 else 10 ** 9,
                              max(5, math.ceil(daily * PRESALE_QTY_DAYS)))
                out.append({
                    "store": st, "code": code, "size": size, "spu": it.get("spuId"),
                    "title": it.get("title") or "", "temuStock": sku.get("stock"),
                    "avail": round(avail, 1), "availRaw": round(avail_raw, 1),
                    "subFrom": sub_from,
                    "daily": round(daily, 2), "d0": round(d0, 2),
                    "transit": int(transit), "eta": str(eta or ""), "leadDays": L,
                    "action": action, "suggestQty": qty,
                })
    out.sort(key=lambda x: (x["store"], x["action"], x["d0"], x["code"], x["size"]))
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="TEMU 预售/清零待办（只算数）")
    ap.add_argument("--no-sub", action="store_true", help="不套用替发/拆解规则（config/temu_stock_rules.json）")
    ap.add_argument("--stores", default="BEL,BHY", help="店铺，默认 BEL,BHY")
    ap.add_argument("--keep-days", type=float, default=KEEP_DAYS,
                    help="没有更近的到货时，可用够卖这么多天就不清零（默认 %g，设很大=回到旧口径）" % KEEP_DAYS)
    _a = ap.parse_args()
    rows = build(stores=tuple(x.strip().upper() for x in _a.stores.split(",") if x.strip()),
                 use_sub=not _a.no_sub, keep_days=_a.keep_days)
    print("待办 %d 条：开预售 %d / 只清零 %d（替发/拆解生效 %d 条）" % (
        len(rows), sum(1 for r in rows if r["action"] == "开预售"),
        sum(1 for r in rows if r["action"] == "只清零"),
        sum(1 for r in rows if r.get("subFrom"))))
    for r in rows:
        print("  %-4s %-22s %-4s 线上%-5s 在库%-6s天 在途%-5s ETA=%-11s → %s%s%s"
              % (r["store"], r["code"], r["size"], r["temuStock"], r["d0"], r["transit"],
                 r["eta"] or "-", r["action"],
                 ("（预售 %d）" % r["suggestQty"]) if r["action"] == "开预售" else "",
                 ("  [%s]" % r["subFrom"]) if r.get("subFrom") else ""))
