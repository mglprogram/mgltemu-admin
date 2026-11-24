# -*- coding: utf-8 -*-
"""一次性：接口回读某货号每个尺码的分仓库存 + 预售开关/补货日/在途预售量。"""
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import api_hx_stock as HX  # noqa: E402
import api_switch_mode as api  # noqa: E402

TZ = timezone(timedelta(hours=8))


def dump_presale(obj):
    """把预售相关字段全挖出来，不猜结构。"""
    found = []

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                p = path + "." + k
                if any(s in k for s in ("reSale", "resale", "deliveryDay", "Warehouse")):
                    if not isinstance(v, (dict, list)):
                        found.append((p, v))
                    elif isinstance(v, list) and all(not isinstance(i, (dict, list)) for i in v):
                        found.append((p, v))
                walk(v, p)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, "%s[%d]" % (path, i))

    walk(obj)
    return found


async def main(port, code):
    hdr = api.load_headers()
    cap = await HX.attach(port)
    if cap is None:
        print("no page")
        return 3
    try:
        try:
            await cap.send("Network.enable", {}, timeout=15)
        except Exception:
            pass
        q = await HX.call(cap, HX.URL_SKC, {"fuzzySkuExtCodes": [code], "page": 1, "pageSize": 20}, hdr)
        items = ((q.get("json") or {}).get("result") or {}).get("pageItems") or []
        if not items:
            print("skc empty", q.get("status"))
            return 1
        it = items[0]
        spu = int(it.get("productId"))
        sku_size = {}
        for s in it.get("productSkuSummaries") or []:
            ext = str(s.get("extCode") or "")
            sz = ext.rsplit("-", 1)[-1].upper() if "-" in ext else ""
            if s.get("productSkuId") and sz:
                sku_size[int(s["productSkuId"])] = (sz, ext)
        print("spu", spu, "skus", sku_size)
        r = await HX.call(cap, HX.URL_QUERY, {"productId": spu, "productSkuIdList": sorted(sku_size)}, hdr)
        js = r.get("json") or {}
        rows = []
        HX.walk_stock(js, rows)
        for x in rows:
            sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else ("?", "")
            print("  %-4s %-8s %-28s %s" % (sz[0], x["skuId"], x["wh"], x["stock"]))
        print("--- 预售字段 ---")
        for p, v in dump_presale(js):
            print(" ", p, "=", v)
        Path(ROOT / "_logs" / "readone.json").write_text(
            json.dumps(js, ensure_ascii=False)[:400000], encoding="utf-8")
        return 0
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(int(sys.argv[1]), sys.argv[2])))
