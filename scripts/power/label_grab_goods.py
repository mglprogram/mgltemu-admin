# -*- coding: utf-8 -*-
"""浏览器级监听：抓 `batch_check_request` → 存 goods 映射表（BHY 窗口，可反复用）。

为什么要在**浏览器级**挂：在线下单页会跳转，页面级 ws 会被冲断；
`Target.setDiscoverTargets` + 自动 attach 新页则不受影响（2026-11-23 实测抓成功）。
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

MAP = r"D:\AI-workplace\TEMU\data\temu_goods_ids.json"


def _merge(rows: list) -> int:
    cur = {}
    if os.path.exists(MAP):
        try:
            cur = json.load(open(MAP, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cur = {}
    for g in rows:
        k = "%s|%s" % (g.get("parent_order_sn"), g.get("order_sn"))
        cur[k] = {"goods_id": g.get("goods_id"), "sku_id": str(g.get("sku_id")),
                  "parent_order_sn": g.get("parent_order_sn"),
                  "order_sn": g.get("order_sn"), "quantity": g.get("quantity", 1)}
    os.makedirs(os.path.dirname(MAP), exist_ok=True)
    json.dump(cur, open(MAP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(rows)


async def main():
    win = sys.argv[1] if len(sys.argv) > 1 else "TM-BHY-YJF"
    wait_s = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    port = int(zs.probe_port_by_window(win) or 0)
    if not port:
        print("窗口 %s 未开" % win)
        return 1
    ver = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port).read())
    print("浏览器级监听:", win, "| 最多 %ds" % wait_s)
    print("👉 请点开一张单的「在线下单」（进页面即可，不要提交）…", flush=True)

    async with websockets.connect(ver["webSocketDebuggerUrl"],
                                  max_size=128 * 1024 * 1024) as ws:
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
        end = loop.time() + wait_s
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
                sid = p.get("sessionId")
                if sid:
                    await send("Network.enable", {"maxTotalBufferSize": 60000000}, sid)
            elif me == "Target.targetCreated":
                ti = p.get("targetInfo") or {}
                if ti.get("type") == "page" and ti.get("targetId") not in seen:
                    seen.add(ti["targetId"])
                    await send("Target.attachToTarget",
                               {"targetId": ti["targetId"], "flatten": True})
            elif me == "Network.requestWillBeSent":
                r = p.get("request") or {}
                if "batch_check_request" not in r.get("url", ""):
                    continue
                try:
                    body = json.loads(r.get("postData") or "{}")
                except Exception:  # noqa: BLE001
                    continue
                rows = []
                for it in body.get("send_request_list") or []:
                    rows.extend(it.get("order_send_info_list") or [])
                if not rows:
                    continue
                n = _merge(rows)
                print("\n✅ 抓到 %d 条 goods → %s" % (n, MAP))
                for g in rows[:12]:
                    print("   %s | %s | goods_id=%s sku_id=%s"
                          % (g.get("parent_order_sn"), g.get("order_sn"),
                             g.get("goods_id"), g.get("sku_id")))
                return 0
        print("没抓到")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
