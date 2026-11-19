# -*- coding: utf-8 -*-
"""抓 BHY / BEL 已开「15 天预售」的款-尺码明细（只读弹窗，不改平台）。

用户 2026-11-19：「把 BHY 和 BEL 开了 15 天预售的抓取下来并置顶在对应店铺的预售面板；
显示最近的补货日期，点击携带货号-变体-尺码即可跳转到头程跟踪」。

名单：`config/temu_presale_15d.json`
产物：`data/presale-15d.json`
    {updatedAt, stores: {BHY: {rows:[{store,code,size,spu,on,date,qty,temuStock,node,account,shelf}]}}}
    · `date` = 弹窗里该尺码的**补货日期（在途库存补货日期）**
    · `node/shelf` = 该尺码最早一票在途的最新物流节点与预测上架日（面板上给「头程跟踪」用）

用法：
    python scripts/goods/fetch_presale15.py            # 两个店都抓
    python scripts/goods/fetch_presale15.py --store BHY
    python scripts/goods/fetch_presale15.py --dry-run  # 只打印
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import bel_split_by_stock as B15  # noqa: E402
import plan_v4 as V4  # noqa: E402
import refresh_presale_live_fields as RF  # noqa: E402

OUT = ROOT / "data" / "presale-15d.json"
STORES = ("BHY", "BEL")


def _goods_spu() -> dict:
    """(店, 货号) → SPU（从在售款明细）。"""
    try:
        g = json.loads((ROOT / "temu-admin" / "public" / "data" / "goods.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for st, blk in (g.get("stores") or {}).items():
        for it in blk.get("items") or []:
            out[(st, str(it.get("code") or ""))] = str(it.get("spuId") or "")
    return out


async def grab(store: str) -> list:
    cfg = (B15.load_presale15() or {}).get(store) or {}
    if not cfg:
        print("[%s] 名单里没有这家店" % store)
        return []
    spu_of = _goods_spu()
    # 需要抓的 (货号, SPU)：名单里的货号可能带店前缀
    targets = []
    for (st, code), spu in spu_of.items():
        if st.upper() != store.upper():
            continue
        if any(B15.is_presale15(code, store) for _ in [0]):
            targets.append((code, spu))
    if not targets:
        print("[%s] 在售明细里找不到名单货号" % store)
        return []
    tab = await RF.setup(store)
    rows = []
    try:
        for code, spu in sorted(targets):
            got = await RF.read_code(tab, code, spu)
            if "_err" in got:
                print("  %-24s ✗ %s" % (code, got["_err"]))
                continue
            for size in sorted(got):
                f = got[size] or {}
                rows.append({
                    "store": store, "code": code, "size": size, "spu": spu,
                    "on": bool(f.get("on")), "date": str(f.get("date") or ""),
                    "qty": int(f.get("qty") or 0),
                    "temuStock": f.get("stock"),
                })
            print("  %-24s 尺码 %s" % (code, ",".join(sorted(got))))
    finally:
        try:
            await tab.keep_tab()
        except Exception:  # noqa: BLE001
            pass
    return rows


def attach_tracking(rows: list) -> list:
    """给每行补「最早一票在途」的节点/上架日（面板上点它跳头程跟踪）。"""
    tmap = V4.transit_index()
    today = date.today()
    for r in rows:
        base = str(r["code"]).split("-", 1)[1] if "-" in str(r["code"]) else str(r["code"])
        t = tmap.get((base.upper(), str(r["size"]).upper())) or {}
        shelf = t.get("okShelf") or t.get("any")
        r["node"] = t.get("node") or t.get("okNode") or ""
        r["account"] = t.get("account") or t.get("okAccount") or ""
        r["channel"] = t.get("channel") or t.get("okChannel") or ""
        r["shelf"] = str(shelf or "")
        r["shelfDays"] = (shelf - today).days if shelf else None
    return rows


async def run(stores: list, dry: bool) -> int:
    data = {}
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text(encoding="utf-8")).get("stores") or {}
        except Exception:  # noqa: BLE001
            data = {}
    for st in stores:
        rows = await grab(st)
        if not rows:
            continue
        rows = attach_tracking(rows)
        data[st] = {"rows": rows}
        print("[%s] %d 个尺码（开着的 %d）" % (st, len(rows), len([r for r in rows if r["on"]])))
        for r in rows:
            if r["on"]:
                print("   %-24s %-4s 补货 %-11s 在途预售 %-5s 节点 %s"
                      % (r["code"], r["size"], r["date"] or "-", r["qty"], (r["node"] or "-")[:28]))
    if dry:
        print("--dry-run：未回写")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rule": "已开 15 天预售（config/temu_presale_15d.json）：只能加、不能清/不能关",
        "stores": data,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写入:", OUT)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="ALL")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    stores = list(STORES) if a.store.upper() == "ALL" else [a.store.upper()]
    return asyncio.run(run(stores, a.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
