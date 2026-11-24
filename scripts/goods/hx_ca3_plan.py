# -*- coding: utf-8 -*-
"""按新规则生成「恒信仓 / 加州3仓」的目标值，并整理**要清零的清单元**清单。

用户 2026-11-23 口径：
    · 恒信仓（恒信加州3 / 恒信加州3仓） = max(0, ⌈领星可用 × 80%⌉)   ← 只看恒信领星
    · 加州3仓（帕沃尔加州3）= max(0, ⌈帕沃尔可用 × 80%⌉)              ← 只看帕沃尔
    · Kansas：不动
    · 目标算下来是 0 的（货号-尺码-sku）**不执行**，单独整理成清单等人工确认

数据源：
    领星：temu-admin/public/data/linxing-stock.json   (wh=ZOO WAREHOUSE ⑥美西)
    帕沃尔：temu-admin/public/data/pw-stock.json       (wh=加州3仓 / kansas仓)
    在售/SPU：data/stock-snapshot/<日期>.json           (每行 code/size/spu)

用法：
    python scripts/goods/hx_ca3_plan.py --store BHY
    python scripts/goods/hx_ca3_plan.py --store BHY --date 2026-11-23
输出：
    _logs/hx_ca3_plan.json      机读计划
    _logs/hx_ca3_zero_list.md   要清零的清单元清单（人工确认）
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "temu-admin" / "public" / "data"
SNAP = ROOT / "data" / "stock-snapshot"
LOGS = ROOT / "_logs"
RATIO = 0.8

CANON = {"KLS": "KinglamanSport"}


def load_linxing() -> dict:
    """(款-套系, 尺码) → 可用（只算领星恒信那个仓）"""
    d = json.loads((DATA / "linxing-stock.json").read_text(encoding="utf-8"))
    out: dict = {}
    for it in d.get("items") or []:
        code = str(it.get("code") or "")
        var = str(it.get("variant") or "")
        size = str(it.get("size") or "").upper()
        if not code or not size:
            continue
        k = (("%s-%s" % (code, var)).upper(), size)
        out[k] = out.get(k, 0.0) + float(it.get("avail") or 0)
    return out


def load_pw(wh_kw: str) -> dict:
    """(款-套系, 尺码) → 帕沃尔指定仓可用"""
    d = json.loads((DATA / "pw-stock.json").read_text(encoding="utf-8"))
    out: dict = {}
    for it in d.get("items") or []:
        if wh_kw not in str(it.get("wh") or ""):
            continue
        code = str(it.get("code") or "")
        var = str(it.get("variant") or "")
        size = str(it.get("size") or "").upper()
        if not code or not size:
            continue
        k = (("%s-%s" % (code, var)).upper(), size)
        out[k] = out.get(k, 0.0) + float(it.get("stock") or 0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--date", default=str(date.today()))
    a = ap.parse_args()
    store = CANON.get(a.store.upper(), a.store)

    lx = load_linxing()
    ca3 = load_pw("加州3仓")
    snap = json.loads((SNAP / ("%s.json" % a.date)).read_text(encoding="utf-8"))
    rows = ((snap.get("stores") or {}).get(store) or {}).get("rows") or []

    plan, zero = [], []
    for r in rows:
        code = str(r.get("code") or "")
        size = str(r.get("size") or "").upper()
        spu = str(r.get("spu") or "")
        base = code.split("-", 1)[1] if "-" in code else code
        k = (base.upper(), size)
        avail = float(lx.get(k) or 0)
        pw = float(ca3.get(k) or 0)
        hx = int(math.ceil(avail * RATIO)) if avail > 0 else 0
        paw = int(math.ceil(pw * RATIO)) if pw > 0 else 0
        item = {"code": code, "size": size, "spu": spu,
                "lxAvail": round(avail, 1), "hxTarget": hx,
                "pwAvail": round(pw, 1), "ca3Target": paw}
        if hx == 0 and paw == 0:
            zero.append(item)
        else:
            plan.append(item)

    out = {"date": a.date, "store": store, "ratio": RATIO,
           "rule": "恒信=⌈领星可用×80%⌉；加州3仓=⌈帕沃尔可用×80%⌉；Kansas 不动；算成 0 的要人工确认",
           "items": plan, "need_zero_confirm": zero}
    (LOGS / "hx_ca3_plan.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 要清零的清单元（货号-尺码）——待你确认", "",
          "来源：%s · %s" % (a.date, store), "",
          "口径：恒信 = ⌈领星可用×80%⌉、加州3仓 = ⌈帕沃尔可用×80%⌉；"
          "两边都算出 0 的行，**不会自动清零**，先请你确认。", "",
          "| 货号 | 尺码 | SPU | 领星可用 | 帕沃尔可用 |", "|---|---|---|---|---|"]
    for z in zero:
        md.append("| %s | %s | %s | %s | %s |" % (z["code"], z["size"], z["spu"],
                                                  z["lxAvail"], z["pwAvail"]))
    md.append("")
    md.append("共 **%d** 条；同货号有目标值的正常行 %d 条。" % (len(zero), len(plan)))
    (LOGS / "hx_ca3_zero_list.md").write_text("\n".join(md), encoding="utf-8")

    print("[%s] 有目标值的行 %d / 需要确认清零的行 %d" % (store, len(plan), len(zero)))
    print("→", LOGS / "hx_ca3_plan.json")
    print("→", LOGS / "hx_ca3_zero_list.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
