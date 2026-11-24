# -*- coding: utf-8 -*-
"""看 `orderDetail` 返回里有没有 SKU 货号（同步脚本，避免 PowerShell 转义）。"""
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


def main():
    hdr = A.load_headers("BHY") or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep["mallid"] = A.MALLID["BHY"]
    ws_url, _ = A._page_ws("BHY")
    js = "(%s)(%s, %s, %s)" % (
        A.JS_CALL, json.dumps("/kirogi/bg/mms/orderDetail"),
        json.dumps({"parentOrderSn": PO}, ensure_ascii=False),
        json.dumps(keep, ensure_ascii=False))
    out = json.loads(asyncio.run(A._ev(ws_url, js)) or "{}")
    j = json.loads(out.get("body") or "{}")
    txt = json.dumps(j, ensure_ascii=False)

    print("成功:", j.get("success"), "| 长度:", len(txt))
    print("顶层键:", list(j)[:8])
    res = j.get("result") or {}
    print("result 键:", list(res)[:20] if isinstance(res, dict) else type(res))

    keys = sorted(set(re.findall(r'"([A-Za-z_]*[Ss]ku[A-Za-z_]*)"', txt)))
    print("\n含 sku 的键名:", keys[:25])

    i = txt.find(SKU_ID)
    if i > 0:
        print("\nskuId 出现处上下文:")
        print("   ", txt[max(0, i - 260):i + 160])
    else:
        print("\n⚠ 响应里没有 skuId", SKU_ID)

    # 找像货号的字符串（BH-xxx / 含 -4Set）
    cands = sorted(set(re.findall(r'"[^"]{4,40}-[A-Za-z0-9]{2,8}"', txt)))[:20]
    print("\n像货号的字符串:", cands[:12])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
