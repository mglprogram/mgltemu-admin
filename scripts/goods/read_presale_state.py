# -*- coding: utf-8 -*-
"""批量只读：某店若干货号的分仓库存 + 预售状态（不写任何东西）。"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import api_hx_stock as HX  # noqa: E402
import api_switch_mode as api  # noqa: E402


def side(nm: str) -> str:
    if "恒信" in nm:
        return "hx"
    if ("加州三" in nm) or ("加州3仓" in nm):
        return "ca3"
    if ("kansas" in nm.lower()) or ("堪萨斯" in nm):
        return "ks"
    return "?"


async def main(port, codes):
    hdr = api.load_headers()
    cap = await HX.attach(port)
    if cap is None:
        print("no page")
        return 3
    out = {}
    try:
        try:
            await cap.send("Network.enable", {}, timeout=15)
        except Exception:
            pass
        for code in codes:
            q = await HX.call(cap, HX.URL_SKC, {"fuzzySkuExtCodes": [code], "page": 1,
                                                "pageSize": 20}, hdr)
            items = ((q.get("json") or {}).get("result") or {}).get("pageItems") or []
            if not items:
                print("%-20s SKC 查不到 (%s)" % (code, q.get("status")))
                continue
            it = items[0]
            spu = int(it.get("productId"))
            sku_size = {}
            for s in it.get("productSkuSummaries") or []:
                ext = str(s.get("extCode") or "")
                sz = ext.rsplit("-", 1)[-1].upper() if "-" in ext else ""
                if s.get("productSkuId") and sz:
                    sku_size[int(s["productSkuId"])] = sz
            r = await HX.call(cap, HX.URL_QUERY, {"productId": spu,
                                                 "productSkuIdList": sorted(sku_size)}, hdr)
            js = r.get("json") or {}
            if r.get("status") != 200:
                print("%-20s 库存查询失败 %s" % (code, r.get("status")))
                continue
            rows = []
            HX.walk_stock(js, rows)
            st = {}
            for x in rows:
                sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else ""
                st.setdefault(sz, {})[side(x["wh"])] = int(x["stock"])
            pre = {}
            for ps in ((js.get("result") or {}).get("productStockList") or []):
                sz = sku_size.get(int(ps.get("productSkuId") or 0), "")
                e = {"on": bool(ps.get("enablePreSale")), "day": ps.get("preSaleStockDeliveryDay")}
                for whs in ps.get("warehouseStockList") or []:
                    nm = str((whs.get("warehouseInfo") or {}).get("warehouseName") or "")
                    e[side(nm)] = whs.get("inTransitPreSaleStock")
                pre[sz] = e
            print("== %s spu=%d" % (code, spu))
            for sz in ("S", "M", "L", "XL", "XXL"):
                if sz not in st:
                    continue
                s, p = st[sz], pre.get(sz, {})
                print("   %-4s 库存 hx=%-5s ca3=%-5s ks=%-5s | 预售开=%-5s hx=%-5s ca3=%-5s day=%s"
                      % (sz, s.get("hx", 0), s.get("ca3", 0), s.get("ks", 0),
                         p.get("on"), p.get("hx"), p.get("ca3"), p.get("day")))
                out["%s-%s" % (code, sz)] = {"stock": s, "pre": p, "spu": spu}
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    (ROOT / "_logs" / "bhy_presale_state.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("写 _logs/bhy_presale_state.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(int(sys.argv[1]), sys.argv[2].split(","))))
