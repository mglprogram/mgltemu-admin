# -*- coding: utf-8 -*-
"""BEL 403 最后一招：**完全复用页面自己的请求**（不自己拼 header）。

思路：
  页面加载时自己会发 `recentOrderList`（用户截图里 170 单待发货、26972 全部）。
  之前 hook 只抓到 6 条别的请求、没抓到它 —— 可能它是在 **iframe / worker** 里发的，
  或者用的不是 fetch/XHR（比如 axios 实例走 XHR 但 url 被拼过）。

  改用 **CDP Network 旁听**（浏览器层，不管用什么方式发都能看到），
  刷新后把页面自己那条 `recentOrderList` 的**完整 request headers** 抄下来。
"""
import asyncio
import json
import sys
import time
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402
import temu_buylabel_api as A  # noqa: E402


async def main():
    store = "BEL"
    ws_url, url = A._page_ws(store)
    print("页面:", url[:90])
    async with websockets.connect(ws_url, max_size=128 * 1024 * 1024) as ws:
        mid = [0]

        async def send(method, params):
            mid[0] += 1
            await ws.send(json.dumps({"id": mid[0], "method": method, "params": params}))
            return mid[0]

        await send("Network.enable", {"maxTotalBufferSize": 80000000})
        await send("Page.enable", {})
        print("旁听 + 刷新页面，抓页面自己的 recentOrderList …")
        await send("Page.reload", {"ignoreCache": False})

        end = time.time() + 60
        found = None
        while time.time() < end and not found:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue
            m = json.loads(raw)
            if m.get("method") != "Network.requestWillBeSent":
                continue
            r = (m.get("params") or {}).get("request") or {}
            u = r.get("url", "")
            if "recentOrderList" not in u:
                continue
            found = r
            print("\n✅ 抓到页面自己的请求：", u[:100])
            for k, v in (r.get("headers") or {}).items():
                print("   %-26s %s" % (k, str(v)[:90]))
            print("   postData:", str(r.get("postData"))[:200])

        if not found:
            print("\n60 秒内没抓到 recentOrderList（页面没发这条请求）")
            return 1

        hdr = {k: v for k, v in (found.get("headers") or {}).items()
               if k.lower().startswith("x-") or k.lower() in
               ("mallid", "web-version", "content-type")}
        body = {"fulfillmentMode": 0, "pageNumber": 1, "pageSize": 3,
                "queryType": 2, "sortType": 1, "timeZone": "UTC+8",
                "parentAfterSalesTag": 0, "needBuySignService": 0,
                "sellerNoteLabelList": []}
        js = "(%s)(%s, %s, %s)" % (
            A.JS_CALL, json.dumps("/kirogi/bg/mms/recentOrderList?is_back=1"),
            json.dumps(body, ensure_ascii=False),
            json.dumps(hdr, ensure_ascii=False))
        out = json.loads(await A._ev(ws_url, js) or "{}")
        print("\n【复用页面 header 复现】status:", out.get("status"))
        print("  body:", str(out.get("body"))[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
