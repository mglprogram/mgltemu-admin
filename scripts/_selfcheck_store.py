# -*- coding: utf-8 -*-
"""自检：大屏数据是否真的跟着「全店汇总 / BEL / BHY」切换。

用途（2026-10-14 用户要求「自检店铺切换」后建立）：
    改完大屏取数逻辑后跑一遍，确认
      ① 每个卡片字段在三档下**有区分**（三档同值 = 多半没跟着切）；
      ② 全店合计 == 两店之和；
      ③ 各店 restock 明细字段齐全（缺 preSaleRows 会出现"计数有、明细空"）；
      ④ 「可加库存」用的是权威 verdictCounts，而不是宽松的 canRestock。

跑法：
    python scripts/_selfcheck_store.py
退出码非 0 表示有不一致。
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "temu-admin" / "public" / "data"
STORES = ["ALL", "BEL", "BHY"]

g = json.loads((DATA / "goods.json").read_text(encoding="utf-8"))
lim = json.loads((DATA / "limited.json").read_text(encoding="utf-8"))
flux = json.loads((DATA / "flux-boost.json").read_text(encoding="utf-8"))

problems: list[str] = []


def metrics(store: str) -> dict:
    """按 useDataScreenData.ts 的真实取数逻辑复算一张卡片的字段。"""
    out: dict = {}
    if store == "ALL":
        t = g["totals"]
        out.update(skc=t["onSaleSkc"], spu=t["spu"], stock=t["stock"])
    else:
        c = g["stores"][store]["counts"]
        out.update(skc=c["onSaleSkc"], spu=c["spu"], stock=c["stock"])

    # 限流
    if store == "ALL":
        out["limited"] = sum((s or {}).get("total", 0) for s in lim["stores"].values())
        out["focusHit"] = sum(w.get("hitCount", 0) for w in lim.get("watch") or [])
    else:
        out["limited"] = (lim["stores"].get(store) or {}).get("total", 0)
        w = next((x for x in lim.get("watch") or [] if x.get("store") == store), None)
        out["focusHit"] = (w or {}).get("hitCount", 0)

    # 低库存 / 补货（关键：走该店的 restock，而不是顶层合计）
    so = None if store == "ALL" else g["stores"][store]
    ls = so["lowStock"] if so else g["lowStock"]
    rest = ls.get("restock") or {}
    vc = rest.get("verdictCounts") or {}
    out["lowStyle"] = ls["summary"]["style"]
    out["lowSku"] = len(ls["rows"])
    out["canRestock"] = vc.get("可加库存", 0)
    out["presaleReady"] = vc.get("可开预售", 0)
    out["needRestock"] = vc.get("需补货", 0)
    out["suggestAddTotal"] = rest.get("suggestAddTotal", 0)
    out["preSaleRows"] = len(rest.get("preSaleRows") or [])

    # 流量加速
    if store == "ALL":
        out["fluxActive"] = sum(
            ((s.get("sections") or {}).get("accelerating") or {}).get("total", 0)
            for s in flux["stores"].values())
    else:
        sec = (flux["stores"].get(store) or {}).get("sections") or {}
        out["fluxActive"] = (sec.get("accelerating") or {}).get("total", 0)
    return out


res = {s: metrics(s) for s in STORES}

print("=" * 74)
print("大屏卡片 × 店铺切换 自检")
print("=" * 74)
print("%-22s %-14s %-14s %-14s" % ("字段", "ALL", "BEL", "BHY"))
print("-" * 74)

CHECK = [
    ("skc", "在售款数(SKC)"), ("spu", "在售SPU"), ("stock", "库存合计"),
    ("limited", "限流商品"), ("focusHit", "重点款命中"),
    ("lowStyle", "低库存-款数"), ("lowSku", "低库存-尺码数"),
    ("canRestock", "可加库存"), ("presaleReady", "可开预售"),
    ("needRestock", "需补货"), ("suggestAddTotal", "可补合计(件)"),
    ("preSaleRows", "预售明细行数"), ("fluxActive", "流量加速中"),
]
for key, label in CHECK:
    vals = [res[s][key] for s in STORES]
    note = ""
    # spu 在两店可能恰好相等，不做同值判定
    if key != "spu" and vals[0] == vals[1] == vals[2]:
        note = "  <<< 三档同值"
        problems.append("%s 三档同值，可能没跟着切换" % label)
    print("%-22s %-14s %-14s %-14s%s" % (label, vals[0], vals[1], vals[2], note))

print()
print("=== 全店合计 == BEL + BHY ===")
top = g["lowStock"]["restock"]
for name in ["可加库存", "可开预售", "需补货"]:
    a = (top["verdictCounts"] or {}).get(name, 0)
    b = (g["stores"]["BEL"]["lowStock"]["restock"]["verdictCounts"] or {}).get(name, 0)
    c = (g["stores"]["BHY"]["lowStock"]["restock"]["verdictCounts"] or {}).get(name, 0)
    ok = a == b + c
    print("  %-8s ALL=%-4d BEL+BHY=%-4d %s" % (name, a, b + c, "OK" if ok else "<<< 不等"))
    if not ok:
        problems.append("%s 合计对不上" % name)

print()
print("=== 各店 restock 字段完整性 ===")
need = {"verdictCounts", "suggestAddTotal", "canRestockSkus", "preSaleRows", "top", "waitingTop"}
for st in ["BEL", "BHY"]:
    r = g["stores"][st]["lowStock"]["restock"]
    miss = sorted(need - set(r.keys()))
    print("  %-4s %s" % (st, "OK" if not miss else "<<< 缺 %s" % miss))
    if miss:
        problems.append("%s restock 缺字段 %s（会导致计数有、明细空）" % (st, miss))

print()
print("=== 可加库存：权威 vs 宽松 canRestock（前端不得用 ||）===")
for st in STORES:
    so = None if st == "ALL" else g["stores"][st]
    rows = (so["lowStock"] if so else g["lowStock"])["rows"]
    loose = sum(1 for r in rows if r.get("verdict") == "可加库存" or r.get("canRestock"))
    auth = res[st]["canRestock"]
    print("  %-4s 权威=%-4d 宽松=%-4d %s" % (
        st, auth, loose, "OK" if loose == auth else "多算 %d" % (loose - auth)))

print()
if problems:
    print("发现问题 %d 项：" % len(problems))
    for p in problems:
        print("  - " + p)
    raise SystemExit(1)
print("全部一致 ✅")
