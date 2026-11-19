# -*- coding: utf-8 -*-
"""把商品列表「货号」前面那个下拉切到 **SPU**（用户 2026-11-19：按 SPU 查，不用查 SKU）。

    python scripts/goods/ensure_spu_mode.py --port 59670           # 只看当前是什么
    python scripts/goods/ensure_spu_mode.py --port 59670 --set     # 切到 SPU
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

JS_STATE = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var vals = [];
Array.prototype.forEach.call(document.querySelectorAll('input'), function (i) {
  if (!i.getClientRects().length) return;
  var v = (i.value || '').trim();
  if (v === 'SPU' || v.indexOf('SKU') >= 0 || v.indexOf('货号') >= 0) vals.push({val: v, ph: i.placeholder || ''});
});
return JSON.stringify(vals);"""

JS_OPEN = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var ins = Array.prototype.slice.call(document.querySelectorAll('input')).filter(function (i) {
  var v = (i.value || '').trim();
  return i.getClientRects().length && (v === 'SPU' || v === 'SKU(模糊查询)' || v.indexOf('SKU') === 0); });
if (!ins.length) return 'no-select';
ins[0].scrollIntoView({block: 'center'});
var r = ins[0].getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);"""

JS_OPT_SPU = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var c = Array.prototype.slice.call(document.querySelectorAll('div,li,span')).filter(function (e) {
  return !e.children.length && e.getClientRects().length && t(e) === 'SPU'; });
if (!c.length) return 'none';
var r = c[c.length - 1].getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);"""


async def _ev(cap, js, timeout=30):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True},
                       timeout=timeout)
    return (r.get("result") or {}).get("value")


async def _click(cap, xy):
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1}, timeout=20)
        await asyncio.sleep(0.2)


async def ensure_spu_mode(cap, do_set: bool = True, log=print) -> str:
    """返回 'SPU' / 其他当前值。`do_set=True` 时若不是 SPU 就点开下拉选 SPU。"""
    try:
        vals = json.loads(await _ev(cap, JS_STATE) or "[]")
    except Exception:
        vals = []
    cur = vals[0]["val"] if vals else ""
    log("  货号下拉当前 = %r" % cur)
    if cur == "SPU" or not do_set:
        return cur
    for _ in range(3):
        xy = await _ev(cap, JS_OPEN)
        if not str(xy).startswith("["):
            continue
        await _click(cap, json.loads(xy))
        await asyncio.sleep(1.0)
        opt = await _ev(cap, JS_OPT_SPU)
        if str(opt).startswith("["):
            await _click(cap, json.loads(opt))
            await asyncio.sleep(1.0)
            vals = json.loads(await _ev(cap, JS_STATE) or "[]")
            cur = vals[0]["val"] if vals else ""
            log("  切换后 = %r" % cur)
            if cur == "SPU":
                return cur
    return cur


async def main_async(a) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("没有商品列表页")
        return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        await cap.send("Page.bringToFront", {}, timeout=10)
        cur = await ensure_spu_mode(cap, do_set=a.set)
        print("cur =", cur)
        return 0 if (cur == "SPU" or not a.set) else 3
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=59670)
    ap.add_argument("--set", action="store_true")
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))
