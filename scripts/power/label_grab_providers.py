# -*- coding: utf-8 -*-
"""浏览器级抓 `shipping_provider_optional`（批量版优先）—— 页面跳转/新开页都不怕。

为什么改：页面级 ws 在「重新进在线下单页」时会断（实测 3 次全断）；
`label_grab_goods.py` 用浏览器级（Target.setDiscoverTargets + 自动 attach）成功过，这里照抄。

产物：`_logs/temu-buylabel-api/batch_providers.json`
"""
import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402
import ziniao_store as zs  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\_logs\temu-buylabel-api"


async def main():
    port = int(zs.probe_port_by_window("TM-BHY-YJF") or 0)
    if not port:
        print("BHY 窗口未开")
        return 1
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port).read())
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "batch_providers.json")
    print("浏览器级监听 BHY（最多 300s）")
    print("👉 请点开 PO-211-01522678948473053 的「在线下单」→ 仓库选 kansas", flush=True)

    recs, pending = [], {}
    async with websockets.connect(ver["webSocketDebuggerUrl"],
                                  max_size=256 * 1024 * 1024) as ws:
        mid = [0]

        async def send(method, params=None, sid=None):
            mid[0] += 1
            msg = {"id": mid[0], "method": method, "params": params or {}}
            if sid:
                msg["sessionId"] = sid
            await ws.send(json.dumps(msg))
            return mid[0]

        await send("Target.setDiscoverTargets", {"discover": True})
        loop = asyncio.get_event_loop()
        end = loop.time() + 300
        seen = set()
        n = 100000
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
                sid = p.get("sessionId")
                if sid:
                    await send("Network.enable", {"maxTotalBufferSize": 80000000}, sid)
            elif me == "Target.targetCreated":
                ti = p.get("targetInfo") or {}
                if ti.get("type") == "page" and ti.get("targetId") not in seen:
                    seen.add(ti["targetId"])
                    await send("Target.attachToTarget",
                               {"targetId": ti["targetId"], "flatten": True})
            elif me == "Network.requestWillBeSent":
                r = p.get("request") or {}
                u = r.get("url", "")
                if "shipping_provider_optional" not in u:
                    continue
                rid = p.get("requestId")
                pending[rid] = {"url": u, "postData": r.get("postData") or "",
                                "sessionId": p.get("sessionId")}
                tag = "★批量" if "batch_" in u else "单渠道"
                print("\n→ [%s] body %d 字节" % (tag, len(pending[rid]["postData"])),
                      flush=True)
                if r.get("postData"):
                    print("   ", r["postData"][:400], flush=True)
            elif me == "Network.responseReceived":
                rid = p.get("requestId")
                if rid in pending:
                    rec = pending.pop(rid)
                    rec["status"] = (p.get("response") or {}).get("status")
                    recs.append(rec)
                    n += 1
                    await send("Network.getResponseBody", {"requestId": rid},
                               p.get("sessionId"))
                    json.dump(recs, open(path, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)
            elif m.get("id", 0) >= 100000 and (m.get("result") or {}).get("body") is not None:
                if recs:
                    recs[-1]["responseBody"] = str(m["result"]["body"])[:200000]
                    json.dump(recs, open(path, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)
                    print("   ← 响应 %d 字节" % len(recs[-1]["responseBody"]), flush=True)

    print("\n落盘 %d 条 → %s" % (len(recs), path))
    for r in recs:
        tag = "批量" if "batch_" in r["url"] else "单渠道"
        print("  [%s] %s body=%d resp=%d"
              % (tag, r.get("status"), len(r.get("postData") or ""),
                 len(r.get("responseBody") or "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
