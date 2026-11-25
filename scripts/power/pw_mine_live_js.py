# -*- coding: utf-8 -*-
"""从**调试 Chrome 已加载的页面**里直接读 `edit_order` 函数的源码。

比下载 JS 文件更可靠：页面里的函数就是**实际执行的**那一份（含内联脚本）。
做法：`edit_order.toString()` → 打印全文，并单独挑出 `xiadan_df` 分支。
"""
import asyncio
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

JS = r"""(() => {
  if (typeof edit_order !== 'function') return JSON.stringify({err: 'no edit_order'});
  const src = edit_order.toString();
  // 找 xiadan_df 分支
  const i = src.indexOf('xiadan_df');
  const seg = i >= 0 ? src.slice(Math.max(0, i - 1200), i + 1800) : '(没找到 xiadan_df)';
  return JSON.stringify({len: src.length, hasXiadan: i >= 0, seg: seg});
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
                    print("EXC", str(r["exceptionDetails"])[:300])
                    return 1
                d = json.loads((r.get("result") or {}).get("value") or "{}")
                print("函数长度:", d.get("len"), "| 含 xiadan_df:", d.get("hasXiadan"))
                print("\n=== xiadan_df 附近源码 ===\n")
                print(d.get("seg"))
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
