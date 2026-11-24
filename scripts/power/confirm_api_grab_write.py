# -*- coding: utf-8 -*-
"""抓「确认发货」的**写接口**：二次确认弹窗已弹出，用户点「确认」前先挂监听。

用户截图：抽屉底部「确认发货 100」→ 二级弹窗「是否确认发货？ 确认/取消」。
现在挂监听，用户点「确认」后抓那条 POST 的路径 + body。
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
SKIP = ("pftka", "/pmm/", "monitor", "statistic")


async def main():
    port = int(zs.probe_port_by_window("TM-BHY-YJF") or 0)
    if not port:
        print("窗口未开")
        return 1
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port).read())
    print("👉 监听已就绪，请点弹窗里的「**确认**」", flush=True)

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
        end = loop.time() + 240
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
                if r.get("method") != "POST":
                    continue
                rid = p.get("requestId")
                body = r.get("postData") or ""
                recs[rid] = {"url": u, "body": body}
                # 只打印「非查询类」的
                if any(k in u for k in ("List", "list", "Query", "query")):
                    continue
                print("\n★ POST %s" % u[:150], flush=True)
                print("   body:", body[:500], flush=True)

    print("\n=== 所有 POST ===")
    for rec in recs.values():
        print("  %s" % rec["url"][:140])
        if rec["body"]:
            print("     %s" % rec["body"][:200])
    out = r"D:\AI-workplace\TEMU\_logs\temu-buylabel-api\confirm_write_api.json"
    json.dump(list(recs.values()), open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n已存 →", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
