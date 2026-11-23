# -*- coding: utf-8 -*-
"""只读诊断：在「修改库存」弹窗里模拟一次完整提交，抓 POST 请求体，判断提交为什么没生效。

只针对指定货号，做完立即取消弹窗（不写后台）。输出请求 URL / payload / 响应。

用法：
    python scripts/goods/probe_stock_submit.py --port 50748 --code BHY-203805-3BHC
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import bel_stock_set as B  # noqa: E402
import cdp_net  # noqa: E402

DUMP = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = {dlgs: [], btns: [], inputs: []};
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 300 && d.querySelectorAll('tbody tr').length > 0;
});
boxes.forEach(function (d) { out.dlgs.push({n: d.querySelectorAll('tbody tr').length, txt: t(d).slice(-120)}); });
Array.prototype.slice.call(document.querySelectorAll('button')).forEach(function (x) {
  if (!x.getClientRects().length) return;
  var r = x.getBoundingClientRect();
  out.btns.push([t(x).slice(0, 10), Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2), !!x.disabled]);
});
Array.prototype.slice.call(document.querySelectorAll('input')).forEach(function (i) {
  if (!i.getClientRects().length) return;
  out.inputs.push([i.placeholder || '', (i.value || '').slice(0, 8), !!i.readOnly]);
});
return JSON.stringify(out);"""

JS_SUBMIT = r"""
var t = function (e) { return (e.innerText || '').trim(); };
var bs = Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (x) {
  return t(x) === '确认' && x.getClientRects().length;
});
if (!bs.length) return 'no-confirm-btn';
var out = [];
bs.forEach(function (b, i) {
  var r = b.getBoundingClientRect();
  out.push(i + ':' + Math.round(r.x + r.width / 2) + ',' + Math.round(r.y + r.height / 2) +
           (b.disabled ? ':disabled' : ''));
});
return out.join(' | ');"""


async def main(port: int, code: str):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("没有商品列表页")
        return 2
    pid = pages[-1]["id"]
    tab = B.OwnTab(port, pid)
    tab.reuse_tid = pid
    tab.any_page_ok = True
    await tab.open()
    await tab.ensure()
    await B.page_ready(tab, 90)
    await B.close_overlays(tab)
    if await B.dialog_open(tab):
        await B.close_dialog(tab)
    print("查询类型:", await B.pick_query_type(tab, "SKU"))
    print("搜索:", await B.search(tab, code))
    print("开弹窗:", await B.open_stock_dialog(tab, code))
    print("等弹窗:", await B.wait_dialog(tab, 15))
    r = await tab.session.eval(DUMP, timeout=30)
    raw = r[0] if isinstance(r, tuple) else r
    print("--- 弹窗态 ---")
    print(str(raw)[:900])
    print("--- 确认按钮 ---")
    # 用带抓包的连接执行一次提交并抓请求
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"],
                                   match=("stock", "inventory", "sku")).__aenter__()
    try:
        await cap.send("Network.enable", {"maxPostDataSize": 8 * 1024 * 1024}, timeout=20)
        print(await (await cap.eval(JS_SUBMIT, timeout=20))[0])
        c, _ = await cap.eval(JS_SUBMIT.replace("return out.join(' | ');", "return 'ok';"), timeout=20)
        print("点确认:", c)
    finally:
        await asyncio.sleep(8)
        print("--- 抓到的请求 ---")
        for h in cap.hits:
            print("URL:", h.get("url", "")[:120])
            print("  REQ:", (h.get("req") or "")[:600])
            print("  RES:", (h.get("body") or "")[:400])
        await cap.__aexit__(None, None, None)
    if await B.dialog_open(tab):
        print("收尾: 关弹窗", await B.close_dialog(tab))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--code", required=True)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.port, a.code)))
