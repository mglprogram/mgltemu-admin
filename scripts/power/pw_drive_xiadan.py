# -*- coding: utf-8 -*-
"""在调试 Chrome 里自动搜单 → 勾选 → 点「下单代发」，同时抓后端请求。

用户指定订单：PO-211-07257044664950105

流程（全部 CDP 驱动，用户无需操作）：
  ① 往 `textarea[name=deliver_no]` 写单号 → 触发 `searchOrder(1)`
  ② 等结果 → 勾第一行 checkbox（`set_checks` 等价）
  ③ 点「下单代发」→ `edit_order('xiadan_df')`
  ④ 不点最终确认（先看弹出的是哪个接口）
"""
import asyncio
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

PORT = 9222
PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07257044664950105"

JS_FILL = r"""(() => {
  const ta = document.querySelector('textarea[name=deliver_no], textarea[name="deliver_no"]');
  if (!ta) return JSON.stringify({err: 'no textarea',
    textareas: [...document.querySelectorAll('textarea')].map(t => t.name)});
  ta.value = %s;
  ta.dispatchEvent(new Event('input', {bubbles: true}));
  if (typeof searchOrder === 'function') { searchOrder(1); return JSON.stringify({ok: 'searchOrder'}); }
  const f = document.forms['searchForm'];
  if (f) { f.submit(); return JSON.stringify({ok: 'form.submit'}); }
  return JSON.stringify({err: 'no search fn'});
})()"""

JS_CHECK = r"""(() => {
  const boxes = [...document.querySelectorAll('input[name="ids[]"], input[name="ids"], input[type=checkbox]')]
    .filter(b => !b.disabled && b.type === 'checkbox');
  const rows = boxes.filter(b => b.closest('tr') && !/全选|check_?all/i.test(b.id + b.name));
  if (!rows.length) return JSON.stringify({err: 'no checkbox', total: boxes.length});
  const b = rows[0];
  if (!b.checked) b.click();
  const tr = b.closest('tr');
  return JSON.stringify({ok: 1, checked: b.checked, name: b.name, value: b.value,
                         text: (tr ? tr.innerText : '').replace(/\s+/g, ' ').slice(0, 160)});
})()"""

JS_XIADAN = r"""(() => {
  let hit = null;
  document.querySelectorAll('input[type=button], button').forEach((x) => {
    if (hit) return;
    if ((x.value || x.innerText || '').replace(/\s+/g, '') === '下单代发') hit = x;
  });
  if (!hit) return JSON.stringify({err: 'no button'});
  hit.click();
  return JSON.stringify({ok: 'clicked', onclick: hit.getAttribute('onclick')});
})()"""

JS_LAYER = r"""(() => {
  const l = document.querySelector('.layui-layer, .layer-content, #xubox_layer');
  return JSON.stringify({layer: !!l, text: l ? l.innerText.replace(/\s+/g,' ').slice(0,200) : ''});
})()"""


async def main():
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % PORT, timeout=10).read())
    tabs = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/list" % PORT, timeout=10).read())
    tab = next((t for t in tabs if "hwc_shop.php" in str(t.get("url") or "")), None)
    if not tab:
        print("没有帕沃尔订单页")
        return 1
    ws_url = tab["webSocketDebuggerUrl"]

    recs, pending = {}, {}
    async with websockets.connect(ver["webSocketDebuggerUrl"],
                                  max_size=100 * 1024 * 1024) as brows:
        bmid = [0]

        async def bsend(method, params=None, sid=None):
            bmid[0] += 1
            msg = {"id": bmid[0], "method": method, "params": params or {}}
            if sid:
                msg["sessionId"] = sid
            await brows.send(json.dumps(msg))

        await bsend("Target.setDiscoverTargets", {"discover": True})
        await asyncio.sleep(1)
        await bsend("Target.attachToTarget", {"targetId": tab["id"], "flatten": True})

        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            mid = [0]

            async def ev(expr):
                mid[0] += 1
                i = mid[0]
                await ws.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                                          "params": {"expression": expr,
                                                     "awaitPromise": True,
                                                     "returnByValue": True,
                                                     "userGesture": True}}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == i:
                        r = m.get("result") or {}
                        if r.get("exceptionDetails"):
                            return "EXC " + str(r["exceptionDetails"])[:160]
                        return (r.get("result") or {}).get("value")

            print("① 填单号并搜索：", await ev(JS_FILL % json.dumps(PO)))
            await asyncio.sleep(6)
            print("② 勾选首行：", await ev(JS_CHECK))
            await asyncio.sleep(1)
            print("③ 点「下单代发」：", await ev(JS_XIADAN))
            await asyncio.sleep(5)
            print("④ 弹层：", await ev(JS_LAYER))

        # 收抓到的请求
        end = time.time() + 6
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(brows.recv(), timeout=3)
            except asyncio.TimeoutError:
                break
            m = json.loads(raw)
            if m.get("method") == "Network.requestWillBeSent":
                r = (m.get("params") or {}).get("request") or {}
                u = r.get("url", "")
                if "powerwarehouse" in u and r.get("method") == "POST":
                    recs[u] = (r.get("postData") or "")[:600]
    for u, b in recs.items():
        print("\n★ POST %s" % u.replace("https://www.powerwarehouse.net", ""))
        print("   %s" % b[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
