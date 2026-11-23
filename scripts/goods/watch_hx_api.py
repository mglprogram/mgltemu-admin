# -*- coding: utf-8 -*-
"""常驻抓包 + 接口批量写：用户手动改一次 → 抓到的 Anti-Content 头拿来写同款其余尺码。

工作方式：
    1) 常驻监听商品列表页的 `updateMmsBtgProductSalesStock` 请求；
    2) 一旦抓到（说明用户刚手动改过一个尺码），立刻记下这次请求头里的 Anti-Content；
    3) 用这个头调 queryBtgProductStockInfo 读该 SPU 全部尺码当前库存；
    4) 按口径算目标：恒信 = max(0, ⌈领星可用×80%⌉)、加州3仓/kansas = 0；
    5) 差额 = 目标 − 当前，非 0 的批量 POST updateMmsBtgProductSalesStock；
    6) 回读 + 写台账。

用法：
    python scripts/goods/watch_hx_api.py --port 50748 --store BHY --seconds 7200
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
import api_hx_stock as HX  # noqa: E402
import cdp_net  # noqa: E402

SNAP = ROOT / "data" / "stock-snapshot"
LEDGER = ROOT / "_logs" / "watch_hx_api.jsonl"
HDR_CACHE = ROOT / "_logs" / "hx_anti_header.json"


def log(rec: dict):
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False)[:500], flush=True)


def load_snapshot(store: str, day: str) -> dict:
    """code → {"spu":..., "sizes": {尺码: 目标恒信量}}"""
    p = SNAP / ("%s.json" % day)
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = ((d.get("stores") or {}).get(HX.canon(store)) or {}).get("rows") or []
    out: dict = {}
    for r in rows:
        out.setdefault(r["code"], {"spu": r.get("spu") or "", "sizes": {}})
        out[r["code"]]["sizes"][str(r.get("size")).upper()] = int(r.get("hxHalf") or 0)
    return out


class Watcher(cdp_net.NetCapture):
    """抓 update 请求：拿 Anti-Content 头 + 该次 body（里面有 productId / sku / 仓 id）。"""

    TARGET = "updateMmsBtgProductSalesStock"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.got = None          # {anti, body}
        self.pending = []

    def _on_event(self, m):
        super()._on_event(m)
        method = m.get("method") or ""
        p = m.get("params") or {}
        if method == "Network.requestWillBeSent":
            req = p.get("request") or {}
            url = req.get("url") or ""
            if self.TARGET not in url:
                return
            hdrs = {k.lower(): v for k, v in (req.get("headers") or {}).items()}
            anti = hdrs.get("anti-content") or hdrs.get("anti_content") or ""
            rid = p.get("requestId")
            rec = {"url": url, "anti": anti, "body": req.get("postData") or ""}
            self.pending.append(rec)
            if not rec["body"]:
                asyncio.ensure_future(self._fill(rid, rec))
            self.got = rec
        elif method == "Network.responseReceived":
            url = (p.get("response") or {}).get("url") or ""
            if self.TARGET in url:
                log({"event": "resp", "status": (p.get("response") or {}).get("status")})

    async def _fill(self, rid, rec):
        try:
            r = await self.send("Network.getRequestPostData", {"requestId": rid}, timeout=10)
            rec["body"] = r.get("postData") or ""
        except Exception:
            pass


async def write_one(cap, rec: dict, snap: dict, store: str):
    anti = rec.get("anti") or ""
    if not anti:
        log({"event": "no-anti", "why": "这次请求没带 Anti-Content"})
        return False
    hdr = api.load_headers()
    hdr["Anti-Content"] = anti
    HDR_CACHE.write_text(json.dumps(hdr, ensure_ascii=False), encoding="utf-8")
    try:
        body = json.loads(rec.get("body") or "{}")
    except Exception:
        body = {}
    spu = body.get("productId")
    chg = (body.get("skuStockChangeList") or [{}])[0]
    sku = chg.get("productSkuId")
    log({"event": "caught", "spu": spu, "sku": sku, "anti": anti[:24] + "…"})
    if not spu:
        return False
    # 找这个 SPU 对应的货号 + 目标值
    code = None
    for c, v in snap.items():
        if str(v.get("spu")) == str(spu):
            code = c
            break
    if not code:
        log({"event": "no-code", "spu": spu, "why": "当天快照里没有这个 SPU"})
        return False
    sizes = snap[code]["sizes"]
    # 读当前库存（用刚抓到的头）
    skus = [int(x) for x in (body.get("skuStockChangeList") and [chg["productSkuId"]] or [])]
    q = await HX.post(cap, "https://agentseller.temu.com/visage-agent-seller/product/skc/pageQuery",
                      {"fuzzySkuExtCodes": [code], "page": 1, "pageSize": 20}, hdr)
    ids = set(skus)
    try:
        for it in ((q.get("json") or {}).get("result") or {}).get("pageItems") or []:
            for s in it.get("skuList") or it.get("skuInfoList") or []:
                v = s.get("productSkuId") or s.get("skuId")
                if v:
                    ids.add(int(v))
    except Exception:
        pass
    r = await HX.post(cap, HX.URL_QUERY, {"productId": int(spu), "productSkuIdList": sorted(ids)}, hdr)
    rows = []
    _walk(r.get("json") or {}, rows)
    log({"event": "read", "code": code, "spu": spu, "n": len(rows)})
    change, detail = build_changes(rows, sizes)
    log({"event": "plan", "code": code, "change": change[:8], "detail": detail[:8]})
    if not change:
        log({"event": "nothing"})
        return True
    # 预校验
    pre = await HX.post(cap, HX.URL_CHECK, {
        "productId": int(spu),
        "preCheckSkuWarehouseList": [{"productSkuId": c["productSkuId"],
                                      "warehouseId": c["warehouseId"]} for c in change]}, hdr)
    log({"event": "precheck", "status": pre.get("status"), "ok": bool((pre.get("json") or {}).get("success"))})
    up = await HX.post(cap, HX.URL_UPDATE, {
        "productId": int(spu), "skuStockChangeList": change,
        "skuTypeChangeList": [], "isCheckVersion": True, "source": 0}, hdr)
    ok = '"success":true' in str(up.get("body") or "") or bool((up.get("json") or {}).get("success"))
    log({"event": "write", "code": code, "ok": ok, "resp": str(up.get("body"))[:200]})
    await asyncio.sleep(3)
    r2 = await HX.post(cap, HX.URL_QUERY, {"productId": int(spu), "productSkuIdList": sorted(ids)}, hdr)
    rows2 = []
    _walk(r2.get("json") or {}, rows2)
    log({"event": "verify", "code": code, "in_hx": {x["size"]: x["stock"] for x in rows2 if "恒信" in x["wh"]}})
    return ok


def _walk(obj, out: list):
    """把 queryBtgProductStockInfo 返回里的 {skuId, warehouseName, stock} 拉平。"""
    if isinstance(obj, dict):
        wh = obj.get("warehouseName") or obj.get("warehouseDesc") or obj.get("warehouseId") or ""
        for key in ("stockNum", "availableStock", "stock", "stockAvailable"):
            if key in obj and isinstance(obj[key], (int, float, str)):
                try:
                    v = int(obj[key])
                except Exception:
                    v = 0
                out.append({"skuId": obj.get("productSkuId") or obj.get("skuId"),
                            "size": str(obj.get("size") or obj.get("sizeName") or ""),
                            "wh": str(wh), "stock": v})
                break
        for v in obj.values():
            _walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out)


def build_changes(rows: list, sizes: dict):
    """按口径算增量：恒信 → ⌈可用×80%⌉；加州3仓 / kansas → 0。"""
    changes, detail = [], []
    by_sku: dict = {}
    for r in rows:
        k = str(r.get("skuId"))
        if not k:
            continue
        by_sku.setdefault(k, {"size": r.get("size"), "hx": [], "zero": []})
        if "恒信" in r["wh"]:
            by_sku[k]["hx"].append(r)
        elif HX.is_zero_wh(r["wh"]):
            by_sku[k]["zero"].append(r)
    for sku, g in by_sku.items():
        sz = str(g["size"] or "").upper()
        if not sz or sz not in sizes:
            continue
        target = max(0, int(sizes[sz]))
        for r in g["hx"]:
            diff = target - int(r["stock"])
            if diff:
                changes.append({"productSkuId": int(sku), "stockDiff": diff,
                                "currentStockAvailable": int(r["stock"]),
                                "currentShippingMode": 1,
                                "warehouseId": r.get("whid") or ""})
                detail.append((sz, "恒信", int(r["stock"]), target, diff))
        for r in g["zero"]:
            cur = int(r["stock"])
            if cur:
                changes.append({"productSkuId": int(sku), "stockDiff": -cur,
                                "currentStockAvailable": cur,
                                "currentShippingMode": 1,
                                "warehouseId": r.get("whid") or ""})
                detail.append((sz, r["wh"], cur, 0, -cur))
    return changes, detail


async def main_async(a):
    snap = load_snapshot(a.store, a.date)
    print("快照货号 %d 个（%s）" % (len(snap), a.store), flush=True)
    end = time.time() + a.seconds
    while time.time() < end:
        pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
                 if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
        cap = None
        for p in pages:
            try:
                await asyncio.wait_for(
                    cdp_net._one_shot(p["webSocketDebuggerUrl"], "Runtime.evaluate",
                                      {"expression": "1", "returnByValue": True}), timeout=8)
                cap = await Watcher(p["webSocketDebuggerUrl"], match=()).__aenter__()
                break
            except Exception:
                continue
        if cap is None:
            print("没有可用列表页，10s 重试", flush=True)
            await asyncio.sleep(10)
            continue
        try:
            await cap.send("Network.enable", {"maxPostDataSize": 8 * 1024 * 1024}, timeout=20)
            print("已挂上，等你手动改一个尺码…", flush=True)
            seen = 0
            while time.time() < end:
                await asyncio.sleep(1.5)
                if cap.got is not None and len(cap.pending) > seen:
                    seen = len(cap.pending)
                    rec = cap.pending[-1]
                    await write_one(cap, rec, snap, a.store)
                    print("完成一组，继续等你下一手…", flush=True)
        except Exception as e:  # noqa: BLE001
            print("异常重连：%s" % str(e)[:120], flush=True)
            await asyncio.sleep(5)
        finally:
            try:
                await cap.__aexit__(None, None, None)
            except Exception:
                pass
    print("监听结束", flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--date", default=str(__import__("datetime").date.today()))
    ap.add_argument("--seconds", type=int, default=7200)
    a = ap.parse_args()
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())
