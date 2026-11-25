# -*- coding: utf-8 -*-
"""监听 BEL 点「查询」时发出的订单列表请求（找出真正有权限的那个接口）。

用户会手动点订单列表的「查询」按钮；这里挂浏览器级 Network 旁听，
把**返回里含 PO-211- 或 totalItemNum** 的那条连 URL/headers/body 一起记下来。
"""
import asyncio
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402
import ziniao_store as zs  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\_logs\bel_order_api.json"


async def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    port = int(zs.probe_port_by_window("TM-BEL-MGL") or 0)
    if not port:
        print("BEL 窗口未开")
        return 1
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port, timeout=10).read())
    print("BEL 端口 %d，最长监听 %ds" % (port, secs))
    print("👉 请在 BEL 订单列表点一下「查询」（或切到「待发货」页签）", flush=True)

    recs, pending = {}, {}
    async with websockets.connect(ver["webSocketDebuggerUrl"],
                                  max_size=200 * 1024 * 1024) as ws:
        mid = [0]

        async def send(method, params=None, sid=None):
            mid[0] += 1
            msg = {"id": mid[0], "method": method, "params": params or {}}
            if sid:
                msg["sessionId"] = sid
            await ws.send(json.dumps(msg))
            return mid[0]

        await send("Target.setDiscoverTargets", {"discover": True})
        end = time.time() + secs
        seen = set()
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=4)
            except asyncio.TimeoutError:
                continue
            except Exception as ex:  # noqa: BLE001
                print("ws:", str(ex)[:60])
                break
            m = json.loads(raw)
            me, p = m.get("method"), (m.get("params") or {})
            if me == "Target.attachedToTarget":
                if p.get("sessionId"):
                    await send("Network.enable", {"maxTotalBufferSize": 100000000},
                               p["sessionId"])
            elif me == "Target.targetCreated":
                ti = p.get("targetInfo") or {}
                if ti.get("type") == "page" and ti.get("targetId") not in seen:
                    seen.add(ti["targetId"])
                    await send("Target.attachToTarget",
                               {"targetId": ti["targetId"], "flatten": True})
            elif me == "Network.requestWillBeSent":
                r = p.get("request") or {}
                u = r.get("url", "")
                if "agentseller" not in u or "pftka" in u or "/report" in u:
                    continue
                rid = p.get("requestId")
                pending[rid] = {
                    "url": u, "method": r.get("method"),
                    "post": (r.get("postData") or "")[:600],
                    "headers": {k: str(v)[:80] for k, v in
                                (r.get("headers") or {}).items()},
                    "sessionId": p.get("sessionId")}
            elif me == "Network.responseReceived":
                rid = p.get("requestId")
                if rid in pending:
                    resp = p.get("response") or {}
                    pending[rid]["status"] = resp.get("status")
                    if "json" in str(resp.get("mimeType") or "").lower():
                        await send("Network.getResponseBody", {"requestId": rid},
                                   p.get("sessionId"))
                        recs[rid] = pending.pop(rid)
            elif "id" in m and (m.get("result") or {}).get("body") is not None:
                body = str(m["result"]["body"] or "")
                for rid, rec in recs.items():
                    if "body" not in rec:
                        rec["body"] = body[:3000]
                        break

    hits = [(rid, r) for rid, r in recs.items()
            if "totalItemNum" in str(r.get("body") or "")
            or "PO-211-" in str(r.get("body") or "")]
    print("\n=== 含订单数据的接口 %d 个 ===" % len(hits))
    for rid, r in hits:
        print("\n★ %s %s" % (r.get("method"),
                             r["url"].replace("https://agentseller-us.temu.com", "")[:100]))
        print("   status:", r.get("status"))
        print("   post:", str(r.get("post"))[:250])
        print("   resp:", str(r.get("body"))[:250])
        hs = {k: v for k, v in (r.get("headers") or {}).items()
              if k.lower().startswith("x-") or k.lower() in ("mallid", "web-version")}
        print("   关键 header:", hs)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump([{k: v for k, v in r.items() if k != "sessionId"} for r in recs.values()],
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n全部 %d 条 → %s" % (len(recs), OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
