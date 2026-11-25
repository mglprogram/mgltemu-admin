# -*- coding: utf-8 -*-
"""挖 `tijiao()` 里 `$.ajax` 的 url / data —— 换 Fnsku 提交的真实接口。

只要找到 `url:` 后面那串，就知道 POST 到哪、带哪些字段。
顺带打印 `ck_sku` 相关构造（`$('.ck_sku')` 的取值方式）。
"""
import asyncio
import json
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

JS = r"""(() => {
  const src = (typeof tijiao === 'function') ? tijiao.toString() : '';
  return JSON.stringify({len: src.length, src: src});
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
                src = json.loads(((m.get("result") or {}).get("result") or {})
                                 .get("value") or "{}").get("src") or ""
                print("函数长度:", len(src))
                for kw in ("url:", "$.ajax", "ck_sku", "serialize", "data:"):
                    for mm in list(re.finditer(re.escape(kw), src))[:2]:
                        i = mm.start()
                        print("\n--- %s @%d ---" % (kw, i))
                        print(src[max(0, i - 220):i + 420])
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
