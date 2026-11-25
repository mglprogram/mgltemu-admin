# -*- coding: utf-8 -*-
"""抓 BEL 订单页加载时的**全部** /mms/ /kirogi/ 请求，找出真正返回订单列表的那个。

背景：BEL 页面有数据（待发货 170），但**不发** `recentOrderList`（两次抓都没抓到）。
⇒ 必须找出它实际用的接口，否则 `order_list_api` 永远对 BEL 无效。

做法：CDP Network 旁听 + 刷新，把每个 /mms|/kirogi 请求的 URL + body + 响应大小记下来，
按「响应里含 PO-211-」筛出真正带订单数据的那个。
"""
import asyncio
import json
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402
import temu_buylabel_api as A  # noqa: E402


async def main():
    store = sys.argv[1] if len(sys.argv) > 1 else "BEL"
    ws_url, url = A._page_ws(store)
    print("页面:", url[:90])
    async with websockets.connect(ws_url, max_size=200 * 1024 * 1024) as ws:
        mid = [0]
        raw_map = {}

        async def send(method, params, want=None):
            mid[0] += 1
            i = mid[0]
            await ws.send(json.dumps({"id": i, "method": method, "params": params}))
            if not want:
                return i
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == i:
                    return m

        await send("Network.enable", {"maxTotalBufferSize": 120000000})
        await send("Page.enable", {})
        print("刷新页面，抓全部 /mms|/kirogi 请求…")
        await send("Page.reload", {"ignoreCache": False})

        end = time.time() + 50
        pending = {}
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=4)
            except asyncio.TimeoutError:
                continue
            m = json.loads(raw)
            me = m.get("method")
            p = m.get("params") or {}
            if me == "Network.requestWillBeSent":
                r = p.get("request") or {}
                u = r.get("url", "")
                if not ("/mms/" in u or "/kirogi/" in u or "/api/" in u):
                    continue
                pending[p.get("requestId")] = {
                    "url": u, "method": r.get("method"),
                    "post": (r.get("postData") or "")[:300]}
            elif me == "Network.responseReceived":
                rid = p.get("requestId")
                if rid in pending:
                    resp = p.get("response") or {}
                    pending[rid]["status"] = resp.get("status")
                    pending[rid]["len"] = resp.get("encodedDataLength")
                    pending[rid]["mime"] = resp.get("mimeType")
                    # 拉响应体（只拉 json）
                    if "json" in str(resp.get("mimeType") or "").lower():
                        await send("Network.getResponseBody", {"requestId": rid})
            elif m.get("id", 0) >= 1 and "result" in m and "body" in (m.get("result") or {}):
                body = str(m["result"]["body"] or "")
                for rid, rec in pending.items():
                    if "body" not in rec and rec.get("mime", "").lower().find("json") >= 0:
                        rec["body"] = body[:400]
                        break

    hits = [(k, v) for k, v in pending.items()
            if "PO-211-" in str(v.get("body") or "") or "totalItemNum" in str(v.get("body") or "")]
    print("\n=== 含订单数据的接口 %d 个 ===" % len(hits))
    for _rid, rec in hits:
        print("  %-5s %-6s %-60s" % (rec.get("status"), rec.get("method"),
                                     rec["url"].replace("https://agentseller-us.temu.com", "")[:60]))
        print("     body:", str(rec.get("post"))[:120])
        print("     resp:", str(rec.get("body"))[:160])
    print("\n=== 全部 /mms|/kirogi|/api 请求 %d 个 ===" % len(pending))
    for _rid, rec in list(pending.items())[:30]:
        print("  %-5s %-6s %s" % (rec.get("status"), rec.get("method"),
                                   rec["url"].replace("https://agentseller-us.temu.com", "")[:80]))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
