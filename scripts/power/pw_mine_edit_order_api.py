# -*- coding: utf-8 -*-
"""挖「订单编辑页换 Fnsku」的提交接口（用户 2026-11-25 指定的失败单处理方法）。

用户流程：
    stock.php?act=list → 直接换 sku → 编辑 → 把原来的 sku 覆盖换成有库存的 → 再次下单代发

已知：`pw_hwc_flow.fix_one_sku` 走 `hwc_shop.php?act=edit_order&id=<oid>`，
      把 `input[name="ck_sku[]"]` 覆盖成新 Fnsku，然后点「提交」。

本脚本：在调试 Chrome（9222）里打开订单编辑页，**只读**挖出：
  ① `<form>` 的 action / 全部 input 名字（提交目标）
  ② `ck_sku[]` 的当前值
  ③ 页面上的「提交」按钮 onclick
"""
import asyncio
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

OID = sys.argv[1] if len(sys.argv) > 1 else ""

JS = r"""(() => {
  const out = {url: location.href};
  const f = document.forms[0];
  if (f) {
    out.formAction = f.getAttribute('action');
    out.formName = f.getAttribute('name');
    out.inputs = [...f.querySelectorAll('input,select,textarea')].map((e) => ({
      n: e.name || e.id, t: e.type || e.tagName.toLowerCase(), v: (e.value || '').slice(0, 60)
    })).slice(0, 40);
  }
  out.forms = [...document.forms].map((x) => ({a: x.getAttribute('action'), n: x.getAttribute('name')}));
  out.buttons = [...document.querySelectorAll('input[type=button],input[type=submit],button')]
    .map((e) => ({v: e.value || e.innerText, oc: e.getAttribute('onclick') || ''}))
    .filter((x) => x.v).slice(0, 15);
  return JSON.stringify(out);
})()"""


async def main():
    if not OID:
        print("用法: pw_mine_edit_order_api.py <订单id>")
        return 1
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:9222/json/list", timeout=10).read())
    tab = next((t for t in tabs if "hwc_shop.php" in str(t.get("url") or "")), None)
    if not tab:
        print("没有帕沃尔页，先开一个")
        tabs = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:9222/json/new?https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1",
            timeout=10).read()) if False else tabs
    url = "https://www.powerwarehouse.net/hwc_shop.php?act=edit_order&id=%s" % OID
    ws_url = (tab or {}).get("webSocketDebuggerUrl")
    if not ws_url:
        print("拿不到页面，先用 pw_local_chrome_probe 开页")
        return 1
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
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
        await call("Page.navigate", {"url": url})
        await asyncio.sleep(6)
        r = await call("Runtime.evaluate", {"expression": JS, "returnByValue": True})
        d = json.loads(((r.get("result") or {}).get("result") or {}).get("value") or "{}")
        print(json.dumps(d, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
