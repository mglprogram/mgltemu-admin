# -*- coding: utf-8 -*-
"""诊断：不靠坐标，直接用 zreact 触发铅笔图标的 onClick/onMouseDown，看能不能弹出「发货模式」弹框。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import switch_mode_batch as SMB  # noqa: E402
import zreact  # noqa: E402

JS_FIRE = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var sku = %s;
var tr = Array.prototype.slice.call(document.querySelectorAll('tbody tr')).filter(function (r) {
  return t(r).indexOf(sku) >= 0; })[0];
if (!tr) return 'no-row';
var cell = Array.prototype.slice.call(tr.querySelectorAll('td')).filter(function (td) {
  var s = t(td); return s === '合作对接仓发货' || s === '卖家自发货'; })[0];
if (!cell) return 'no-cell';
var svg = cell.querySelector('[data-testid="beast-core-icon-edit"]');
if (!svg) return 'no-icon';
var tries = [];
function fire(el, name) {
  if (!el || !window.__zr) return;
  var p = window.__zr.findProps(el);
  if (p && typeof p[name] === 'function') {
    try { window.__zr.call(el, name); tries.push(name); } catch (e) { tries.push(name + ':ERR'); }
    return;
  }
  var cur = el.parentElement, hops = 0;
  while (cur && hops < 4) {
    var p2 = window.__zr.findProps(cur);
    if (p2 && typeof p2[name] === 'function') {
      try { window.__zr.call(cur, name); tries.push(name + '@' + hops); } catch (e) { tries.push(name + ':ERR'); }
      return;
    }
    cur = cur.parentElement; hops += 1;
  }
}
fire(svg, 'onClick');
fire(svg.parentElement, 'onClick');
fire(svg, 'onMouseDown');
return JSON.stringify({ok: true, tries: tries});"""

JS_STATE = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var n = 0, rows = 0;
Array.prototype.forEach.call(document.querySelectorAll('div'), function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return;
  var r = d.getBoundingClientRect();
  if (r.width < 260) return;
  if (t(d).indexOf('发货模式') < 0) return;
  n += 1; rows = Math.max(rows, d.querySelectorAll('tbody tr').length);
});
return JSON.stringify({dlg: n, rows: rows});"""


async def main_async(a) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        await cap.send("Page.bringToFront", {}, timeout=10)
        await zreact.inject(cap)
        st = await SMB._search(cap, a.sku)
        print("search:", json.dumps(st, ensure_ascii=False))
        print("fire:", await SMB._ev(cap, JS_FIRE % json.dumps(a.sku)))
        for i in range(6):
            await asyncio.sleep(1.2)
            print("after %ds:" % (i + 1), await SMB._ev(cap, JS_STATE))
            if '"dlg":1' in str(await SMB._ev(cap, JS_STATE)):
                return 0
        return 4
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=59670)
    ap.add_argument("--sku", required=True)
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))
