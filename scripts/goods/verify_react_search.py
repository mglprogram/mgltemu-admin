# -*- coding: utf-8 -*-
"""验证：第二行筛选器「SKU(精准查询)」填**完整 SKU 货号**（如 BHY-102204-6SetC-S）应能筛出 1 行。

此前一直失败的原因推测：该下拉默认「精准查询」，而我填的是**款号**
（`102204-6SetC` / `BHY-102204-6SetC`）—— 精准匹配不到任何 SKU 货号，于是返回全量。

只读：只做筛选 + 观察。

用法：python scripts/goods/verify_react_search.py BHY BHY-102204-6SetC-S
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

URL = "https://agentseller.temu.com/goods/list"
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}

#: 第二行的输入框（k=6）与它的「查询」按钮
JS_STATE = """
var ins = Array.prototype.slice.call(document.querySelectorAll('input'));
var vis = ins.filter(function (i) { return i.getClientRects().length; });
return JSON.stringify({values: vis.map(function (i) { return i.value; }).slice(0, 12),
  rows: document.querySelectorAll('tbody tr').length});
"""

JS_FILL_ROW2 = """
var kw = %s;
var ins = Array.prototype.slice.call(document.querySelectorAll('input'))
  .filter(function (i) { return i.getClientRects().length; });
if (ins.length < 7) return 'not-enough-inputs:' + ins.length;
var el = ins[6];   /* 第二行 SKU(精准查询) 的值输入框 */
return window.__zr.write(el, kw);
"""

JS_ROWS = """
var trs = document.querySelectorAll('tbody tr');
var codes = [];
Array.prototype.forEach.call(trs, function (t) {
  var s = t.innerText || '';
  var i = s.indexOf('货号：');
  if (i >= 0) {
    var tail = s.slice(i + 3);
    var nl = tail.indexOf(String.fromCharCode(10));
    codes.push(nl > 0 ? tail.slice(0, nl) : tail.slice(0, 21));
  }
});
return JSON.stringify({rows: trs.length, codes: codes.slice(0, 6)});
"""


async def main() -> int:
    store = (sys.argv[1] if len(sys.argv) > 1 else "BHY").upper()
    kw = sys.argv[2] if len(sys.argv) > 2 else "BHY-102204-6SetC-S"
    port = probe_port(WINDOWS[store])
    tid = await cdp_net.new_tab(port, URL, foreground=True)
    tgt = None
    for _ in range(30):
        await asyncio.sleep(1)
        tgt = next((x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                    if (x.get("id") or x.get("targetId")) == tid), None)
        if tgt:
            break
    async with cdp_net.NetCapture(tgt["webSocketDebuggerUrl"]) as cap:
        for _ in range(20):
            await asyncio.sleep(3)
            if await zreact.call_js(cap, "return String(document.querySelectorAll"
                                        "('tbody tr').length);", timeout=60) != "0":
                break
        await zreact.inject(cap)
        print("初始:", await zreact.call_js(cap, JS_STATE, timeout=60))
        print("① 写第二行筛选框:", await zreact.call_js(cap, JS_FILL_ROW2 % json.dumps(kw),
                                                     timeout=60))
        print("   现在:", await zreact.call_js(cap, JS_STATE, timeout=60))
        print("② 点查询:", await zreact.call_js(
            cap, "return window.__zr.call(window.__zr.btn('查询'), 'onClick');", timeout=60))
        for i in range(6):
            await asyncio.sleep(2)
            st = json.loads(await zreact.call_js(cap, JS_ROWS, timeout=60) or "{}")
            if st.get("rows") not in (0, 51, 70):
                break
        print("③ 结果:", await zreact.call_js(cap, JS_ROWS, timeout=60))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
