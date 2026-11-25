# -*- coding: utf-8 -*-
"""定位 BEL 订单列表**真正的数据接口**（`totalItemNum` 从哪来）。

已抓到两条关键响应（2026-11-25）：
  ① `/mms/orchid/address/batch/snapshot/order_shipping_address_base_info_query`
     → result 是 `{PO-xxx: {region_name...}}`（只是地址快照，不是列表）
  ② 还有一条返回 `{"success":true,"errorCode":0,"result":{"totalItemNum":0,
     "pageItems":[],"mallInfoMap":...}}` ← **这才是订单列表接口**，但 totalItemNum=0

⇒ 必须抓出 ② 的 URL。做法：按响应体内容反查（含 `mallInfoMap` / `totalItemNum`）。
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

MARK = ("totalItemNum", "mallInfoMap", "pageItems")


async def main():
    store = sys.argv[1] if len(sys.argv) > 1 else "BEL"
    ws_url, url = A._page_ws(store)
    print("页面:", url[:90])
    async with websockets.connect(ws_url, max_size=200 * 1024 * 1024) as ws:
        mid = [0]

        async def send(method, params):
            mid[0] += 1
            i = mid[0]
            await ws.send(json.dumps({"id": i, "method": method, "params": params}))
            return i

        await send("Network.enable", {"maxTotalBufferSize": 120000000})
        await send("Page.enable", {})
        print("刷新页面…")
        await send("Page.reload", {"ignoreCache": False})

        pending = {}
        bodies = {}
        end = time.time() + 50
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
                if "agentseller" not in u or "pftka" in u:
                    continue
                rid = p.get("requestId")
                pending[rid] = {"url": u, "method": r.get("method"),
                                "post": (r.get("postData") or "")[:400],
                                "h": {k: str(v)[:60] for k, v in
                                      (r.get("headers") or {}).items()
                                      if k.lower().startswith("x-") or
                                      k.lower() in ("mallid", "web-version")}}
            elif me == "Network.responseReceived":
                rid = p.get("requestId")
                if rid in pending and "json" in str(
                        (p.get("response") or {}).get("mimeType") or "").lower():
                    await send("Network.getResponseBody", {"requestId": rid})
            elif "id" in m and (m.get("result") or {}).get("body") is not None:
                bodies[f"id{m['id']}"] = str(m["result"]["body"])[:5000]

        # 按 URL 顺序把响应体配回去（getResponseBody 的 id 与请求顺序对应）
        body_list = list(bodies.values())
        hit = None
        for b in body_list:
            if all(k in b for k in MARK):
                hit = b
                break
        print("\n=== 响应含 totalItemNum/mallInfoMap 的条数：%d ==="
              % sum(1 for b in body_list if all(k in b for k in MARK)))
        if hit:
            print("样例响应:", hit[:300])

        # 再按 URL 找：打印所有可能承载列表的接口（POST + 非埋点）
        print("\n=== 所有 POST 接口（非埋点）===")
        for rid, rec in pending.items():
            if rec.get("method") != "POST":
                continue
            u = rec["url"].replace("https://agentseller-us.temu.com", "")
            if any(s in u for s in ("pftka", "/report", "phantom", "_stm")):
                continue
            print("  %-72s %s" % (u[:72], rec["post"][:90]))
            print("      headers:", rec["h"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
