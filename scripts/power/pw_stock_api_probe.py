# -*- coding: utf-8 -*-
"""探测帕沃尔库存页背后有没有 JSON 接口（替代抓 HTML）。

用户令：「split 这一步骤也接口化，你去探测」。
split 依赖两份库存：`pw-stock.json`（帕沃尔，现在抓 HTML）、`linxing-stock.json`（恒信）。

做法（**只读，不改数据**）：
  1. 在紫鸟窗口新开一个 target 访问 `stock.php?act=list`
  2. 浏览器级 Network 旁听，收集该域所有 XHR，标出返回 JSON 的
  3. 打印候选接口的 URL / 方法 / postData
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

TARGET = "https://www.powerwarehouse.net/stock.php?act=list"


async def main():
    port = int(zs.probe_port_by_window("TM-BHY-YJF") or 0)
    if not port:
        print("BHY 窗口未开")
        return 1
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port).read())
    print("探测目标:", TARGET)

    async with websockets.connect(ver["webSocketDebuggerUrl"],
                                  max_size=128 * 1024 * 1024) as ws:
        mid = [0]

        async def send(method, params=None, sid=None):
            mid[0] += 1
            msg = {"id": mid[0], "method": method, "params": params or {}}
            if sid:
                msg["sessionId"] = sid
            await ws.send(json.dumps(msg))

        await send("Target.createTarget", {"url": TARGET})
        recs, seen = {}, set()
        loop = asyncio.get_event_loop()
        end = loop.time() + 45
        while loop.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1, end - loop.time()))
            except asyncio.TimeoutError:
                break
            except Exception as ex:  # noqa: BLE001
                print("ws:", str(ex)[:80])
                break
            m = json.loads(raw)
            me, p = m.get("method"), (m.get("params") or {})
            if me == "Target.attachedToTarget":
                if p.get("sessionId"):
                    await send("Network.enable",
                               {"maxTotalBufferSize": 60000000}, p["sessionId"])
            elif me == "Target.targetCreated":
                ti = p.get("targetInfo") or {}
                if ti.get("type") == "page" and ti.get("targetId") not in seen:
                    seen.add(ti["targetId"])
                    await send("Target.attachToTarget",
                               {"targetId": ti["targetId"], "flatten": True})
            elif me == "Network.requestWillBeSent":
                r = p.get("request") or {}
                u = r.get("url", "")
                if "powerwarehouse" not in u:
                    continue
                rid = p.get("requestId")
                recs[rid] = {"url": u, "method": r.get("method"),
                             "type": p.get("type"),
                             "postData": (r.get("postData") or "")[:300]}
            elif me == "Network.responseReceived":
                rid = p.get("requestId")
                if rid in recs:
                    resp = p.get("response") or {}
                    recs[rid]["status"] = resp.get("status")
                    recs[rid]["mime"] = resp.get("mimeType")

    print("\n帕沃尔域请求 %d 条：" % len(recs))
    js = [r for r in recs.values() if "json" in str(r.get("mime") or "").lower()
          or str(r.get("type") or "").lower() in ("xhr", "fetch")]
    for rec in recs.values():
        tag = " ★" if rec in js else "  "
        print("%s%-4s %-6s %-58s %s"
              % (tag, rec.get("status"), rec.get("method"),
                 rec["url"].replace("https://www.powerwarehouse.net", "")[:58],
                 str(rec.get("mime") or "")[:24]))
    if js:
        print("\n候选 JSON 接口：")
        for rec in js:
            print("  %s %s" % (rec.get("method"), rec["url"]))
            if rec.get("postData"):
                print("     body:", rec["postData"][:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
