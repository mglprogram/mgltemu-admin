# -*- coding: utf-8 -*-
"""守着已打开的「发货模式」弹框，自动完成：切卖家自发货 → 按弹框里的原库存填两行 → 三层确认。

用户在页面上点铅笔打开弹框后，本脚本会把该弹框做完（不需知道是哪个 SKU，
原库存直接读弹框里「合作对接仓发货」那行的当前库存）。

用法：
    python scripts/goods/finish_open_dialog.py --port 59670 --timeout 900 [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402

JS_STATE = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 300 && /发货模式/.test(t(d)) && d.querySelectorAll('tbody tr').length > 0;
});
if (!boxes.length) return 'closed';
boxes.sort(function (a, b) {
  return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length; });
window.__dlg = boxes[0];
var rows = Array.prototype.slice.call(window.__dlg.querySelectorAll('tbody tr'));
var mode = '', base = '';
rows.forEach(function (tr) {
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  cells.forEach(function (s) {
    if (s === '合作对接仓发货' || s === '卖家自发货') mode = s;
  });
  if (!base) {
    var ins = tr.querySelectorAll('input');
    if (ins.length) base = ins[0].value;
  }
});
return JSON.stringify({mode: mode, base: base, rows: rows.length,
                       cells: document.querySelectorAll('[id*="stockNum"]').length});"""

JS_OPEN_SEL = r"""
if (!window.__dlg) return 'no-dlg';
var s = window.__dlg.querySelector('[data-testid="beast-core-select"]');
if (!s) return 'no-sel';
var h = s.querySelector('[data-testid="beast-core-select-header"]') || s;
return window.__zr.call(h, 'onClick');"""

JS_SEL_XY = r"""
if (!window.__dlg) return 'no-dlg';
var s = window.__dlg.querySelector('[data-testid="beast-core-select"]');
if (!s) return 'no-sel';
var h = s.querySelector('[data-testid="beast-core-select-header"]') || s;
var r = h.getBoundingClientRect();
return JSON.stringify([Math.round(r.x+r.width/2), Math.round(r.y+r.height/2)]);"""

JS_OPT = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var c = Array.prototype.slice.call(document.querySelectorAll('div,li,span')).filter(function (e) {
  return !e.children.length && t(e) === '卖家自发货' && e.getClientRects().length; });
if (!c.length) return 'none';
var r = c[c.length - 1].getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width/2), Math.round(r.y + r.height/2)]);"""

JS_CELLS = r"""
var boxes = Array.prototype.slice.call(document.querySelectorAll('[id*="stockNum"]'));
return JSON.stringify(boxes.map(function (b) {
  var el = b.closest('td') || b;
  var r = el.getBoundingClientRect();
  return [Math.round(r.x + r.width/2), Math.round(r.y + r.height/2)];
}));"""

JS_BTN = r"""
var t = function (e) { return (e.innerText || '').trim(); };
var pat = %s;
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  return new RegExp(pat).test(d.innerText || '') && d.querySelectorAll('button').length > 0; });
if (!boxes.length) return 'none';
boxes.sort(function (a, b) { return a.getBoundingClientRect().width - b.getBoundingClientRect().width; });
var b = Array.prototype.slice.call(boxes[0].querySelectorAll('button')).filter(function (x) {
  return t(x) === '确认'; })[0];
if (!b) return 'no-btn';
var r = b.getBoundingClientRect();
return JSON.stringify([Math.round(r.x+r.width/2), Math.round(r.y+r.height/2)]);"""

JS_STOCK_VALS = r"""
var boxes = Array.prototype.slice.call(document.querySelectorAll('[id*="stockNum"]'));
return JSON.stringify(boxes.map(function (b) {
  var i = b.querySelector('input'); return i ? i.value : ''; }));"""


async def _ev(cap, js, timeout=40):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js,
                        "returnByValue": True}, timeout=timeout)
    if r.get("exceptionDetails"):
        return "EXC:" + str(r["exceptionDetails"])[:140]
    return (r.get("result") or {}).get("value")


async def _click(cap, xy):
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1},
                       timeout=20)
        await asyncio.sleep(0.2)


async def main_async(a) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("没有商品列表页")
        return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        await zreact.inject(cap)
        st = "closed"
        for _ in range(a.timeout):
            st = await _ev(cap, JS_STATE)
            if str(st).startswith("{"):
                break
            await asyncio.sleep(1)
        if not str(st).startswith("{"):
            print("超时：没等到弹框")
            return 3
        d = json.loads(st)
        base = int(d.get("base") or 0)
        print("弹框:", d, "→ 基准库存", base)
        if d.get("mode") == "卖家自发货" and d.get("cells"):
            print("已是卖家自发货，直接填")
        else:
            for attempt in range(4):
                if attempt % 2 == 0:
                    await _ev(cap, JS_OPEN_SEL)
                else:
                    xy = await _ev(cap, JS_SEL_XY)
                    if str(xy).startswith("["):
                        await _click(cap, json.loads(xy))
                await asyncio.sleep(1.3)
                opt = await _ev(cap, JS_OPT)
                if not str(opt).startswith("["):
                    continue
                await _click(cap, json.loads(opt))
                await asyncio.sleep(2.5)
                await _ev(cap, JS_STATE)
                cells = await _ev(cap, JS_CELLS)
                if str(cells).startswith("[") and cells != "[]":
                    break
        cells = json.loads(await _ev(cap, JS_CELLS) or "[]")
        if not cells:
            print("库存格没出现")
            return 4
        ca = max(1, base - 1) if len(cells) >= 2 else max(1, base)
        vals = [ca, 1] if len(cells) >= 2 else [max(1, base)]
        print("填:", vals, "格数", len(cells))
        # 逐格：点一下聚焦 → insertText → Tab
        for k, c in enumerate(cells[:2]):
            v = vals[k] if k < len(vals) else vals[-1]
            await _click(cap, c)
            await asyncio.sleep(0.5)
            await cap.send("Input.insertText", {"text": str(v)}, timeout=20)
            await asyncio.sleep(0.4)
            await cap.send("Input.dispatchKeyEvent",
                           {"type": "keyDown", "key": "Tab", "windowsVirtualKeyCode": 9},
                           timeout=10)
            await asyncio.sleep(0.3)
        print("填后:", await _ev(cap, JS_STOCK_VALS))
        if not a.apply:
            print("(未提交；--apply 才点确认)")
            return 0
        for pat in ("当前库存", "确认修改发货模式", "建议SKU下每个发货仓"):
            c = await _ev(cap, JS_BTN % json.dumps(pat))
            if str(c).startswith("["):
                await _click(cap, json.loads(c))
                print("确认:", pat[:8])
            await asyncio.sleep(3)
        await asyncio.sleep(15)
        print("完成一次切换（请用列表回读核对）")
        return 0
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--apply", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
