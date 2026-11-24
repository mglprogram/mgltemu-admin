# -*- coding: utf-8 -*-
"""主动调 `confirmShippingList` 看返回结构（只读），并试着找「执行确认」的写接口。

已抓到（用户点抽屉时）：
  POST /kirogi/bg/mms/confirmShippingList?is_back=1
  body: {"pageNumber":1,"pageSize":100,"parentOrderSnList":[],"onlyY2Package":false}

候选写接口（按命名规律猜，逐个试**只读式**探测：塞空数组看报什么错，
不真提交 —— 空数组不会确认任何单，安全）：
  /kirogi/bg/mms/confirmShipping
  /kirogi/bg/mms/batchConfirmShipping
  /kirogi/bg/mms/confirmShippingSubmit
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402

LIST_PATH = "/kirogi/bg/mms/confirmShippingList?is_back=1"
LIST_BODY = {"pageNumber": 1, "pageSize": 5, "parentOrderSnList": [],
             "onlyY2Package": False}


async def call(path, body):
    ws_url, _ = A._page_ws("BHY")
    hdr = A.load_headers("BHY") or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep["mallid"] = A.MALLID["BHY"]
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(path),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(await A._ev(ws_url, js) or "{}")
    try:
        return json.loads(out.get("body") or "{}")
    except Exception:  # noqa: BLE001
        return {"_raw": str(out.get("body"))[:300]}


async def main():
    print("=== ① confirmShippingList（只读）===")
    j = await call(LIST_PATH, LIST_BODY)
    print("success:", j.get("success"), "| code:", j.get("errorCode"))
    res = j.get("result") or {}
    print("result keys:", sorted(res.keys()))
    items = res.get("pageItems") or res.get("list") or []
    print("本页条数:", len(items), "| 总数:", res.get("totalItemNum"))
    if items:
        one = items[0]
        print("\n单条字段:", sorted(one.keys())[:20])
        for k in ("packageSn", "parentOrderSn", "trackingNumber", "waybillSn",
                  "shippingCompanyName", "orderSnList"):
            if k in one:
                print("   %-22s = %s" % (k, str(one[k])[:60]))
        print("\n样例:", json.dumps(one, ensure_ascii=False)[:400])

    print("\n=== ② 找写接口（空数组探测，不会确认任何单）===")
    for p in ("/kirogi/bg/mms/confirmShipping?is_back=1",
              "/kirogi/bg/mms/batchConfirmShipping?is_back=1",
              "/kirogi/bg/mms/confirmShippingSubmit?is_back=1"):
        r = await call(p, {"parentOrderSnList": [], "packageSnList": []})
        code = r.get("errorCode") or r.get("error_code")
        msg = str(r.get("errorMsg") or r.get("error_msg") or "")[:50]
        print("  %-48s code=%-8s %s" % (p.split("/")[-1][:46], code, msg))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
