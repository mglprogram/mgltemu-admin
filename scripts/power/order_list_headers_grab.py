# -*- coding: utf-8 -*-
"""抓 `recentOrderList` 的**真实请求头**（订单列表接口 403 / 40001 时用）。

做法：浏览器级监听，抓 `/kirogi/bg/mms/` 的请求头（`x-phan-data` 之类的签名），
存进 `data/temu_goods_ids.json` 同目录的 `_headers_<store>.json`，供 order_list_api 复用。

触发方式：页面刷新订单列表 / 点「查询」，就会重发这条请求。
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

OUT_DIR = r"D:\AI-workplace\TEMU\data\temu-buylabel"


async def main():
    win = sys.argv[1] if len(sys.argv) > 1 else "TM-BHY-YJF"
    store = {"TM-BHY-YJF": "BHY", "TM-BEL-MGL": "BEL"}.get(win, "BHY")
    port = int(zs.probe_port_by_window(win) or 0)
    if not port:
        print("窗口 %s 未开" % win)
        return 1
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port).read())
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "_headers_%s.json" % store)
    print("浏览器级监听 %s（240s）" % store)
    print("👉 请刷新订单列表页，或点一下「查询」", flush=True)

    found = {}
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
                print("ws:", str(ex)[:80])
                break
            m = json.loads(raw)
            me, p = m.get("method"), (m.get("params") or {})
            if me == "Target.attachedToTarget":
                if p.get("sessionId"):
                    await send("Network.enable", {"maxTotalBufferSize": 40000000},
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
                if "recentOrderList" not in u and "/kirogi/bg/mms/" not in u:
                    continue
                h = {k: str(v)[:500] for k, v in (r.get("headers") or {}).items()}
                if h:
                    found = h
                    json.dump(found, open(path, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)
                    print("\n✅ 抓到 header（%d 项）：%s" % (len(h), u[-60:]))
                    for k in h:
                        if k.lower().startswith("x-") or k in ("mallid", "web-version"):
                            print("   %-24s %s" % (k, str(h[k])[:60]))
                    return 0
        print("\n没抓到（%d）" % len(found))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
