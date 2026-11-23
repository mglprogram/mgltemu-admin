# -*- coding: utf-8 -*-
"""自己点开「修改库存」弹窗（不走 bel_stock_set.open_stock_dialog），验证提交按钮为什么点不动。

流程：SPU 查询 → 悬停目标行 → 点「修改库存」→ dump 弹窗结构（含「确认」按钮的 React props）
不填数、不提交。

    python scripts/goods/diag_open_dialog.py --port 50748 --code BHY-102105-3BGR
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

JS_ROWS = r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var out = [];
Array.prototype.slice.call(document.querySelectorAll('tbody tr')).forEach(function (tr) {
  var s = t(tr);
  if (s.indexOf(%s) < 0) return;
  var r = tr.getBoundingClientRect();
  var a = Array.prototype.slice.call(tr.querySelectorAll('a')).filter(function (e) {
    return t(e) === '修改库存'; })[0];
  var ar = a ? a.getBoundingClientRect() : null;
  out.push({y: Math.round(r.y + r.height / 2),
            ax: ar ? Math.round(ar.x + ar.width / 2) : null,
            ay: ar ? Math.round(ar.y + ar.height / 2) : null});
});
return JSON.stringify(out);"""

JS_DUMP = r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var out = {dlgs: [], btns: []};
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 400 && r.height > 200 && d.querySelectorAll('tbody tr').length > 0;
});
boxes.sort(function (a, b) { return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length; });
if (!boxes.length) return JSON.stringify(out);
var d = boxes[0];
out.dlg = {rows: d.querySelectorAll('tbody tr').length, head: t(d).slice(0, 80)};
Array.prototype.slice.call(d.querySelectorAll('button')).forEach(function (b) {
  var r = b.getBoundingClientRect();
  var props = [];
  try {
    var k = Object.keys(b).filter(function (x) { return x.indexOf('__reactProps') === 0; })[0];
    if (k) props = Object.keys(b[k] || {});
  } catch (e) {}
  out.btns.push({txt: t(b), x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                 dis: !!b.disabled, props: props, cross: b.getAttribute('data-testid') || ''});
});
return JSON.stringify(out);"""


async def main(port: int, code: str):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    for p in pages:
        try:
            await asyncio.wait_for(cdp_net._one_shot(p["webSocketDebuggerUrl"], "Runtime.evaluate",
                                                     {"expression": "1", "returnByValue": True}), timeout=8)
            break
        except Exception:
            continue
    cap = await cdp_net.NetCapture(p["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        await zreact.inject(cap)
        rows = json.loads(await _ev(cap, JS_ROWS % json.dumps(code)) or "[]")
        print("命中行:", rows)
        if not rows:
            print("页面上没有这个货号的行")
            return 2
        r0 = rows[0]
        # 悬停出行内操作
        for tp in ("mouseMoved",):
            await cap.send("Input.dispatchMouseEvent", {"type": tp, "x": 400, "y": r0["y"],
                                                       "button": "none"}, timeout=15)
        await asyncio.sleep(1.5)
        rows = json.loads(await _ev(cap, JS_ROWS % json.dumps(code)) or "[]")
        r0 = rows[0] if rows else r0
        print("链接坐标:", r0)
        for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
            await cap.send("Input.dispatchMouseEvent", {"type": tp, "x": r0["ax"], "y": r0["ay"],
                                                       "button": "left", "clickCount": 1}, timeout=15)
            await asyncio.sleep(0.25)
        await asyncio.sleep(4)
        v = await _ev(cap, JS_DUMP)
        print("弹窗:", str(v)[:1500])
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    return 0


async def _ev(cap, js):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True}, timeout=40)
    return (r.get("result") or {}).get("value")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--code", required=True)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.port, a.code)))
