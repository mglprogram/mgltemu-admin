# -*- coding: utf-8 -*-
"""定位目标款并打开「修改库存」——**不筛选**版：先把每页调到 200，再按款号找。

用户 2026-10-12 提示：每页可选到 500。调到 200 后「在售中 88 个商品」一页装得下
（列表有虚拟渲染，行数会随滚动变化，所以滚动到底再扫一遍）。

用法：python scripts/goods/verify_row_lookup.py BHY BHY-102204-6SetC
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_net  # noqa: E402
import set_page_size as S  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

URL = "https://agentseller.temu.com/goods/list"
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}

JS_FIND_CODE = """
var code = %s;
var all = Array.prototype.slice.call(document.querySelectorAll('*'));
var cands = all.filter(function (e) {
  return (e.innerText || '').indexOf(code) >= 0 && e.getClientRects().length > 0;
});
return JSON.stringify({rows: document.querySelectorAll('tbody tr').length,
                       hits: cands.length});
"""

JS_CLICK_EDIT = """
var code = %s;
var all = Array.prototype.slice.call(document.querySelectorAll('*'));
var cands = all.filter(function (e) {
  return (e.innerText || '').indexOf(code) >= 0 && e.getClientRects().length > 0;
});
if (!cands.length) return 'not-found';
cands.sort(function (a, b) { return (a.innerText || '').length - (b.innerText || '').length; });
var node = cands[0], link = null, hops = 0;
while (node && hops < 12) {
  var ls = Array.prototype.slice.call(node.querySelectorAll('a,span,div,button'))
    .filter(function (e) { return (e.innerText || '').trim() === '修改库存'; });
  if (ls.length) { link = ls[0]; break; }
  node = node.parentElement; hops++;
}
if (!link) return 'no-link';
return 'edit: ' + window.__zr.call(link, 'onClick');
"""

JS_DIALOG = """
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null;
  try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 400 && r.height > 250;
});
var box = boxes[boxes.length - 1];
if (!box) return JSON.stringify({ok: false});
var t = (box.innerText || '').split(String.fromCharCode(10)).join(' ');
var i = t.indexOf('货号：');
return JSON.stringify({ok: true, sku: i >= 0 ? t.slice(i, i + 26) : null,
                       rows: box.querySelectorAll('tbody tr').length,
                       hasInput: box.querySelectorAll('input').length});
"""


async def main() -> int:
    store = (sys.argv[1] if len(sys.argv) > 1 else "BHY").upper()
    code = sys.argv[2] if len(sys.argv) > 2 else "BHY-102204-6SetC"
    port = probe_port(WINDOWS[store])
    # foreground=True 是技术必需（后台页被节流）；但必须自己关掉（即用即关）。
    tid = await cdp_net.new_tab(port, URL, foreground=True)  # noqa: tab-safety 前台是技术必需（后台页被节流、Runtime 挂住），已配 finally close_tab
    tgt = None
    try:
        for _ in range(30):
            await asyncio.sleep(1)
            tgt = next((x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                        if (x.get("id") or x.get("targetId")) == tid), None)
            if tgt:
                break
        if not tgt:
            print("!! 拿不到新页 target")
            return 2
        async with cdp_net.NetCapture(tgt["webSocketDebuggerUrl"]) as cap:
            for _ in range(20):
                await asyncio.sleep(3)
                if await zreact.call_js(cap, "return String(document.querySelectorAll"
                                            "('tbody tr').length);", timeout=60) != "0":
                    break
            await zreact.inject(cap)
            print("① 每页调 200:", await S.set_page_size(cap, "200"))
            await asyncio.sleep(2)
            info = json.loads(await zreact.call_js(cap, JS_FIND_CODE % json.dumps(code),
                                                  timeout=60) or "{}")
            print("   扫描:", info)
            if not info.get("hits"):
                print("   滚到底再扫……")
                await zreact.call_js(cap, S.JS_SCROLL_BOTTOM, timeout=60)
                await asyncio.sleep(3)
                info = json.loads(await zreact.call_js(cap, JS_FIND_CODE % json.dumps(code),
                                                      timeout=60) or "{}")
                print("   扫描:", info)
            if not info.get("hits"):
                print("❌ 200/页 后仍看不到该款（可能被虚拟渲染裁掉）")
                return 2
            print("② 点它的「修改库存」:", await zreact.call_js(cap, JS_CLICK_EDIT
                                                              % json.dumps(code), timeout=60))
            await asyncio.sleep(6)
            print("③ 弹窗:", await zreact.call_js(cap, JS_DIALOG, timeout=60))
        return 0
    finally:
        if tid:
            try:
                await cdp_net.close_tab(port, tid)      # 用完就关
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
