# -*- coding: utf-8 -*-
"""挖订单编辑页 `tijiao()` 函数：换 Fnsku 后提交到哪个接口、带什么字段。

页面结构（2026-11-25 实测）：
    表单 name="myform"，`<input type="button" value="提交" onclick="tijiao()">`
    Fnsku 字段：`input[name="ck_sku[]"]`
"""
import asyncio
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

JS = r"""(() => {
  const out = {fn: typeof tijiao};
  if (typeof tijiao === 'function') out.src = tijiao.toString();
  const sku = [...document.querySelectorAll('input[name="ck_sku[]"]')].map((e) => e.value);
  out.sku = sku;
  const f = document.forms['myform'];
  if (f) {
    out.action = f.action; out.method = f.method;
    out.names = [...new Set([...f.querySelectorAll('[name]')].map(e => e.name))];
  }
  return JSON.stringify(out);
})()"""


async def main():
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9222/json/list", timeout=10).read())
    tab = next((t for t in tabs if "act=edit_order" in str(t.get("url") or "")), None) \
        or next((t for t in tabs if "powerwarehouse" in str(t.get("url") or "")), None)
    if not tab:
        print("没有帕沃尔编辑页")
        return 1
    print("页面:", str(tab.get("url"))[:90])
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
                print("当前 Fnsku:", d.get("sku"))
                print("form action:", d.get("action"), "| method:", d.get("method"))
                print("form 字段:", d.get("names"))
                print("\n=== tijiao() 函数体 ===\n")
                print(d.get("src"))
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
