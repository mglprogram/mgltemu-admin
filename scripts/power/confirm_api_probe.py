# -*- coding: utf-8 -*-
"""探测「批量确认发货」背后的接口（现在 temu_confirm 是纯页面点击）。

做法：浏览器级监听 + 在页面点一次「确认发货」流程里的**只读**步骤，
把所有 `/mms/`、`/kirogi/` 的请求记下来，找带 confirm/ship 的那个写接口。

⚠ 不点最后的「确认」——只抓**扫描/查询**阶段的请求，先看清有哪些接口。
"""
import asyncio
import json
import sys
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402
import ziniao_store as zs  # noqa: E402

KEEP = ("/mms/", "/kirogi/")
SKIP = ("pftka", "/pmm/", "monitor")


async def main():
    win = sys.argv[1] if len(sys.argv) > 1 else "TM-BHY-YJF"
    port = int(zs.probe_port_by_window(win) or 0)
    if not port:
        print("窗口未开")
        return 1
    tabs = zs.list_page_targets(port)
    for t in tabs:
        u = str(t.get("url") or "")
        if "orders.html" in u:
            print("订单页:", u[:100])
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port).read())
    print("👉 请在订单页点一下「批量确认发货」按钮（**到弹窗就停，不要确认**）…",
          flush=True)

    recs = {}
    async with websockets.connect(ver["webSocketDebuggerUrl"],
                                  max_size=128 * 1024 * 1024) as ws:
        mid = [0]

        async def send(method, params=None, sid=None):
            mid[0] += 1
            msg = {"id": mid[0], "method": method, "params": params or {}}
            if sid:
                msg["sessionId"] = sid
            await ws.send(json.dumps(msg))

        await send("Target.setDiscoverTargets", {"discover": True})
        loop = asyncio.get_event_loop()
        end = loop.time() + 180
        seen = set()
        while loop.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1, end - loop.time()))
            except asyncio.TimeoutError:
                break
            except Exception as ex:  # noqa: BLE001
                print("ws:", str(ex)[:60])
                break
            m = json.loads(raw)
            me, p = m.get("method"), (m.get("params") or {})
            if me == "Target.attachedToTarget":
                if p.get("sessionId"):
                    await send("Network.enable",
                               {"maxTotalBufferSize": 80000000}, p["sessionId"])
            elif me == "Target.targetCreated":
                ti = p.get("targetInfo") or {}
                if ti.get("type") == "page" and ti.get("targetId") not in seen:
                    seen.add(ti["targetId"])
                    await send("Target.attachToTarget",
                               {"targetId": ti["targetId"], "flatten": True})
            elif me == "Network.requestWillBeSent":
                r = p.get("request") or {}
                u = r.get("url", "")
                if not any(k in u for k in KEEP) or any(s in u for s in SKIP):
                    continue
                rid = p.get("requestId")
                recs[rid] = {"url": u, "method": r.get("method"),
                             "postData": (r.get("postData") or "")[:800]}
                print("\n→ %s %s" % (r.get("method"), u[:150]), flush=True)
                if recs[rid]["postData"]:
                    print("   body:", recs[rid]["postData"][:300], flush=True)

    print("\n共 %d 个接口：" % len(recs))
    for rec in recs.values():
        print("  %-6s %s" % (rec.get("method"), rec["url"][:130]))
    out = r"D:\AI-workplace\TEMU\_logs\temu-buylabel-api\confirm_api_probe.json"
    json.dump(list(recs.values()), open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n已存 →", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
