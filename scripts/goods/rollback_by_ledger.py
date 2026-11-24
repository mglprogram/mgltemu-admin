# -*- coding: utf-8 -*-
"""按台账 detail 精确回滚某货号的库存到「改动前」值（针对指定时间点的一条写入记录）。

用户 2026-11-24：「104102 今天 6:37 那次的三条，回滚」。

做法：从 _logs/api_hx_ca3.jsonl 里取 `--at` 指定的那条 write 记录，
     把 detail 里的 [尺码, 仓, 改动前, 目标, 差额] 反算一遍：目标 = 改动前，
     差额 = 改动前 − 当前（当前实时读），走同一个接口写回并回读。

用法：
    python scripts/goods/rollback_by_ledger.py --port 63317 --store BEL \
        --code BEL-104102-1B --at "2026-11-24 06:37:01"           # 只看
    python scripts/goods/rollback_by_ledger.py --port 63317 --store BEL \
        --code BEL-104102-1B --at "2026-11-24 06:37:01" --apply   # 真回滚
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import api_hx_stock as HX  # noqa: E402
import api_switch_mode as api  # noqa: E402

LOGS = ROOT / "_logs"


def load_snapshot(code: str, at: str) -> list:
    """从台账取那条 write 记录，返回 [(尺码, 仓, 原值, 现值)]（原值 = detail 的 before）。"""
    p = LOGS / "api_hx_ca3.jsonl"
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("event") != "write" or str(r.get("code")) != code:
            continue
        if at not in str(r.get("ts") or ""):
            continue
        for d in r.get("detail") or []:
            out.append((d[0], d[1], int(d[2]), int(d[3])))
    return out


async def main(a):
    want = load_snapshot(a.code, a.at)
    if not want:
        print("台账里没有找到这条记录：%s @ %s" % (a.code, a.at))
        return 3
    print("要回滚的原始值：")
    for sz, wh, before, after in want:
        print("   %-4s %-12s %s → 回滚到 %s" % (sz, wh, after, before))

    cap = await HX.attach(a.port)
    if cap is None:
        print("没有可用页面")
        return 3
    try:
        mallid = ""
        r = await cap.send("Runtime.evaluate", {
            "expression": ("(function(){var parts=document.cookie.split(';');"
                           "for(var i=0;i<parts.length;i++){var p=parts[i].trim();"
                           "if(p.indexOf('mallid=')===0){return p.substring(7);}}return '';})()"),
            "returnByValue": True}, timeout=15)
        mallid = str((r.get("result") or {}).get("value") or "")
        hdr = api.load_headers(mallid)
        print("   mallid=%s anti=%d" % (mallid or "?", len(hdr.get("Anti-Content") or "")))
        if not hdr.get("Anti-Content"):
            print("!! 没有有效请求头")
            return 3

        q = await HX.call(cap, HX.URL_SKC, {"fuzzySkuExtCodes": [a.code], "page": 1, "pageSize": 20}, hdr)
        items = ((q.get("json") or {}).get("result") or {}).get("pageItems") or []
        if not items:
            print("查不到该货号")
            return 3
        it = items[0]
        spu = str(it.get("productId"))
        sku_size = {}
        for s in it.get("productSkuSummaries") or []:
            ext = str(s.get("extCode") or "")
            sz = ext.rsplit("-", 1)[-1].upper() if "-" in ext else ""
            if s.get("productSkuId") and sz:
                sku_size[int(s["productSkuId"])] = sz
        cur = await HX.call(cap, HX.URL_QUERY, {"productId": int(spu),
                                                 "productSkuIdList": sorted(sku_size)}, hdr)
        rows = []
        HX.walk_stock(cur.get("json") or {}, rows)
        idx = {}
        for x in rows:
            sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else None
            if sz:
                idx[(sz, x["wh"])] = x
        change, detail = [], []
        for sz, wh, before, _after in want:
            x = idx.get((sz.upper(), wh))
            if not x:
                print("   %-4s %-12s ✗ 实时数据里没找到这一行" % (sz, wh))
                continue
            now = int(x["stock"])
            diff = before - now
            print("   %-4s %-12s 当前 %s → 目标 %s（差额 %+d）" % (sz, wh, now, before, diff))
            if diff:
                change.append({"productSkuId": int(x["skuId"]), "stockDiff": diff,
                               "currentStockAvailable": now,
                               "currentShippingMode": 1, "warehouseId": x["whid"]})
                detail.append((sz, wh, now, before, diff))
        if not change:
            print("已经等于原值，无需回滚")
            return 0
        if not a.apply:
            print("（dry-run：加 --apply 才写）")
            return 0
        pre = await HX.call(cap, HX.URL_CHECK, {"productId": int(spu), "preCheckSkuWarehouseList": [
            {"productSkuId": c["productSkuId"], "warehouseId": c["warehouseId"]} for c in change]}, hdr)
        up = await HX.call(cap, HX.URL_UPDATE, {"productId": int(spu), "skuStockChangeList": change,
                                                "skuTypeChangeList": [], "isCheckVersion": True,
                                                "source": 0}, hdr)
        ok = bool((up.get("json") or {}).get("success"))
        print("precheck=%s 写入=%s" % (bool((pre.get("json") or {}).get("success")), ok))
        await asyncio.sleep(3)
        back = await HX.call(cap, HX.URL_QUERY, {"productId": int(spu),
                                                 "productSkuIdList": sorted(sku_size)}, hdr)
        rows2 = []
        HX.walk_stock(back.get("json") or {}, rows2)
        got = {}
        for x in rows2:
            sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else None
            if sz:
                got[(sz, x["wh"])] = int(x["stock"])
        print("回读：")
        allok = True
        for sz, wh, before, _a in want:
            v = got.get((sz.upper(), wh))
            good = (v == before)
            allok = allok and good
            print("   %-4s %-12s %s %s" % (sz, wh, v, "✅" if good else "❌ 期望 %s" % before))
        with open(LOGS / "rollback_ledger.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"code": a.code, "at": a.at, "ok": allok,
                                "detail": detail}, ensure_ascii=False) + "\n")
        return 0 if allok else 5
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--store", default="BEL")
    ap.add_argument("--code", required=True)
    ap.add_argument("--at", required=True, help="台账时间点，匹配 ts 子串，如 '2026-11-24 06:37'")
    ap.add_argument("--apply", action="store_true")
    x = ap.parse_args()
    raise SystemExit(asyncio.run(main(x)))
