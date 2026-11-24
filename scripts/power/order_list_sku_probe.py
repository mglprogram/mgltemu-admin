# -*- coding: utf-8 -*-
"""翻 `recentOrderList` 的**完整单条结构**，看 orderList[] 里有没有 SKU 货号。

`order_list_api.parse_items` 只抽了 `orderSn/goodsId/skuId/quantity`，
但原始 `orderList[]` 里可能还有 `skuCode / sellerSku / skuSn`。
若有 ⇒ split 走接口时不必逐单调 orderDetail。
"""
import json
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import asyncio  # noqa: E402

import temu_buylabel_api as A  # noqa: E402
import order_list_api as O  # noqa: E402


def main():
    hdr = A.load_headers("BHY") or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep["mallid"] = A.MALLID["BHY"]
    ws_url, _ = A._page_ws("BHY")
    body = dict(O.BASE_BODY, pageNumber=1, pageSize=1)
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(O.PATH),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(asyncio.run(A._ev(ws_url, js)) or "{}")
    j = json.loads(out.get("body") or "{}")
    items = (j.get("result") or {}).get("pageItems") or []
    if not items:
        print("没拉到单")
        return 1
    one = items[0]
    print("pageItem 顶层键:", sorted(one.keys()))

    ol = one.get("orderList") or []
    print("\norderList 条数:", len(ol))
    if ol:
        print("orderList[0] 全部键:", sorted(ol[0].keys()))
        # 找像货号的值
        txt = json.dumps(ol[0], ensure_ascii=False)
        print("\norderList[0] 里的候选货号:")
        for m in re.finditer(r'"([A-Za-z_]*[Ss]ku[A-Za-z_]*)"\s*:\s*"([^"]{3,40})"', txt):
            print("   %-22s = %s" % (m.group(1), m.group(2)))
        cands = sorted(set(re.findall(r'"([A-Z]+-[A-Za-z0-9\-]{4,30})"', txt)))
        print("\n像货号的字符串:", cands[:10])

    pm = one.get("parentOrderMap") or {}
    print("\nparentOrderMap 含 sku 的键:",
          sorted(k for k in pm if "sku" in k.lower() or "Seller" in k)[:10])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
