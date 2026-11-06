# -*- coding: utf-8 -*-
"""pw_accept_dialog.py — 接受/关闭帕沃尔页面上的原生 alert（阻塞 CDP）。

现象：点「查询」太快会弹原生 alert「您已提交查询操作,请勿重复点击,如长时间未响应请刷新页面重试」，
      它**阻塞页面 JS**，导致所有 `Runtime.evaluate` 超时。
处理：直接用裸 websocket 发 `Page.handleJavaScriptDialog {accept:true}`，
      不走 `CdpPage.evaluate`（那会被 alert 卡死）。
"""
import asyncio
import json
import sys

import websockets

PORT = 9333


def get_ws():
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % PORT, timeout=5) as r:
        lst = json.loads(r.read().decode("utf-8"))
    for t in lst:
        if t.get("type") == "page":
            return t["webSocketDebuggerUrl"], t.get("url", "")
    return None, ""


async def accept(ws_url):
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Page.enable", "params": {}}))
        try:
            await asyncio.wait_for(ws.recv(), timeout=3)
        except Exception:  # noqa: BLE001
            pass
        await ws.send(json.dumps({"id": 2, "method": "Page.handleJavaScriptDialog",
                                  "params": {"accept": True}}))
        try:
            m = await asyncio.wait_for(ws.recv(), timeout=5)
            print("resp:", m[:200])
        except Exception as e:  # noqa: BLE001
            print("no resp:", str(e)[:80])
        # 再探一次 JS 是否恢复
        await ws.send(json.dumps({"id": 3, "method": "Runtime.evaluate",
                                  "params": {"expression": "document.title",
                                             "returnByValue": True}}))
        try:
            m = await asyncio.wait_for(ws.recv(), timeout=5)
            print("title resp:", m[:200])
        except Exception as e:  # noqa: BLE001
            print("js still blocked:", str(e)[:80])


def main():
    ws_url, url = get_ws()
    print("url:", url[:100])
    if not ws_url:
        print("[FAIL] 没有可用 page target")
        return 1
    asyncio.run(accept(ws_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
