# -*- coding: utf-8 -*-
"""在指定 CDP 端口的目标 tab 上，用**真实鼠标事件**点击页面坐标。

两个必须点（2026-10-10 实测）：
  1. TEMU 认证页对 JS `el.click()` 完全不响应 —— 必须走 CDP Input.dispatchMouseEvent。
  2. **整组鼠标事件必须在同一条 websocket 上发**：`cdp_transport.dispatch_mouse`
     每个事件各新建一条连接（Cdp(...) 每次 connect），mousePressed/mouseReleased
     落在不同连接上，浏览器不把它合成一次点击 —— 探针里 click 事件永远为 0。
     这里改成一条长连接顺序发 moved/pressed/released。

用法：
  python cdp_click.py <port> <url子串> <x> <y>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(r"D:\AI-workplace\TEMU\scripts\power\lib")))
from cdp_transport import Cdp, http_targets  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def pick(port: int, needle: str):
    for t in http_targets(port):
        if t.get("type") == "page" and needle in (t.get("url") or ""):
            return t
    return None


def click(ws: str, x: float, y: float) -> None:
    """一条长连接内顺序发完整鼠标序列。"""
    import asyncio

    import websockets

    async def go():
        async with websockets.connect(ws, max_size=40 * 1024 * 1024) as sock:
            async def send(method, params):
                await sock.send(json.dumps({"id": 1, "method": method, "params": params}))
                while True:
                    m = json.loads(await asyncio.wait_for(sock.recv(), timeout=10))
                    if m.get("id") == 1:
                        return m

            await send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
            await send("Input.dispatchMouseEvent",
                       {"type": "mousePressed", "x": x, "y": y, "button": "left",
                        "buttons": 1, "clickCount": 1})
            await send("Input.dispatchMouseEvent",
                       {"type": "mouseReleased", "x": x, "y": y, "button": "left",
                        "buttons": 0, "clickCount": 1})

    asyncio.run(go())


def main():
    port = int(sys.argv[1])
    needle = sys.argv[2]
    x, y = float(sys.argv[3]), float(sys.argv[4])

    t = pick(port, needle)
    if not t:
        print("未找到匹配 tab:", needle)
        for tt in http_targets(port):
            if tt.get("type") == "page":
                print("   ", (tt.get("url") or "")[:120])
        return 2

    print("tab:", (t.get("url") or "")[:110])
    click(t["webSocketDebuggerUrl"], x, y)
    print(f"已点击 @({x},{y})")
    time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())