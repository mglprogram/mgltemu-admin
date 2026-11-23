# -*- coding: utf-8 -*-
"""展开 `recentOrderList` 的 `pageItems`，确认里面有没有 skuId / goodsId / orderSn。

`result.pageItems[].parentOrderMap` 是父单；商品行应该在其下的 list 里
（常见键：orderList / childOrderList / orderInfoList），这里递归找。
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402

PATH = "/kirogi/bg/mms/recentOrderList?is_back=1"
BODY = {"fulfillmentMode": 0, "pageNumber": 1, "pageSize": 3, "queryType": 2,
        "sortType": 1, "timeZone": "UTC+8", "parentAfterSalesTag": 0,
        "needBuySignService": 0, "sellerNoteLabelList": []}


def find_goods(obj, out, depth=0):
    """递归找带 skuId / goodsId 的对象。"""
    if depth > 8 or not isinstance(obj, (dict, list)):
        return
    if isinstance(obj, list):
        for x in obj[:50]:
            find_goods(x, out, depth + 1)
        return
    keys = set(obj.keys())
    if {"skuId"} & keys or {"goodsId"} & keys:
        out.append({k: obj.get(k) for k in
                    ("orderSn", "parentOrderSn", "goodsId", "skuId", "skuCode",
                     "quantity", "skuQuantity", "productName", "goodsName") if k in obj})
    for v in obj.values():
        find_goods(v, out, depth + 1)


async def main():
    ws_url, _u = A._page_ws("BHY")
    hdr = A.load_headers("BHY") or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep.setdefault("mallid", A.MALLID["BHY"])
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(PATH),
                               json.dumps(BODY, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(await A._ev(ws_url, js) or "{}")
    txt = out.get("body") or ""
    j = json.loads(txt)
    res = j.get("result") or {}
    items = res.get("pageItems") or []
    print("总单数:", res.get("totalItemNum"), "| 本页:", len(items))
    if items:
        pm = items[0].get("parentOrderMap") or {}
        print("\nparentOrderMap 键:", sorted(pm.keys()))
        print("父单号:", pm.get("parentOrderSn"), "| region:", pm.get("regionId1"))
    goods = []
    find_goods(items, goods)
    print("\n找到带 skuId/goodsId 的行:", len(goods))
    for g in goods[:8]:
        print("  ", json.dumps(g, ensure_ascii=False))
    if not goods:
        s = json.dumps(items, ensure_ascii=False)
        for k in ("skuId", "goodsId", "orderSn", "skuCode"):
            print("  含 %-10s: %s" % (k, k in s))
        print("\n前 1200 字：", s[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
