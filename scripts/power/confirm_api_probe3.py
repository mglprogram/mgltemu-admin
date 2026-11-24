# -*- coding: utf-8 -*-
"""再试一批「确认发货」写接口候选路径（空数组探测，不会确认任何单）。

已知：`/kirogi/bg/mms/confirmShippingList`（读，通）
已试失败：confirmShipping / batchConfirmShipping / confirmShippingSubmit → 405

405 = 路由存在但方法不对，或该前缀被网关拦；这里继续按命名规律试。
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402

CANDIDATES = [
    "/kirogi/bg/mms/confirmShippingBatch?is_back=1",
    "/kirogi/bg/mms/confirmShippingSave?is_back=1",
    "/kirogi/bg/mms/doConfirmShipping?is_back=1",
    "/kirogi/bg/mms/confirmShippingCommit?is_back=1",
    "/kirogi/bg/mms/updateShippingStatus?is_back=1",
    "/kirogi/bg/mms/confirmShipment?is_back=1",
    "/kirogi/bg/mms/batchConfirm?is_back=1",
    "/kirogi/bg/mms/confirmShippingOrder?is_back=1",
    # 也试 mms 前缀（非 kirogi）
    "/mms/eagle/package/confirm_shipping?is_back=1",
    "/mms/eagle/package/batch_confirm_shipping?is_back=1",
]
BODY = {"parentOrderSnList": [], "packageSnList": []}


async def call(path):
    ws_url, _ = A._page_ws("BHY")
    hdr = A.load_headers("BHY") or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep["mallid"] = A.MALLID["BHY"]
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(path),
                               json.dumps(BODY, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(await A._ev(ws_url, js) or "{}")
    txt = str(out.get("body") or "")
    try:
        j = json.loads(txt)
    except Exception:  # noqa: BLE001
        j = {"_raw": txt[:120]}
    return out.get("status"), j


async def main():
    for p in CANDIDATES:
        status, j = await call(p)
        code = j.get("errorCode") or j.get("error_code")
        msg = str(j.get("errorMsg") or j.get("error_msg") or j.get("_raw") or "")[:44]
        mark = " ★" if code == 0 or "参数" in msg or "empty" in msg.lower() else "  "
        print("%s%-56s http=%-4s code=%-8s %s"
              % (mark, p.replace("?is_back=1", "")[:54], status, code, msg))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
