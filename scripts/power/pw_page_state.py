# -*- coding: utf-8 -*-
"""看调试 Chrome 里帕沃尔订单页的当前状态：弹窗还在不在、勾了几行、提示文本。

用于判断「下单代发」是否已经点过确认。
"""
import asyncio
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

JS = r"""(() => {
  const t = (document.body.innerText || '').replace(/\s+/g, ' ');
  const boxes = [...document.querySelectorAll('input[type=checkbox]')];
  const checked = boxes.filter((b) => b.checked).length;
  const i = t.indexOf('下单');
  return JSON.stringify({
    checked: checked,
    boxes: boxes.length,
    around: i >= 0 ? t.slice(Math.max(0, i - 80), i + 160) : '(无「下单」字样)',
    head: t.slice(0, 200),
  });
})()"""


async def main():
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9222/json/list", timeout=10).read())
    tab = next((t for t in tabs if "hwc_shop.php" in str(t.get("url") or "")), None)
    if not tab:
        print("没有订单页")
        return 1
    async with websockets.connect(tab["webSocketDebuggerUrl"],
                                  max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": JS, "awaitPromise": True,
                                             "returnByValue": True}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 1:
                r = m.get("result") or {}
                if r.get("exceptionDetails"):
                    print("EXC", str(r["exceptionDetails"])[:200])
                    return 1
                print(json.dumps(json.loads((r.get("result") or {}).get("value") or "{}"),
                                 ensure_ascii=False, indent=1))
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
