# -*- coding: utf-8 -*-
"""只恢复/更新**恒信仓**以外被误清零的仓（加州三仓 / Kansas）到原始数量。

背景（2026-11-23）：
    · 上午那轮按用户当时口径「加州三仓 / Kansas 清零」，用接口把 66 个货号的这些仓写了 0；
    · 用户随后改规则：**以后只更新恒信仓的数值，其他仓一律不动**；
    · 本脚本把已清零的仓按 `_logs/restore_zero_wh.json`（从运行日志的「计划」行还原）写回原数。

写接口同恒信：updateMmsBtgProductSalesStock（stockDiff = 目标 − 当前）。
注意：需要有效的 `_logs/edit_headers.json`（Anti-Content）。

用法：
    python scripts/goods/restore_other_wh.py --port 50748 --dry-run
    python scripts/goods/restore_zero_wh.py --port 50748 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import api_hx_stock as HX  # noqa: E402
import api_switch_mode as api  # noqa: E402

PLAN = ROOT / "_logs" / "restore_zero_wh.json"
LEDGER = ROOT / "_logs" / "restore_other_wh.jsonl"


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False)[:400], flush=True)


async def main_async(a):
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    hdr = api.load_headers()
    if not hdr.get("Anti-Content"):
        print("!! 没有 Anti-Content 头")
        return 3
    cap = await HX.attach(a.port)
    if cap is None:
        print("没有可用页面")
        return 3
    ok = bad = 0
    try:
        for code, whmap in plan.items():
            q = await HX.call(cap, HX.URL_SKC, {"fuzzySkuExtCodes": [code], "page": 1, "pageSize": 20}, hdr)
            if q.get("status") != 200:
                log({"event": "skc-fail", "code": code, "status": q.get("status")})
                bad += 1
                continue
            items = ((q.get("json") or {}).get("result") or {}).get("pageItems") or []
            if not items:
                log({"event": "skc-empty", "code": code})
                bad += 1
                continue
            it = items[0]
            spu = str(it.get("productId"))
            sku_size = {}
            for s in it.get("productSkuSummaries") or []:
                ext = str(s.get("extCode") or "")
                sz = ext.rsplit("-", 1)[-1].upper() if "-" in ext else ""
                if s.get("productSkuId") and sz:
                    sku_size[int(s["productSkuId"])] = sz
            r = await HX.call(cap, HX.URL_QUERY,
                              {"productId": int(spu), "productSkuIdList": sorted(sku_size)}, hdr)
            rows = []
            HX.walk_stock(r.get("json") or {}, rows)
            change, detail = [], []
            for x in rows:
                sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else None
                if not sz:
                    continue
                wh = x.get("wh") or ""
                want = None
                for wname, m in whmap.items():
                    if wname == wh or (wname.replace("三", "3") == wh.replace("三", "3")):
                        want = m.get(sz)
                        break
                if want is None:
                    continue
                diff = int(want) - int(x["stock"])
                if diff:
                    change.append({"productSkuId": int(x["skuId"]), "stockDiff": diff,
                                   "currentStockAvailable": int(x["stock"]),
                                   "currentShippingMode": 1, "warehouseId": x["whid"]})
                    detail.append((sz, wh, int(x["stock"]), int(want), diff))
            print("  %s 计划 %s" % (code, detail), flush=True)
            if not change:
                log({"event": "nothing", "code": code})
                ok += 1
                continue
            if not a.apply:
                continue
            up = await HX.call(cap, HX.URL_UPDATE,
                               {"productId": int(spu), "skuStockChangeList": change,
                                "skuTypeChangeList": [], "isCheckVersion": True, "source": 0}, hdr)
            good = bool((up.get("json") or {}).get("success"))
            log({"event": "write", "code": code, "ok": good, "n": len(change),
                 "resp": str(up.get("body"))[:120]})
            ok += 1 if good else 0
            bad += 0 if good else 1
            await asyncio.sleep(0.5)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    print("\n完成 ok=%d bad=%d（%s）" % (ok, bad, "已写" if a.apply else "dry-run"))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())
