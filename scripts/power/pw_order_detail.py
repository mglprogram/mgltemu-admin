# -*- coding: utf-8 -*-
"""按单号查订单详情（`/kirogi/bg/mms/orderDetail`）—— 看它的包裹 / 面单状态。

★ 2026-11-26 真机：`PO-211-07248603720310091`
    订单列表 status=2（待发货）
    confirmShippingList 查无包裹
    买面单却回「发货订单重复」
  ⇒ 用 orderDetail 看它的 `packageSnList` / `fulfillmentInfo`，
    判断是否已有包裹（只是没进待确认列表）。

接口： POST /kirogi/bg/mms/orderDetail
        body {"parentOrderSn": "<PO-...>"}
"""
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import asyncio  # noqa: E402

import temu_buylabel_api as A  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07248603720310091"
STORE = sys.argv[2] if len(sys.argv) > 2 else "KINGLAMANSPORT"


def main():
    ws_url, _u = A._page_ws(STORE)
    hdr = A.load_headers(STORE) or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k.lower() in
            ("mallid", "web-version", "content-type")}
    keep["mallid"] = A.mallid_of(STORE, ws_url)
    body = {"parentOrderSn": PO}
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps("/kirogi/bg/mms/orderDetail"),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(asyncio.run(A._ev(ws_url, js)) or "{}")
    print("status:", out.get("status"))
    raw = out.get("body") or ""
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001
        print("body:", raw[:400])
        return 1

    res = d.get("result") or {}
    print("success:", d.get("success"), "| errorCode:", d.get("errorCode"))

    # 找包裹相关字段
    txt = json.dumps(res, ensure_ascii=False)
    import re  # noqa: PLC0415
    pks = sorted(set(re.findall(r"PK-\d+", txt)))
    print("\n包裹号:", pks or "（无）")
    for k in ("packageSnList", "packageList", "fulfillmentInfo",
              "orderStatus", "status", "trackingNumber", "waybillNo"):
        if k in res:
            print("  %-20s %s" % (k, json.dumps(res[k], ensure_ascii=False)[:200]))

    # 子单
    olist = res.get("orderList") or []
    print("\n子单 %d 条：" % len(olist))
    for o in olist[:3]:
        print("  orderSn=%s status=%s track=%s"
              % (o.get("orderSn") or o.get("order_sn"),
                 o.get("orderStatus") or o.get("status"),
                 o.get("trackingNumber") or o.get("waybillNo") or "-"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
