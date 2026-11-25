# -*- coding: utf-8 -*-
"""监听「从紫鸟启动店铺 → 登录授权」全过程，抓授相关的网络请求 + 页面跳转。

用户：你开监听，看我是从紫鸟点击启动到登录授权。

做法（只读）：
  ① 每 3 秒扫一遍所有紫鸟窗口的 CDP 端口（窗口一开就能发现）
  ② 端口出现后，浏览器级 attach，打印所有 temu 域请求（URL + 状态）
  ③ 同时记录页面 URL 变化（谁跳到了 auth / orders / seller-login）
  ④ 一直跑到超时或手动停止
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

WATCH = ["TM-BEL-MGL", "TM-KinglamanSport", "TM-BHY-YJF"]
DUMP = r"D:\AI-workplace\TEMU\_logs\_auth_watch.json"


def find_ports() -> dict:
    out = {}
    for w in WATCH:
        try:
            p = zs.probe_port_by_window(w)
        except Exception:  # noqa: BLE001
            p = 0
        if p:
            out[w] = int(p)
    return out


async def watch_port(win: str, port: int, seen: dict, events: list, end: float):
    try:
        ver = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/json/version" % port, timeout=8).read())
    except Exception as ex:  # noqa: BLE001
        print("  [%s] 连不上 %s: %s" % (win, port, str(ex)[:60]))
        return
    try:
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
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                except Exception:  # noqa: BLE001
                    break
                m = json.loads(raw)
                me, p = m.get("method"), (m.get("params") or {})
                if me == "Target.attachedToTarget":
                    if p.get("sessionId"):
                        await send("Network.enable",
                                   {"maxTotalBufferSize": 40000000}, p["sessionId"])
                elif me == "Target.targetCreated":
                    ti = p.get("targetInfo") or {}
                    if ti.get("type") == "page":
                        u = str(ti.get("url") or "")
                        if "temu" in u:
                            k = "newpage|" + u[:90]
                            if k not in seen:
                                seen[k] = 1
                                print("  [%s] 新页: %s" % (win, u[:100]))
                                events.append({"t": time.time(), "win": win,
                                               "kind": "newpage", "url": u})
                        await send("Target.attachToTarget",
                                   {"targetId": ti.get("targetId"), "flatten": True})
                elif me == "Network.requestWillBeSent":
                    r = p.get("request") or {}
                    u = r.get("url", "")
                    if "temu" not in u or "pftka" in u or "/pmm/" in u:
                        continue
                    k = "req|" + r.get("method", "") + "|" + u[:110]
                    if k in seen:
                        continue
                    seen[k] = 1
                    print("  [%s] → %s %s" % (win, r.get("method"), u[:110]))
                    events.append({"t": time.time(), "win": win, "kind": "req",
                                   "method": r.get("method"), "url": u})
                elif me == "Network.responseReceived":
                    rid = p.get("requestId")
                    st = (p.get("response") or {}).get("status")
                    u = (p.get("response") or {}).get("url", "")
                    if st and st >= 400 and "temu" in u:
                        k = "resp|%s|%s" % (st, u[:100])
                        if k not in seen:
                            seen[k] = 1
                            print("  [%s] ← %s %s" % (win, st, u[:100]))
                elif me == "Network.frameNavigated":
                    fr = p.get("frame") or {}
                    u = str(fr.get("url") or "")
                    if "temu" not in u:
                        continue
                    k = "nav|" + u[:110]
                    if k not in seen:
                        seen[k] = 1
                        print("  [%s] NAV %s" % (win, u[:110]))
                        events.append({"t": time.time(), "win": win,
                                       "kind": "nav", "url": u})
    except Exception as ex:  # noqa: BLE001
        print("  [%s] ws 断开: %s" % (win, str(ex)[:70]))


async def main():
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    print("监听紫鸟窗口启动+授权，最长 %ds" % secs)
    print("👉 请现在从紫鸟点店铺启动，按你的习惯走到登录/授权完成", flush=True)
    seen, events = {}, []
    done_ports = set()
    end = time.time() + secs
    tasks = []
    while time.time() < end:
        ports = find_ports()
        for w, p in ports.items():
            if (w, p) in done_ports:
                continue
            done_ports.add((w, p))
            print("\n[发现窗口] %s 端口 %s" % (w, p), flush=True)
            tasks.append(asyncio.create_task(watch_port(w, p, seen, events, end)))
        await asyncio.sleep(3)
    for t in tasks:
        t.cancel()
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:  # noqa: BLE001
        pass
    os.makedirs(os.path.dirname(DUMP), exist_ok=True)
    json.dump(events, open(DUMP, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n共记录 %d 个事件 → %s" % (len(events), DUMP))


if __name__ == "__main__":
    asyncio.run(main())
