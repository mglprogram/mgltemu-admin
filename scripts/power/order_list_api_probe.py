# -*- coding: utf-8 -*-
"""重放 `recentOrderList`（只读分页查询），把响应体完整取回并分析字段。

目的：确认「订单列表接口」返回里有没有 `skuId / goodsId / orderSn`，
决定能不能靠它批量取 goods 明细（那样买面单就不需要人工点在线下单页）。
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402

PATH = "/kirogi/bg/mms/recentOrderList?is_back=1"
BODY = {"fulfillmentMode": 0, "pageNumber": 1, "pageSize": 10, "queryType": 2,
        "sortType": 1, "timeZone": "UTC+8", "parentAfterSalesTag": 0,
        "needBuySignService": 0, "sellerNoteLabelList": []}


async def main():
    ws_url, _u = A._page_ws("BHY")
    hdr = A.load_headers("BHY") or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep.setdefault("mallid", A.MALLID["BHY"])
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(PATH),
                               json.dumps(BODY, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    # ⚠ 已在事件循环里，必须 await `_ev`（不能再 asyncio.run）
    out = json.loads(await A._ev(ws_url, js) or "{}")
    print("HTTP:", out.get("status"))
    txt = out.get("body") or out.get("error") or ""
    try:
        j = json.loads(txt)
    except Exception:  # noqa: BLE001
        print("非 JSON：", txt[:600])
        return 1

    print("success:", j.get("success"), "| code:", j.get("error_code"),
          "|", str(j.get("error_msg") or "")[:80])
    res = j.get("result") or {}
    print("result keys:", sorted(res.keys())[:20])
    lst = res.get("pageData") or res.get("list") or res.get("orders") or []
    print("单数:", len(lst))
    if lst:
        one = lst[0]
        print("\n单条 key:", sorted(one.keys()))
        s = json.dumps(one, ensure_ascii=False)
        for k in ("goodsId", "skuId", "orderSn", "parentOrderSn", "skcId"):
            print("  含 %-14s: %s" % (k, k in s))
        print("\n样例(前 1500):", s[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
