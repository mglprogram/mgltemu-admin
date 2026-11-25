# -*- coding: utf-8 -*-
"""挖 `auto_update_ship_newsx` 的**前半段** —— 那里 `$.post/$.ajax` 的 URL 和参数。

上一版只打印了尾部（回调里更新 UI 的部分）。这次：
  ① 全文长度 + 所有 `$.post` / `$.ajax` / `$.get` 调用点
  ② 打印每个调用前后 400 字
"""
import asyncio
import json
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

JS = r"""(() => {
  const src = (typeof auto_update_ship_newsx === 'function')
    ? auto_update_ship_newsx.toString() : '';
  return JSON.stringify({len: src.length, src: src});
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
                d = json.loads((r.get("result") or {}).get("value") or "{}")
                src = d.get("src") or ""
                print("函数长度:", d.get("len"))
                for kw in ("$.post", "$.ajax", "$.get", "url:", "act:"):
                    for mm in re.finditer(re.escape(kw), src):
                        i = mm.start()
                        print("\n--- %s @%d ---" % (kw, i))
                        print(src[max(0, i - 300):i + 500])
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
