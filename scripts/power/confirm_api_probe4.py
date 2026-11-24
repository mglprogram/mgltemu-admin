# -*- coding: utf-8 -*-
"""试 `POST confirmShippingList` 带空 packageSnList —— 看写接口是否就是它本身。

假设：TEMU 的读/写常复用同一路径，用不同 body 区分。
空数组不会确认任何单（安全）；若返回「参数不能为空」类错误 ⇒ 找到写入口了。
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402

P = "/kirogi/bg/mms/confirmShippingList?is_back=1"
CASES = {
    "空 packageSnList": {"packageSnList": []},
    "空 parentOrderSnList": {"parentOrderSnList": []},
    "空两者": {"packageSnList": [], "parentOrderSnList": []},
    "带 onlyConfirm 标志": {"packageSnList": [], "parentOrderSnList": [],
                            "confirmType": 1},
}


async def call(body):
    ws_url, _ = A._page_ws("BHY")
    hdr = A.load_headers("BHY") or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep["mallid"] = A.MALLID["BHY"]
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(P),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(await A._ev(ws_url, js) or "{}")
    txt = str(out.get("body") or "")
    try:
        j = json.loads(txt)
    except Exception:  # noqa: BLE001
        j = {"_raw": txt[:150]}
    return out.get("status"), j


async def main():
    for name, body in CASES.items():
        status, j = await call(body)
        code = j.get("errorCode") or j.get("error_code")
        msg = str(j.get("errorMsg") or j.get("error_msg") or j.get("_raw") or "")[:70]
        res = j.get("result") or {}
        keys = ",".join(sorted(res.keys()))[:40] if isinstance(res, dict) else ""
        print("%-22s http=%-4s code=%-8s %s %s"
              % (name, status, code, msg, keys))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
