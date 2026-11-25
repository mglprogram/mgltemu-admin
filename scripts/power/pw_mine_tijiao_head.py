# -*- coding: utf-8 -*-
"""挖 `tijiao()` 的**前半段**：`$.ajax({url: ...})` 到底提交到哪。

上一版只打印了尾部回调。这次打印 src 开头 1800 字 + 所有 `url:` / `dataType`。
"""
import asyncio
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

JS = r"""(() => {
  const src = (typeof tijiao === 'function') ? tijiao.toString() : '';
  return JSON.stringify({len: src.length, head: src.slice(0, 2200), src: src});
})()"""


async def main():
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9222/json/list", timeout=10).read())
    tab = next((t for t in tabs if "act=edit_order" in str(t.get("url") or "")), None) \
        or next((t for t in tabs if "powerwarehouse" in str(t.get("url") or "")), None)
    if not tab:
        print("没有帕沃尔编辑页")
        return 1
    async with websockets.connect(tab["webSocketDebuggerUrl"],
                                  max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": JS, "awaitPromise": True,
                                             "returnByValue": True}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 1:
                d = json.loads(((m.get("result") or {}).get("result") or {})
                               .get("value") or "{}")
                print("函数长度:", d.get("len"))
                print("\n=== 前半段 ===\n")
                print(d.get("head"))
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
