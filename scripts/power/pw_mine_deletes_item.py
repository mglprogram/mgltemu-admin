# -*- coding: utf-8 -*-
"""读 `deletes_item(oid)` 函数体 → 拿到「删除重新更新」的接口。

页面证据（2026-11-25）：
    <a onclick="deletes_item(3014155)">[删除重新更新]</a>

用它可以把被写坏的订单删掉并从平台重新拉取（地址恢复原样）。
"""
import asyncio
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

JS = r"""(() => {
  const out = {fn: typeof deletes_item};
  if (typeof deletes_item === 'function') out.src = deletes_item.toString();
  return JSON.stringify(out);
})()"""


async def main():
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9222/json/list", timeout=10).read())
    tab = next((t for t in tabs if "powerwarehouse" in str(t.get("url") or "")), None)
    if not tab:
        print("没有帕沃尔页")
        return 1
    async with websockets.connect(tab["webSocketDebuggerUrl"],
                                  max_size=64 * 1024 * 1024) as ws:
        mid = [0]

        async def call(m, p):
            mid[0] += 1
            i = mid[0]
            await ws.send(json.dumps({"id": i, "method": m, "params": p}))
            while True:
                x = json.loads(await ws.recv())
                if x.get("id") == i:
                    return x

        await call("Page.enable", {})
        await call("Page.navigate",
                   {"url": "https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1"})
        await asyncio.sleep(6)
        r = await call("Runtime.evaluate", {"expression": JS, "returnByValue": True})
        d = json.loads(((r.get("result") or {}).get("result") or {}).get("value") or "{}")
        print("类型:", d.get("fn"))
        print("\n=== deletes_item 函数体 ===\n")
        print(d.get("src"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
