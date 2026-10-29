# -*- coding: utf-8 -*-
"""把店铺窗口的商品列表页开出来（给写操作驱动准备一个**前台、已加载**的页面）。

用法：
    python scripts/goods/open_goods_list.py BHY          # 开/切到商品列表页
    python scripts/goods/open_goods_list.py BHY --state  # 只看当前状态
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_net  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}
URL = "https://agentseller.temu.com/goods/list"

JS_STATE = r"""
var trs = document.querySelectorAll('tbody tr');
var ins = Array.prototype.slice.call(document.querySelectorAll('input'))
  .filter(function (i) { return i.getClientRects().length; });
return JSON.stringify({
  url: location.href,
  mode: ins[0] ? ins[0].value : null,
  kw: ins[1] ? ins[1].value : null,
  rows: trs.length,
  codes: Array.prototype.slice.call(trs).map(function (t) {
    var m = (t.innerText || '').match(/货号：\s*([A-Za-z0-9\-]+)/);
    return m ? m[1] : null; }).filter(Boolean).slice(0, 5)
});
"""


async def main() -> int:
    store = (sys.argv[1] if len(sys.argv) > 1 else "BHY").upper()
    port = probe_port(WINDOWS[store])
    if not port:
        print("!! 探不到 %s 的 CDP 端口（紫鸟窗口要先打开）" % store)
        return 1
    # ★ 2026-10-29（「你自己打开的即用即关」的**明确例外**）：
    #   本脚本的唯一用途就是"把商品列表页开出来、让人在窗口里手工做筛选"，
    #   所以它开的页**故意留着**——关掉等于关掉自己的工作成果。
    #   这是给人用的开页工具，不是无人值守任务，不套 即用即关。
    tid = await cdp_net.new_tab(port, URL)  # noqa: tab-safety 工具脚本，页要留给人用
    print("新开了商品列表页 target=%s" % tid[:16])
    for _ in range(30):
        await asyncio.sleep(1)
        t = next((x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                  if (x.get("id") or x.get("targetId")) == tid), None)
        if t:
            break
    if not t:
        print("!! 拿不到 target")
        return 2
    async with cdp_net.NetCapture(t["webSocketDebuggerUrl"]) as cap:
        await cap.send("Page.bringToFront", {}, timeout=20)
        for i in range(15):
            await asyncio.sleep(3)
            r = await cap.send("Runtime.evaluate",
                               {"expression": JS_STATE, "returnByValue": True}, timeout=30)
            v = (r.get("result") or {}).get("value")
            if v:
                st = json.loads(v)
                print("第 %d 次探测: rows=%s" % (i + 1, st.get("rows")))
                if st.get("rows"):
                    print(json.dumps(st, ensure_ascii=False, indent=1))
                    break
        print("\n下一步（在紫鸟窗口里手工做，一次即可）：")
        print("  1) 左边条件下拉选「SKU货号」")
        print("  2) 输入款号（如 BHY-102204-6SetC）→ 点「查询」，让列表只剩目标款")
        print("  3) 回来跑：python scripts/goods/run_stock_topup.py --store %s --code <款号>"
              " [--apply]" % store)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
