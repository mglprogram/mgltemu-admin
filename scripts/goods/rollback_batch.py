# -*- coding: utf-8 -*-
"""按台账 detail 批量回滚某个时间窗内写入的货号（把 before 值写回去）。

2026-11-24：误跑全店（BEL 22 个货号）后用它精确还原。

用法：
    python scripts/goods/rollback_batch.py --port 63317 --store BEL \
        --since "2026-11-24 13:2" --exclude BEL-204605-3BHP        # 只看
    python scripts/goods/rollback_batch.py ... --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import api_hx_stock as HX  # noqa: E402
import api_switch_mode as api  # noqa: E402

LOGS = ROOT / "_logs"
LEDGER = LOGS / "api_hx_ca3.jsonl"


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOGS / "rollback_batch.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False)[:300], flush=True)


def pick(since: str, exclude: set, code_filter: str) -> dict:
    """取 since 之后的 write 记录 → {code: [(尺码, 仓, 原值)]}（同一仓取最早一次 before）。"""
    out: dict = {}
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("event") != "write" or not r.get("ok"):
            continue
        if str(r.get("ts") or "") < since:
            continue
        code = str(r.get("code") or "")
        if code in exclude:
            continue
        if code_filter and code_filter not in code:
            continue
        for d in r.get("detail") or []:
            out.setdefault(code, {})[(d[0], d[1])] = int(d[2])   # 保留最早的 before
    return out


async def main(a):
    tgt = pick(a.since, set(x for x in a.exclude.split(",") if x), a.code)
    if not tgt:
        print("该时间窗内没有可回滚的记录")
        return 0
    print("待回滚货号 %d 个：%s" % (len(tgt), ", ".join(sorted(tgt))))
    cap = await HX.attach(a.port)
    if cap is None:
        print("没有可用页面")
        return 3
    ok = bad = 0
    try:
        r = await cap.send("Runtime.evaluate", {
            "expression": ("(function(){var ps=document.cookie.split(';');"
                           "for(var i=0;i<ps.length;i++){var p=ps[i].trim();"
                           "if(p.indexOf('mallid=')===0){return p.substring(7);}}return '';})()"),
            "returnByValue": True}, timeout=15)
        mallid = str((r.get("result") or {}).get("value") or "")
        hdr = api.load_headers(mallid)
        if not hdr.get("Anti-Content"):
            print("没有请求头")
            return 3
        for code, want in sorted(tgt.items()):
            q = await HX.call(cap, HX.URL_SKC, {"fuzzySkuExtCodes": [code],
                                               "page": 1, "pageSize": 20}, hdr)
            items = ((q.get("json") or {}).get("result") or {}).get("pageItems") or []
            if not items:
                log({"event": "no-skc", "code": code})
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
            cur = await HX.call(cap, HX.URL_QUERY, {"productId": int(spu),
                                                     "productSkuIdList": sorted(sku_size)}, hdr)
            rows = []
            HX.walk_stock(cur.get("json") or {}, rows)
            idx = {}
            for x in rows:
                sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else None
                if sz:
                    idx[(sz, x["wh"])] = x
            change = []
            for (sz, wh), before in want.items():
                x = idx.get((sz.upper(), wh))
                if not x:
                    continue
                now = int(x["stock"])
                if now == before:
                    continue
                change.append({"productSkuId": int(x["skuId"]), "stockDiff": before - now,
                               "currentStockAvailable": now, "currentShippingMode": 1,
                               "warehouseId": x["whid"]})
            if not change:
                log({"event": "already", "code": code})
                ok += 1
                continue
            if not a.apply:
                log({"event": "would", "code": code, "n": len(change)})
                continue
            up = await HX.call(cap, HX.URL_UPDATE, {"productId": int(spu),
                                                     "skuStockChangeList": change,
                                                     "skuTypeChangeList": [],
                                                     "isCheckVersion": True, "source": 0}, hdr)
            good = bool((up.get("json") or {}).get("success"))
            log({"event": "rollback", "code": code, "ok": good, "n": len(change)})
            ok += 1 if good else 0
            bad += 0 if good else 1
            await asyncio.sleep(0.4)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    print("\n回滚完成 ok=%d bad=%d（%s）" % (ok, bad, "已写" if a.apply else "dry-run"))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--store", default="BEL")
    ap.add_argument("--since", required=True, help="只回滚 ts >= 这个字符串的记录")
    ap.add_argument("--exclude", default="", help="逗号分隔，排除的货号")
    ap.add_argument("--code", default="", help="只处理包含该子串的货号")
    ap.add_argument("--apply", action="store_true")
    x = ap.parse_args()
    raise SystemExit(asyncio.run(main(x)))
