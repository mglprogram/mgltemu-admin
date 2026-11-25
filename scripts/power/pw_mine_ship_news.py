# -*- coding: utf-8 -*-
"""挖 `auto_update_ship_news(ids_list)` —— 真正逐批下单的地方。

调用链已查明（2026-11-25 真机）：
    edit_order('xiadan_df')
      → confirm('您确认下单代发吗?...')
      → xiadan_df()                       ← 收集勾选 ids，按 5 个一组切成 ids_list
          → auto_update_ship_news(ids_list)   ← **这里才是发请求的**
"""
import asyncio
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

JS = r"""(() => {
  const out = {fn: typeof auto_update_ship_news};
  if (typeof auto_update_ship_news === 'function') {
    out.src = auto_update_ship_news.toString();
  }
  out.cands = Object.keys(window).filter(
    (k) => /ship_news|update_ship|xiadan|waybill/i.test(k) && typeof window[k] === 'function'
  ).slice(0, 30);
  return JSON.stringify(out);
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
                print("类型:", d.get("fn"))
                print("相关函数:", d.get("cands"))
                print("\n=== auto_update_ship_news 函数体 ===\n")
                print(d.get("src"))
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
