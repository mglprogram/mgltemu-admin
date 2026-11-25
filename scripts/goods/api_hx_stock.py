# -*- coding: utf-8 -*-
"""恒信仓库存：接口直调（读 → 算 ⌈可用×80%⌉ → 预校验 → 批量写 → 回读）。

前提：`_logs/edit_headers.json` 里要有**有效**的 Anti-Content（由 capture_simple.py 在
用户手动提交时自动落盘）。头会过期（403 No Permission），过期就再手动提交一次。

用法：
    python scripts/goods/api_hx_stock.py --port 50748 --store BHY --code BHY-203311-4SetA          # 只看
    python scripts/goods/api_hx_stock.py --port 50748 --store BHY --code BHY-203311-4SetA --apply  # 真写
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

import api_switch_mode as api  # noqa: E402
import cdp_net  # noqa: E402

BASE = "https://agentseller.temu.com/darwin-mms/api/kiana/ghost/btg/sales/stock/"
URL_QUERY = BASE + "queryBtgProductStockInfo"
URL_CHECK = BASE + "preCheckWarehouseInfo"
URL_UPDATE = BASE + "updateMmsBtgProductSalesStock"
URL_SKC = "https://agentseller.temu.com/visage-agent-seller/product/skc/pageQuery"

SNAP = ROOT / "data" / "stock-snapshot"
LEDGER = ROOT / "_logs" / "api_hx_stock.jsonl"
HX_RATIO = 0.8

JS = ('const U_=%s,B_=%s,H_=%s;return fetch(U_,{method:"POST",credentials:"include",'
      'headers:Object.assign({"content-type":"application/json"},H_),body:JSON.stringify(B_)})'
      '.then(function(r){return r.text().then(function(t){return JSON.stringify({s:r.status,b:t});});});')


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False)[:400], flush=True)


def canon(store: str) -> str:
    return "KinglamanSport" if str(store).upper() == "KLS" else store


def load_targets(store: str, day: str) -> dict:
    p = SNAP / ("%s.json" % day)
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = ((d.get("stores") or {}).get(canon(store)) or {}).get("rows") or []
    out: dict = {}
    for r in rows:
        out.setdefault(r["code"], {"spu": str(r.get("spu") or ""), "sizes": {}})
        out[r["code"]]["sizes"][str(r.get("size")).upper()] = int(r.get("hxHalf") or 0)
    return out


async def attach(port: int, timeout: float = 8.0):
    """只在**目标店自己的窗口**里取页面（Anti-Content 头与店/环境绑定，不能跨页串用）。

    ⚠ 2026-11-25：KLS 的页面刚打开/刚授权完时**首次 eval 很慢**（8s 必超时，
    实测要 15~20s 才返回），超时后 attach 返回 None，看着像「没有可用页」。
    需要时用 `timeout=` 放宽（read_onsale_api / fetch_presale_live_onsale 已传 25s）。
    """
    cands = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "agentseller.temu.com" in (x.get("url") or "")]
    # 优先商品列表页
    cands.sort(key=lambda x: 0 if "goods/list" in (x.get("url") or "") else 1)
    for p in cands:
        try:
            await asyncio.wait_for(cdp_net._one_shot(p["webSocketDebuggerUrl"], "Runtime.evaluate",
                                                     {"expression": "1", "returnByValue": True}),
                                   timeout=timeout)
            print("  用页：%s %s" % (p["id"][:8], (p.get("url") or "")[:50]), flush=True)
            return await cdp_net.NetCapture(p["webSocketDebuggerUrl"], match=()).__aenter__()
        except Exception:
            continue
    return None


async def call(cap, url: str, body: dict, hdr: dict) -> dict:
    r = await api.ev_async(cap, JS % (json.dumps(url), json.dumps(body), json.dumps(hdr)))
    out = {"status": r.get("s"), "body": r.get("b") or ""}
    try:
        out["json"] = json.loads(out["body"] or "{}")
    except Exception:
        out["json"] = {}
    return out


def walk_stock(obj, out, ctx=None):
    """把 queryBtgProductStockInfo 的返回拉平成 [{skuId, wh, whid, stock}]。

    真实结构（2026-11-23 实测）：
        result.productStockList[].productSkuId
          .warehouseStockList[].warehouseInfo{warehouseId, warehouseName}
                              .stockAvailable
    """
    res = (obj or {}).get("result") or {}
    for ps in res.get("productStockList") or []:
        sku = ps.get("productSkuId")
        for whs in ps.get("warehouseStockList") or []:
            info = whs.get("warehouseInfo") or {}
            v = whs.get("stockAvailable")
            if v is None:
                v = whs.get("stockNum") or 0
            try:
                stock = int(v)
            except Exception:
                stock = 0
            out.append({"skuId": sku, "wh": str(info.get("warehouseName") or ""),
                        "whid": str(info.get("warehouseId") or ""), "size": "", "stock": stock})


async def do_code(cap, hdr, code: str, sizes: dict, apply: bool):
    # 1) SKU 列表
    q = await call(cap, URL_SKC, {"fuzzySkuExtCodes": [code], "page": 1, "pageSize": 20}, hdr)
    if q.get("status") != 200:
        log({"event": "skc-fail", "code": code, "status": q.get("status"), "body": q["body"][:120]})
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
    if not sku_size:
        log({"event": "no-sku-size", "code": code})
        return "fail"
    # 2) 读当前库存
    r = await call(cap, URL_QUERY, {"productId": int(spu), "productSkuIdList": sorted(sku_size)}, hdr)
    rows = []
    walk_stock(r.get("json") or {}, rows)
    log({"event": "read", "code": code, "spu": spu, "rows": len(rows),
         "sample": [{k: x[k] for k in ("skuId", "wh", "stock")} for x in rows[:6]]})
    # 3) 算增量
    change, detail = [], []
    for x in rows:
        sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else None
        if not sz or sz not in sizes:
            continue
        wh = x["wh"]
        target = max(0, int(math.ceil(int(sizes[sz])))) if "恒信" in wh else 0
        if "恒信" not in wh and not (("加州三" in wh) or ("加州3仓" in wh)
                                     or ("kansas" in wh.lower()) or ("堪萨斯" in wh)):
            continue
        diff = target - int(x["stock"])
        if diff:
            change.append({"productSkuId": int(x["skuId"]), "stockDiff": diff,
                           "currentStockAvailable": int(x["stock"]),
                           "currentShippingMode": 1, "warehouseId": x["whid"]})
            detail.append((sz, wh, int(x["stock"]), target, diff))
    print("  计划：", detail, flush=True)
    if not change:
        log({"event": "nothing", "code": code})
        return "ok"
    if not apply:
        return "dry"
    # 4) 预校验 + 写
    pre = await call(cap, URL_CHECK, {"productId": int(spu), "preCheckSkuWarehouseList": [
        {"productSkuId": c["productSkuId"], "warehouseId": c["warehouseId"]} for c in change]}, hdr)
    log({"event": "precheck", "code": code, "status": pre.get("status"),
         "ok": bool((pre.get("json") or {}).get("success"))})
    up = await call(cap, URL_UPDATE, {"productId": int(spu), "skuStockChangeList": change,
                                      "skuTypeChangeList": [], "isCheckVersion": True,
                                      "source": 0}, hdr)
    ok = bool((up.get("json") or {}).get("success"))
    log({"event": "write", "code": code, "ok": ok, "resp": up["body"][:160]})
    await asyncio.sleep(3)
    r2 = await call(cap, URL_QUERY, {"productId": int(spu), "productSkuIdList": sorted(sku_size)}, hdr)
    rows2 = []
    walk_stock(r2.get("json") or {}, rows2)
    got = {}
    want = {}
    for x in rows2:
        sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else None
        if sz and "恒信" in x["wh"]:
            got[sz] = int(x["stock"])
    for sz in sizes:
        want[sz] = max(0, int(sizes[sz]))
    log({"event": "verify", "code": code, "got": got, "want": want,
         "match": all(got.get(k) == v for k, v in want.items() if k in got)})
    return "ok" if ok else "fail"


async def main_async(a):
    hdr = api.load_headers()
    if not hdr.get("Anti-Content"):
        print("!! 没有 Anti-Content 头")
        return 3
    tgt = load_targets(a.store, a.date)
    codes = [c.strip() for c in a.code.split(",") if c.strip()] or sorted(tgt)
    if a.code:
        codes = [c for c in codes if c in tgt] or codes
    cap = await attach(a.port)
    if cap is None:
        print("没有可用页面")
        return 3
    stat = {}
    try:
        for code in codes:
            if code not in tgt:
                print("  %s 不在当天快照" % code)
                continue
            print("\n== %s（SPU %s）目标 %s" % (code, tgt[code]["spu"],
                                              "，".join("%s→%d" % kv for kv in sorted(tgt[code]["sizes"].items()))))
            r = await do_code(cap, hdr, code, tgt[code]["sizes"], a.apply)
            stat[r] = stat.get(r, 0) + 1
            if r == "no-header":
                print("!! Anti-Content 失效，停止（请再手动提交一次以换新头）")
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
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--date", default=str(__import__("datetime").date.today()))
    ap.add_argument("--code", default="")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())
