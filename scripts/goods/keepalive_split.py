# -*- coding: utf-8 -*-
"""跨平台「分摊保活」方案（TEMU 3 店 + SHEIN 3 店同池）。

用户 2026-11-19：
  · 「可售 ≤3 天：改成 <=2 天」；
  · 「如果可售天数 ≤3 天且**没有在途**：按照分摊保活策略：三个 temu 店铺 + 三个 shein 店铺分摊库存」。

口径（纯算数，不碰任何后台）：
  1. 只处理「可售 ≤ D0_MAX(2 天) 且**该尺码没有任何在途**」的 SKU；
  2. 这些 SKU 按「款号-套系-尺码」归成**一个池子**（同一批仓内库存供 6 家店卖）；
  3. 池子里仓内可用 = 恒信可用 + 帕沃尔两仓现货（**不重复计**：每家店看到的是同一份货）；
  4. 分摊方式：
       · 该池里各店的日均合计 daily_all；每家拿到的份 = 仓内可用 ×（本店日均 ÷ daily_all）；
       · 每家有**保底** `min_keep`（默认 2 件），但总挂单 ≤ 仓内可用；
       · 日均 0 的店不参与分摊（不占货）。
  5. 输出每家店的「建议挂多少」（清零还是留几件），供人工按店执行。

用法：
    python scripts/goods/keepalive_split.py               # 打印方案
    python scripts/goods/keepalive_split.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "temu-admin" / "public" / "data"

D0_MAX = 2.0      # 可售 ≤2 天
MIN_KEEP = 2      # 每家保底件数
TEMU_STORES = ("BEL", "BHY", "KinglamanSport")
SHEIN_STORES = ("BS", "BD", "BAN", "BE")


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _temu_skus() -> list:
    """(店, 货号, 套系, 尺码) + 日均 + 线上库存，来自在售款明细。"""
    g = _load(DATA / "goods.json")
    out = []
    for st in TEMU_STORES:
        for it in ((g.get("stores") or {}).get(st) or {}).get("items") or []:
            if it.get("status") != "在售中":
                continue
            code = str(it.get("code") or "")
            base = code.split("-", 1)[1] if "-" in code else code
            for sku in it.get("skus") or []:
                out.append({"platform": "TEMU", "store": st, "code": code,
                            "variant": base, "size": str(sku.get("size") or "").upper(),
                            "stock": sku.get("stock")})
    return out


def _shein_skus() -> list:
    p = _load(DATA / "shein-stock.json")
    out = []
    for st, blk in (p.get("stores") or {}).items():
        for it in (blk.get("items") or blk.get("rows") or []):
            code = str(it.get("code") or it.get("skc") or "")
            if not code:
                continue
            base = code.split("-", 1)[1] if "-" in code else code
            out.append({"platform": "SHEIN", "store": st, "code": code,
                        "variant": base, "size": str(it.get("size") or "").upper(),
                        "stock": it.get("onlineNow")})
    return out


def _avail_daily():
    """(款-套系大写, 尺码) → 仓内可用 / 7天日均 / 在途件数。"""
    avail, daily, transit = {}, {}, {}
    for x in (_load(DATA / "linxing-stock.json").get("items") or []):
        code, var = str(x.get("code") or ""), str(x.get("variant") or "")
        size = str(x.get("size") or "").upper()
        if not code or not size:
            continue
        k = (("%s-%s" % (code, var)).upper(), size)
        avail[k] = avail.get(k, 0.0) + float(x.get("avail") or 0)
        daily[k] = max(daily.get(k, 0.0), float(x.get("daily") or 0))
        transit[k] = max(transit.get(k, 0.0), float(x.get("transit") or 0))
    for x in (_load(DATA / "pw-stock.json").get("items") or []):
        code, var = str(x.get("code") or ""), str(x.get("variant") or "")
        size = str(x.get("size") or "").upper()
        if not code or not size:
            continue
        k = (("%s-%s" % (code, var)).upper(), size)
        avail[k] = avail.get(k, 0.0) + float(x.get("stock") or 0)
    return avail, daily, transit


def build() -> list:
    avail, daily, transit = _avail_daily()
    skus = _temu_skus() + _shein_skus()
    # 池子要按**货号-尺码**把「6 家店里所有在售这个 SKU 的店」都收进来
    # （不能先按可售天数筛掉店，否则就只剩一家，分红也就只剩一家）。
    by_key: dict = defaultdict(list)
    for s in skus:
        by_key[(s["variant"].upper(), s["size"])].append(s)
    cand = []
    for (variant, size), rows in sorted(by_key.items()):
        k = (variant, size)
        a = float(avail.get(k) or 0)
        d = float(daily.get(k) or 0)
        t = float(transit.get(k) or 0)
        if t > 0:                      # 有在途 → 不属这条策略（有货在路上，按预售走）
            continue
        d0 = (a / d) if d > 0 else (0 if a <= 0 else 999)
        if d0 > D0_MAX:                # 仓里还够卖 → 不动
            continue
        if all(float(s.get("stock") or 0) <= 0 for s in rows):
            # ★ 这些店线上本来就都是 0，没有「分摊保活」可言（清零是幂等的，不用再分货）
            continue
        for s in rows:
            cand.append({**s, "avail": a, "daily": d, "d0": round(d0, 2)})
    # 按池子（款号-套系-尺码）分组
    pools = defaultdict(list)
    for s in cand:
        pools[(s["variant"].upper(), s["size"])].append(s)
    out = []
    for (variant, size), rows in sorted(pools.items()):
        a = float(rows[0]["avail"])
        # ★ 日均按「(款-套系, 尺码)」查（不按店）：Temu 销量归 SPU，各店拿到的是同一个日均；
        #   这样才是「这批货有多少家店在卖、按日均分摊」。
        key = (variant, size)
        dal = float(daily.get(key) or 0) or max((float(r["daily"] or 0) for r in rows), default=0)
        n_store = len({(r["platform"], r["store"]) for r in rows})
        daily_all = dal * n_store
        for r in rows:
            r["daily"] = dal
        plan = []
        for r in rows:
            d = float(r["daily"] or 0)
            if daily_all <= 0:
                share = 0
            else:
                share = a * d / daily_all
            share = int(math.floor(share))
            keep = min(MIN_KEEP, int(a)) if d > 0 else 0
            qty = max(keep, share) if a > 0 else 0
            plan.append({"platform": r["platform"], "store": r["store"], "code": r["code"],
                         "size": size, "daily": round(d, 2), "keep": int(qty),
                         "action": "保留" if qty > 0 else "清零"})
        total = sum(p["keep"] for p in plan)
        # 总量超过仓内可用 → 按比例往下压（先砍日均小的）
        while total > a and any(p["keep"] > 0 for p in plan):
            for p in sorted(plan, key=lambda x: x["daily"]):
                if p["keep"] > 0 and total > a:
                    p["keep"] -= 1
                    total -= 1
        for p in plan:
            p["action"] = "保留" if p["keep"] > 0 else "清零"
        out.append({"variant": variant, "size": size, "avail": a,
                    "dailyAll": round(daily_all, 2), "stores": plan})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--show", type=int, default=20, help="打印前 N 个池子")
    a = ap.parse_args()
    pools = build()
    print("「可售 ≤%g 天 + 无在途」的池子 %d 个（跨 TEMU 3 店 + SHEIN 4 店分摊，保底 %d 件/店）"
          % (D0_MAX, len(pools), MIN_KEEP))
    for p in pools[:a.show]:
        stores = " | ".join("%s-%s 保留%d" % (s["store"], s["size"], s["keep"])
                            for s in p["stores"] if s["keep"] > 0)
        print("  %-24s %-4s 仓内%-6s 日均合计%-7s → %s"
              % (p["variant"], p["size"], p["avail"], p["dailyAll"], stores or "全清"))
    if a.json:
        Path(a.json).write_text(json.dumps({"generatedAt": str(date.today()),
                                            "d0Max": D0_MAX, "minKeep": MIN_KEEP,
                                            "pools": pools}, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print("已写入:", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
