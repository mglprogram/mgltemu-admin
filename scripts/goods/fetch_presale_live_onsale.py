# -*- coding: utf-8 -*-
"""只抓「在售中」货号的清零/预售状态 → 写 data/presale-live.json（按店合并，不覆盖他店）。

用户 2026-11-24：「只抓取在售中的就好了」——不要翻页抓全部 SKC（191 条要翻 10 页、
慢且会被 ⚠ 字符的 GBK 编码搞挂）。改走接口：
    ① data/store-db/<STORE>.onsale.txt   ← read_onsale_api.py 产的「在售中」货号清单
    ② 对每个在售中货号调 skc/pageQuery 拿 productId + 各尺码 skuId
    ③ queryBtgProductStockInfo 拿分仓库存 + enablePreSale / inTransitPreSaleStock / deliveryDay

产物 data/presale-live.json 结构保持 fetch_presale_live.py 的口径（下游
build_presale_watch.py 直接用），**只替换本店的 stores[<STORE>] 段**，别店原样保留。

用法：
    python scripts/goods/fetch_presale_live_onsale.py --port 50748 --store BHY
    python scripts/goods/fetch_presale_live_onsale.py --port 50748 --store BHY --only BHY-202504-5SetA
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

LIVE = ROOT / "data" / "presale-live.json"
DB = ROOT / "data" / "store-db"
CANON = {"KLS": "KinglamanSport"}


def side(nm: str) -> str:
    if "恒信" in nm:
        return "hx"
    if ("加州三" in nm) or ("加州3仓" in nm):
        return "ca3"
    if ("kansas" in nm.lower()) or ("堪萨斯" in nm):
        return "ks"
    return ""


def onsale_codes(store: str) -> list:
    """读在售中清单。文件名有三种写法（KLS / KinglamanSport / BH…），都试一遍。"""
    names = [store]
    for k, v in CANON.items():                      # KLS ↔ KinglamanSport
        if v == store:
            names.append(k)
        elif k == store:
            names.append(v)
    names += ["KinglamanSport", "KLS"]
    for nm in names:
        p = DB / ("%s.onsale.txt" % nm)
        if p.exists():
            return [x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return []


async def fetch_one(cap, hdr, code: str) -> list:
    q = await HX.call(cap, HX.URL_SKC, {"fuzzySkuExtCodes": [code], "page": 1, "pageSize": 20}, hdr)
    if q.get("status") != 200:
        return []
    items = ((q.get("json") or {}).get("result") or {}).get("pageItems") or []
    rows = []
    for it in items:
        # 只认本店这一条（模糊查询可能带出同前缀的兄弟货号）
        if str(it.get("extCode") or "").upper() != code.upper():
            continue
        spu = int(it.get("productId"))
        sku_size = {}
        for s in it.get("productSkuSummaries") or []:
            ext = str(s.get("extCode") or "")
            sz = ext.rsplit("-", 1)[-1].upper() if "-" in ext else ""
            if s.get("productSkuId") and sz:
                sku_size[int(s["productSkuId"])] = sz
        if not sku_size:
            continue
        r = await HX.call(cap, HX.URL_QUERY, {"productId": spu,
                                             "productSkuIdList": sorted(sku_size)}, hdr)
        js = r.get("json") or {}
        if r.get("status") != 200:
            continue
        stock = {}
        flat = []
        HX.walk_stock(js, flat)
        for x in flat:
            sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else ""
            if sz:
                stock.setdefault(sz, {})[side(x["wh"])] = int(x["stock"])
        pre = {}
        for ps in ((js.get("result") or {}).get("productStockList") or []):
            sz = sku_size.get(int(ps.get("productSkuId") or 0), "")
            if not sz:
                continue
            e = {"on": bool(ps.get("enablePreSale")),
                 "day": ps.get("preSaleStockDeliveryDay"),
                 "hx": None, "ca3": None, "ks": None}
            for whs in ps.get("warehouseStockList") or []:
                nm = str((whs.get("warehouseInfo") or {}).get("warehouseName") or "")
                s = side(nm)
                if s:
                    e[s] = whs.get("inTransitPreSaleStock")
            pre[sz] = e
        for sz in sorted(stock, key=lambda z: ["S", "M", "L", "XL", "XXL"].index(z)
                         if z in ("S", "M", "L", "XL", "XXL") else 99):
            st = stock[sz]
            p = pre.get(sz) or {}
            bywh = {"hx": p.get("hx"), "ca3": p.get("ca3")}
            rows.append({
                "code": code, "size": sz, "spu": str(spu),
                "temuStock": sum(v for k, v in st.items() if k),
                "stockByWh": {"hx": st.get("hx", 0), "ca3": st.get("ca3", 0),
                              "ks": st.get("ks", 0)},
                "presaleOn": bool(p.get("on")),
                # ⚠ build_presale_watch 要的是**单个数字**（int() 强转），分仓明细放 presaleByWh
                "presaleQty": sum(v for v in bywh.values() if isinstance(v, int)),
                "presaleByWh": bywh,
                "presaleDate": p.get("day"),
            })
    return rows


async def main(a):
    store = CANON.get(a.store.upper(), a.store)
    codes = [x for x in a.only.split(",") if x.strip()] if a.only else onsale_codes(store)
    if not codes:
        print("!! %s 没有在售中清单（先跑 read_onsale_api.py）" % store)
        return 4
    print("%s：在售中 %d 个货号" % (store, len(codes)), flush=True)
    cap = await HX.attach(a.port)
    if cap is None:
        print("没有可用页面")
        return 3
    mallid = ""
    try:
        r = await cap.send("Runtime.evaluate", {
            "expression": ("(function(){var parts=document.cookie.split(';');"
                           "for(var i=0;i<parts.length;i++){var p=parts[i].trim();"
                           "if(p.indexOf('mallid=')===0){return p.substring(7);}}return '';})()"),
            "returnByValue": True}, timeout=15)
        mallid = str((r.get("result") or {}).get("value") or "")
    except Exception:
        pass
    hdr = api.load_headers(mallid)
    print("  mallid=%s anti=%d chars" % (mallid or "?", len(hdr.get("Anti-Content") or "")), flush=True)
    rows = []
    try:
        try:
            await cap.send("Network.enable", {}, timeout=15)
        except Exception:
            pass
        for i, code in enumerate(codes, 1):
            got = await fetch_one(cap, hdr, code)
            rows += got
            if i % 10 == 0 or i == len(codes):
                print("  %d/%d …累计 %d 行" % (i, len(codes), len(rows)), flush=True)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    # ⚠ 抓到 0 行不覆盖（同 read_onsale_api 的原则）
    if not rows:
        print("!! 抓到 0 行 → 保留原 presale-live.json 不动")
        return 4
    d = {}
    if LIVE.exists():
        try:
            d = json.loads(LIVE.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    stores = d.setdefault("stores", {})
    old = stores.get(store) or {}
    stores[store] = {"rows": rows,
                     "source": "后台实读（只在售中）",
                     "onsaleCodes": len(codes)}
    d["generatedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    d["source"] = "fetch_presale_live_onsale.py（接口直读，仅 skcStatus=11 & skcSiteStatus=1）"
    LIVE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    pre = sum(1 for r in rows if r["presaleOn"])
    zero = sum(1 for r in rows if not r["presaleOn"] and r["temuStock"] == 0)
    print("%s：%d 行（预售中 %d / 已清零 %d），原 %d 行 → 已写 data/presale-live.json"
          % (store, len(rows), pre, zero, len(old.get("rows") or [])), flush=True)
    return 0


def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--only", default="", help="只跑这些货号（逗号分隔）")
    x = ap.parse_args()
    raise SystemExit(asyncio.run(main(x)))


if __name__ == "__main__":
    main_cli()
