# -*- coding: utf-8 -*-
"""读 `xiadan_df()` 函数本体 —— 这里才是真正发请求的地方。

`edit_order('xiadan_df')` 只是 confirm + 调 `xiadan_df()`：
    if(obj == 'xiadan_df') {
      if(!confirm('您确认下单代发吗?下单成功后将不可取消')) return false;
      xiadan_df();
    }
"""
import asyncio
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

JS = r"""(() => {
  const out = {fn: typeof xiadan_df};
  if (typeof xiadan_df === 'function') out.src = xiadan_df.toString();
  // 顺带看看有没有相关的其它函数
  out.cands = Object.keys(window).filter((k) => /xiadan|df_|shipping|shipping_label/i.test(k)
    && typeof window[k] === 'function').slice(0, 20);
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
                print("xiadan_df 类型:", d.get("fn"))
                print("相关函数:", d.get("cands"))
                print("\n=== 函数体 ===\n")
                print(d.get("src"))
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
