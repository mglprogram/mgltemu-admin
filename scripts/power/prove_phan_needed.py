# -*- coding: utf-8 -*-
"""证明：BEL 的 403 是因为缺 `x-phan-data`（页面自己带、我们没带）。

抓包已证实（2026-11-25）：
  页面自己发 recentOrderList → **200**，headers 里有：
    x-phan-data: 0aeJxVzLsNgDAQA1CJMA7F-XLfWRCLMV4mIQUkQXLzZMut7IRW7u2Ep6oj3ST4GArUfMUk4rLISL5llz…
    x-document-referer / web-version / mallid

  我发同样的请求（只带 mallid）→ **403**。

本脚本：用抓到的这份 header 复现一次，确认 200；再不带 x-phan-data 复现，确认 403。
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402
import order_list_api as O  # noqa: E402

DUMP = r"D:\AI-workplace\TEMU\_logs\bel_order_api.json"


def page_headers() -> dict:
    d = json.load(open(DUMP, encoding="utf-8"))
    for r in d:
        if "recentOrderList" in r.get("url", "") and r.get("status") == 200:
            h = r.get("headers") or {}
            return {k: v for k, v in h.items()
                    if k.lower().startswith("x-") or k.lower() in
                    ("mallid", "web-version", "content-type")}
    return {}


async def fire(store: str, keep: dict, label: str):
    ws_url, _ = A._page_ws(store)
    body = dict(O.BASE_BODY, pageNumber=1, pageSize=3)
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(O.PATH),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(await asyncio.wait_for(A._ev(ws_url, js), timeout=45) or "{}")
    print("  %-28s status=%s  %s" % (label, out.get("status"),
                                     str(out.get("body"))[:80]))


async def main():
    hdr = page_headers()
    print("抓到页面 header 项:", sorted(hdr))
    print("含 x-phan-data:", bool(hdr.get("x-phan-data")))
    print()
    print("用页面原样 header 复现：")
    await fire("BEL", hdr, "带 x-phan-data（页面原样）")
    only = {k: v for k, v in hdr.items() if k.lower() != "x-phan-data"}
    print("去掉 x-phan-data 复现：")
    await fire("BEL", only, "只 mallid（我原来的做法）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
