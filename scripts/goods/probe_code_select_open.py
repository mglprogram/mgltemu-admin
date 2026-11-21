# -*- coding: utf-8 -*-
"""调试：点开「货号」那个下拉并把可见选项打出来，好知道能不能程序切到 SPU。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402

JS_FIND = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var ins = Array.prototype.slice.call(document.querySelectorAll('input')).filter(function (i) {
  var v = (i.value || '').trim();
  return i.getClientRects().length && (v === 'SPU' || v.indexOf('SKU') === 0); });
if (!ins.length) return 'no-input';
var el = ins[0];
var box = el.closest('[data-testid="beast-core-select"]') || el.parentElement.parentElement;
box.scrollIntoView({block: 'center'});
var r = box.getBoundingClientRect();
return JSON.stringify({xy: [Math.round(r.x + r.width/2), Math.round(r.y + r.height/2)],
                       tag: box.getAttribute('data-testid') || box.tagName,
                       text: t(box).slice(0, 40)});"""

JS_VISIBLE = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
Array.prototype.forEach.call(document.querySelectorAll('div,li,span'), function (e) {
  if (e.children.length) return;
  if (!e.getClientRects().length) return;
  var s = t(e);
  if (!s || s.length > 16) return;
  var r = e.getBoundingClientRect();
  if (r.width === 0) return;
  out.push({s: s, xy: [Math.round(r.x + r.width/2), Math.round(r.y + r.height/2)]});
});
var seen = {}, uniq = [];
out.forEach(function (o) { if (!seen[o.s]) { seen[o.s] = 1; uniq.push(o); } });
return JSON.stringify(uniq.slice(0, 40));"""


async def _ev(cap, js):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True},
                       timeout=30)
    return (r.get("result") or {}).get("value")


async def _click(cap, xy):
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1}, timeout=20)
        await asyncio.sleep(0.2)


async def main(port: int) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("no page")
        return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        await cap.send("Page.bringToFront", {}, timeout=10)
        info = await _ev(cap, JS_FIND)
        print("box:", info)
        if not str(info).startswith("{"):
            return 3
        d = json.loads(info)
        await _click(cap, d["xy"])
        await asyncio.sleep(1.2)
        vis = json.loads(await _ev(cap, JS_VISIBLE) or "[]")
        print("visible options:")
        for o in vis:
            print("   ", o)
        with open(Path(__file__).resolve().parents[2] / "_logs" / "code_select_options.json",
                  "w", encoding="utf-8") as f:
            json.dump(vis, f, ensure_ascii=False, indent=1)
        return 0
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 59670)))
