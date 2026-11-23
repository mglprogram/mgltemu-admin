# -*- coding: utf-8 -*-
"""恒信 / 加州3 / Kansas 三仓目标值 + 接口执行（BEL / KLS / BHY 通用）。

用户 2026-11-23 最终口径：
    恒信仓   = max(0, ⌈领星可用 × 80%⌉)
    加州3仓  = max(0, ⌈帕沃尔加州3可用 × 80%⌉)
    Kansas   = 0                       ← 直接清零
    例外：恒信与加州3 **两边都算出 0** 的（货号-尺码）不自动执行，
          整理成清单交人工确认（一边 0、一边有值 → 正常执行）。

执行走接口（复用 api_hx_stock 的链路）：
    skc/pageQuery → queryBtgProductStockInfo → preCheckWarehouseInfo → updateMmsBtgProductSalesStock → 回读

用法：
    python scripts/goods/api_hx_ca3.py --port 63317 --store BEL           # 只看
    python scripts/goods/api_hx_ca3.py --port 63317 --store BEL --apply   # 真写
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import api_hx_stock as HX  # noqa: E402
import api_switch_mode as api  # noqa: E402

DATA = ROOT / "temu-admin" / "public" / "data"
SNAP = ROOT / "data" / "stock-snapshot"
LOGS = ROOT / "_logs"
LEDGER = LOGS / "api_hx_ca3.jsonl"
RATIO = 0.8
CANON = {"KLS": "KinglamanSport"}


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False)[:420], flush=True)


def load_index(path: Path, wh_kw: str, field: str) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    out: dict = {}
    for it in d.get("items") or []:
        if wh_kw and wh_kw not in str(it.get("wh") or ""):
            continue
        code = str(it.get("code") or "")
        var = str(it.get("variant") or "")
        size = str(it.get("size") or "").upper()
        if not code or not size:
            continue
        k = (("%s-%s" % (code, var)).upper(), size)
        out[k] = out.get(k, 0.0) + float(it.get(field) or 0)
    return out


def build_plan(store: str, day: str) -> tuple:
    lx = load_index(DATA / "linxing-stock.json", "", "avail")
    ca3 = load_index(DATA / "pw-stock.json", "加州3仓", "stock")
    snap = json.loads((SNAP / ("%s.json" % day)).read_text(encoding="utf-8"))
    rows = ((snap.get("stores") or {}).get(CANON.get(store.upper(), store)) or {}).get("rows") or []
    plan, zero = {}, []
    for r in rows:
        code = str(r.get("code") or "")
        size = str(r.get("size") or "").upper()
        base = code.split("-", 1)[1] if "-" in code else code
        k = (base.upper(), size)
        la = float(lx.get(k) or 0)
        pa = float(ca3.get(k) or 0)
        hx = int(math.ceil(la * RATIO)) if la > 0 else 0
        ca = int(math.ceil(pa * RATIO)) if pa > 0 else 0
        if hx == 0 and ca == 0:
            zero.append({"code": code, "size": size, "spu": str(r.get("spu") or ""),
                         "lx": round(la, 1), "pw": round(pa, 1)})
            continue
        plan.setdefault(code, {"spu": str(r.get("spu") or ""), "sizes": {}})
        plan[code]["sizes"][size] = {"hx": hx, "ca3": ca}
    return plan, zero


async def do_code(cap, hdr, code, info, apply):
    q = await HX.call(cap, HX.URL_SKC, {"fuzzySkuExtCodes": [code], "page": 1, "pageSize": 20}, hdr)
    if q.get("status") != 200:
        log({"event": "skc-fail", "code": code, "status": q.get("status")})
        return "no-header" if q.get("status") == 403 else "fail"
    items = ((q.get("json") or {}).get("result") or {}).get("pageItems") or []
    if not items:
        log({"event": "skc-empty", "code": code})
        return "fail"
    it = items[0]
    spu = str(it.get("productId"))
    sku_size = {}
    for s in it.get("productSkuSummaries") or []:
        ext = str(s.get("extCode") or "")
        sz = ext.rsplit("-", 1)[-1].upper() if "-" in ext else ""
        if s.get("productSkuId") and sz:
            sku_size[int(s["productSkuId"])] = sz
    r = await HX.call(cap, HX.URL_QUERY, {"productId": int(spu),
                                          "productSkuIdList": sorted(sku_size)}, hdr)
    rows = []
    HX.walk_stock(r.get("json") or {}, rows)
    change, detail = [], []
    for x in rows:
        sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else None
        if not sz or sz not in info["sizes"]:
            continue
        wh = x.get("wh") or ""
        t = info["sizes"][sz]
        if "恒信" in wh:
            target = t["hx"]
        elif ("加州三" in wh) or ("加州3仓" in wh):
            target = t["ca3"]
        elif ("kansas" in wh.lower()) or ("堪萨斯" in wh):
            target = 0
        else:
            continue
        diff = target - int(x["stock"])
        if diff:
            change.append({"productSkuId": int(x["skuId"]), "stockDiff": diff,
                           "currentStockAvailable": int(x["stock"]),
                           "currentShippingMode": 1, "warehouseId": x["whid"]})
            detail.append((sz, wh, int(x["stock"]), target, diff))
    if not change:
        return "nothing"
    print("  %s 计划 %s" % (code, detail), flush=True)
    if not apply:
        return "dry"
    pre = await HX.call(cap, HX.URL_CHECK, {"productId": int(spu), "preCheckSkuWarehouseList": [
        {"productSkuId": c["productSkuId"], "warehouseId": c["warehouseId"]} for c in change]}, hdr)
    up = await HX.call(cap, HX.URL_UPDATE, {"productId": int(spu), "skuStockChangeList": change,
                                            "skuTypeChangeList": [], "isCheckVersion": True,
                                            "source": 0}, hdr)
    good = bool((up.get("json") or {}).get("success"))
    log({"event": "write", "code": code, "ok": good, "n": len(change),
         "precheck": bool((pre.get("json") or {}).get("success")),
         "resp": str(up.get("body"))[:100]})
    await asyncio.sleep(0.5)
    r2 = await HX.call(cap, HX.URL_QUERY, {"productId": int(spu),
                                           "productSkuIdList": sorted(sku_size)}, hdr)
    rows2 = []
    HX.walk_stock(r2.get("json") or {}, rows2)
    got = {}
    for x in rows2:
        sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else None
        if sz:
            got.setdefault(sz, {})[x["wh"]] = int(x["stock"])
    log({"event": "verify", "code": code, "ok": good, "sample": str(got)[:200]})
    return "ok" if good else "fail"


async def main_async(a):
    cap0 = await HX.attach(a.port)
    if cap0 is None:
        print("没有可用页面")
        return 3
    # 头按店取：先读本店 mallid，再取 edit_headers_<mallid>.json（三店 mallid 不同）
    mallid = ""
    try:
        r = await cap0.send("Runtime.evaluate",
                            {"expression": "function c(n){var m=document.cookie.match(new RegExp('(^| )'+n+'=([^;]+)'));return m?m[2]:'';}c('mallid')",
                             "returnByValue": True}, timeout=15)
        mallid = str((r.get("result") or {}).get("value") or "")
    except Exception:
        pass
    hdr = api.load_headers(mallid)
    print("[头] %s mallid=%s anti=%d chars" % (a.store, mallid or "?", len(hdr.get("Anti-Content") or "")),
          flush=True)
    if not hdr.get("Anti-Content"):
        print("!! 该店没有 Anti-Content 头（_logs/edit_headers_%s.json）" % (mallid or "<mallid>"))
        try:
            await cap0.__aexit__(None, None, None)
        except Exception:
            pass
        return 3
    if a.refresh:
        print("[①] 刷新领星 + 帕沃尔库存…", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "goods" / "refresh_inventory.py")],
                       cwd=str(ROOT))
    snap = SNAP / ("%s.json" % a.date)
    if not snap.exists() or a.resnapshot:
        print("[②] 重建快照…", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "goods" / "daily_stock_snapshot.py"),
                        "--stores", a.store], cwd=str(ROOT))

    plan, zero = build_plan(a.store, a.date)
    (LOGS / ("%s_zero_confirm.json" % a.store.lower())).write_text(
        json.dumps(zero, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[③] 货号 %d 个可写 / 待确认清零 %d 条" % (len(plan), len(zero)), flush=True)

    cap = cap0          # 复用上面已经连上的页面（避免重复 attach）
    stat = {}
    try:
        for code in sorted(plan):
            r = await do_code(cap, hdr, code, plan[code], a.apply)
            stat[r] = stat.get(r, 0) + 1
            if r == "no-header":
                print("!! Anti-Content 失效")
                break
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    print("\n结果：", stat)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--store", default="BEL")
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--refresh", action="store_true", help="先刷新领星+帕沃尔")
    ap.add_argument("--resnapshot", action="store_true", help="重建当天快照")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())
