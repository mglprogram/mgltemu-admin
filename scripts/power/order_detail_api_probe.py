# -*- coding: utf-8 -*-
"""探测订单详情接口是否带 SKU 货号（同步版，避免事件循环嵌套）。"""
import json
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import asyncio  # noqa: E402

import temu_buylabel_api as A  # noqa: E402

PO = "PO-211-12556758467193266"
SKU_ID = "106276818273918"

CANDIDATES = [
    ("/mms/eagle/order/orderInfo", {"parentOrderSnList": [PO]}),
    ("/kirogi/bg/mms/orderInfo", {"parentOrderSnList": [PO]}),
    ("/kirogi/bg/mms/orderDetail", {"parentOrderSn": PO}),
    ("/mms/orchid/order/detail", {"parentOrderSn": PO}),
]


def call(path, body):
    hdr = A.load_headers("BHY") or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep["mallid"] = A.MALLID["BHY"]
    ws_url, _ = A._page_ws("BHY")
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(path),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(asyncio.run(A._ev(ws_url, js)) or "{}")
    txt = str(out.get("body") or "")
    try:
        return out.get("status"), json.loads(txt)
    except Exception:  # noqa: BLE001
        return out.get("status"), {"_raw": txt[:160]}


for path, body in CANDIDATES:
    st, j = call(path, body)
    code = j.get("errorCode") or j.get("error_code")
    txt = json.dumps(j, ensure_ascii=False)
    has = bool(re.search(r'[Ss]ku[a-zA-Z]*(Code|Sn|No)', txt)) or ("BH-" in txt)
    print("%-42s http=%-4s code=%-8s 长度=%-6d 含货号字段=%s"
          % (path[:40], st, code, len(txt), has))
    if has:
        m = re.search(r'"[a-zA-Z]*(?:sku|Sku)[a-zA-Z]*(?:Code|Sn|No)"\s*:\s*"([^"]{3,40})"', txt)
        print("   命中样例:", m.group(1) if m else "(仅命中 BH-)")
        print("   片段:", txt[:300])
