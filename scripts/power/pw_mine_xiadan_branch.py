# -*- coding: utf-8 -*-
"""直接看 `edit_order` 里 `xiadan_df` **分支自己那一段**（精确定位到 if 块）。

上一版截取范围太宽，落到了 `huishou`/`pl_shanchu` 分支。
这次按 `obj == 'xiadan_df'` 定位，向后取到下一个 `obj ==` 为止。
同时把 `xiadan_df` 全部出现位置都打出来。
"""
import asyncio
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

JS = r"""(() => {
  const src = (typeof edit_order === 'function') ? edit_order.toString() : '';
  const out = {len: src.length, hits: []};
  const re = /obj\s*===?\s*['"]([a-z_0-9]+)['"]/g;
  let m;
  while ((m = re.exec(src))) out.hits.push(m[1]);
  const i = src.search(/obj\s*===?\s*['"]xiadan_df['"]/);
  if (i >= 0) {
    const rest = src.slice(i + 10);
    const nx = rest.search(/obj\s*===?\s*['"]/);
    out.seg = src.slice(i, i + 10 + (nx > 0 ? nx : 2500));
  }
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
                print("函数长度:", d.get("len"))
                print("所有分支:", d.get("hits"))
                print("\n=== xiadan_df 分支 ===\n")
                print(d.get("seg"))
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
