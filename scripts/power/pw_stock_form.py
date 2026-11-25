# -*- coding: utf-8 -*-
"""抓库存页 `stock.php?act=list` 的查询表单参数名 + 分页。

做法：在调试 Chrome（9222）里打开库存页，**只读**页面结构：
  ① form 的 action / method / 所有 input name（找出 Seller sku 输入框叫什么）
  ② 分页控件（`page` / `p` / `gotoPage`）
  ③ 顺带看「查询」按钮的 onclick 函数
"""
import asyncio
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

URL = "https://www.powerwarehouse.net/stock.php?act=list"

JS = r"""(() => {
  const out = {};
  out.url = location.href;
  out.forms = [...document.forms].map((f) => ({
    a: f.getAttribute('action'), m: f.method, n: f.getAttribute('name'),
    inputs: [...f.querySelectorAll('input,select')].map((e) => ({
      n: e.name || e.id, t: e.type, v: (e.value || '').slice(0, 40)})).slice(0, 30)
  }));
  out.buttons = [...document.querySelectorAll('input[type=button],button,input[type=submit]')]
    .map((e) => ({v: (e.value || e.innerText || '').trim(), oc: e.getAttribute('onclick') || ''}))
    .filter((x) => x.v).slice(0, 15);
  // 分页
  const pg = document.body.innerText.match(/共\s*\d+\s*[页条]|每页\s*\d+|第\s*\d+\s*页/g);
  out.page = pg ? pg.slice(0, 6) : [];
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

        async def call(method, params):
            mid[0] += 1
            i = mid[0]
            await ws.send(json.dumps({"id": i, "method": method, "params": params}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == i:
                    return m

        await call("Page.enable", {})
        await call("Page.navigate", {"url": URL})
        await asyncio.sleep(7)
        r = await call("Runtime.evaluate", {"expression": JS, "returnByValue": True})
        d = json.loads(((r.get("result") or {}).get("result") or {}).get("value") or "{}")
        print(json.dumps(d, ensure_ascii=False, indent=1)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
