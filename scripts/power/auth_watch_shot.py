# -*- coding: utf-8 -*-
"""开监听 + 定时截屏：记录「KLS 窗口从授权页 → 进订单列表」的全过程。

用途：用户手动操作一遍，我把每一步的 URL / 点击位置 / 网络请求都记下来，
之后写成脚本复现。

产物：
  _logs/auth_watch_kls/<时间>.png    截图序列
  _logs/auth_watch_kls/events.json   事件（URL 跳转、请求、点击）
"""
import asyncio
import base64
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\_logs\auth_watch_kls"
PORT_DISCOVER = True
SECS = int(sys.argv[1]) if len(sys.argv) > 1 else 180


def find_port() -> int:
    sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
    sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
    import ziniao_store as zs  # noqa: PLC0415
    for w in ("TM-KinglamanSport", "TM-BHY-YJF", "TM-BEL-MGL"):
        try:
            p = zs.probe_port_by_window(w)
        except Exception:  # noqa: BLE001
            p = 0
        if p:
            print("窗口 %s 端口 %s" % (w, p))
            return int(p)
    return 0


async def main():
    os.makedirs(OUT, exist_ok=True)
    port = find_port()
    if not port:
        print("没有可用窗口")
        return 1
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port, timeout=10).read())

    events = []
    shot = [0]

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

        async def wait_for(i, timeout=10):
            t0 = time.time()
            while time.time() - t0 < timeout:
                m = json.loads(await ws.recv())
                if m.get("id") == i:
                    return m
                handle(m)
            return {}

        def handle(m):
            me, p = m.get("method"), (m.get("params") or {})
            if me == "Network.frameNavigated":
                u = str((p.get("frame") or {}).get("url") or "")
                if "temu" in u:
                    events.append({"t": time.time(), "kind": "nav", "url": u})
                    print("  NAV %s" % u[:110])
            elif me == "Network.requestWillBeSent":
                r = p.get("request") or {}
                u = r.get("url", "")
                if "temu.com" in u and "/mmsos/" in u:
                    events.append({"t": time.time(), "kind": "req",
                                   "method": r.get("method"), "url": u[:160]})
            elif me == "Target.targetCreated":
                ti = p.get("targetInfo") or {}
                if ti.get("type") == "page" and "temu" in str(ti.get("url") or ""):
                    events.append({"t": time.time(), "kind": "newpage",
                                   "url": str(ti.get("url"))[:160]})

        await send("Target.setDiscoverTargets", {"discover": True})
        await asyncio.sleep(1)

        tabs = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/json/list" % port, timeout=10).read())
        tgt = [t for t in tabs if t.get("type") == "page"]
        print("页数:", len(tgt))
        for t in tgt:
            print("  ", str(t.get("url"))[:100])
            await send("Target.attachToTarget", {"targetId": t["id"], "flatten": True})

        print("\n👉 请现在手动操作：点「商家中心」→ 完成授权 → 进订单列表（最多 %ds）"
              % SECS, flush=True)

        end = time.time() + SECS
        last_shot = 0
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
            except asyncio.TimeoutError:
                raw = None
            if raw:
                handle(json.loads(raw))
            # 每 5 秒截一张
            if time.time() - last_shot >= 5:
                last_shot = time.time()
                try:
                    i = await send("Page.captureScreenshot", {"format": "png"})
                    m = await wait_for(i, timeout=15)
                    data = ((m.get("result") or {}).get("data") or "")
                    if data:
                        shot[0] += 1
                        p = os.path.join(OUT, "%03d_%s.png"
                                         % (shot[0], time.strftime("%H%M%S")))
                        with open(p, "wb") as f:
                            f.write(base64.b64decode(data))
                except Exception:  # noqa: BLE001
                    pass

    with open(os.path.join(OUT, "events.json"), "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=1)
    print("\n截图 %d 张 / 事件 %d 条 → %s" % (shot[0], len(events), OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
