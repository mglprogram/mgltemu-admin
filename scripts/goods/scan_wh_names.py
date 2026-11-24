"""采集三店每个货号实际出现的仓名，汇总成清单。"""
import asyncio
import json
import sys
import collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import api_hx_stock as HX  # noqa: E402
import api_switch_mode as api  # noqa: E402

DB = ROOT / "data" / "store-db"


async def scan(port, store, codes):
    cap = await HX.attach(port)
    if cap is None:
        return {}
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
    names = collections.Counter()
    ids = {}
    try:
        try:
            await cap.send("Network.enable", {}, timeout=15)
        except Exception:
            pass
        for i, code in enumerate(codes, 1):
            q = await HX.call(cap, HX.URL_SKC, {"fuzzySkuExtCodes": [code], "page": 1,
                                               "pageSize": 20}, hdr)
            items = ((q.get("json") or {}).get("result") or {}).get("pageItems") or []
            for it in items:
                if str(it.get("extCode") or "").upper() != code.upper():
                    continue
                spu = int(it.get("productId"))
                skus = [int(s["productSkuId"]) for s in (it.get("productSkuSummaries") or [])
                        if s.get("productSkuId")]
                if not skus:
                    continue
                r = await HX.call(cap, HX.URL_QUERY, {"productId": spu,
                                                     "productSkuIdList": skus}, hdr)
                rows = []
                HX.walk_stock(r.get("json") or {}, rows)
                for x in rows:
                    nm = (x.get("wh") or "").strip()
                    if nm:
                        names[nm] += 1
                        ids.setdefault(nm, x.get("whid"))
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    return {"store": store, "mallid": mallid, "names": dict(names), "ids": ids}


async def main():
    jobs = [("BEL", 63317), ("BHY", 50748)]
    out = {}
    for store, port in jobs:
        p = DB / ("%s.onsale.txt" % store)
        codes = [x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()][:40]
        print("扫描 %s（%d 个货号样本）…" % (store, len(codes)), flush=True)
        out[store] = await scan(port, store, codes)
    json.dump(out, open(ROOT / "_logs" / "wh-names.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    for st, d in out.items():
        print("\n=== %s (mallid=%s) ===" % (st, d.get("mallid")))
        for nm, n in sorted(d["names"].items(), key=lambda x: -x[1]):
            print("   %-16s 出现 %-4d  id=%s" % (nm, n, d["ids"].get(nm)))


if __name__ == "__main__":
    asyncio.run(main())
