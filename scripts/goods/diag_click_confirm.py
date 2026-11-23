# -*- coding: utf-8 -*-
"""在已打开的「修改库存」弹窗上：填一个尺码的增减 → 用**真实鼠标**点确认 → 观察弹窗是否关闭。

用途：验证 confirm() 为什么点了没反应。
    python scripts/goods/diag_click_confirm.py --port 50748
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

JS_FIND = r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 400 && r.height > 200 && d.querySelectorAll('tbody tr').length > 0;
});
if (!boxes.length) return JSON.stringify({open: false});
boxes.sort(function (a, b) { return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length; });
var d = boxes[0];
var btn = Array.prototype.slice.call(d.querySelectorAll('button')).filter(function (b) {
  return t(b) === '确认'; })[0];
var br = btn ? btn.getBoundingClientRect() : null;
return JSON.stringify({open: true, rows: d.querySelectorAll('tbody tr').length,
  bt: btn ? [Math.round(br.x + br.width / 2), Math.round(br.y + br.height / 2)] : null});"""


async def _ev(cap, js):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True}, timeout=40)
    if r.get("exceptionDetails"):
        return "EXC:" + str(r["exceptionDetails"])[:120]
    return (r.get("result") or {}).get("value")


async def main(port: int, use_detail: bool):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    cap = None
    for p in pages:
        try:
            await asyncio.wait_for(cdp_net._one_shot(p["webSocketDebuggerUrl"], "Runtime.evaluate",
                                                    {"expression": "1", "returnByValue": True}), timeout=8)
            cap = await cdp_net.NetCapture(p["webSocketDebuggerUrl"], match=()).__aenter__()
            break
        except Exception:
            continue
    if cap is None:
        print("没有可用页")
        return 2
    try:
        await zreact.inject(cap)
        info = json.loads(await _ev(cap, JS_FIND) or "{}")
        print("弹窗状态:", info)
        if not info.get("open") or not info.get("bt"):
            print("弹窗没开或没找到确认按钮")
            return 3
        x, y = info["bt"]
        print("点确认 @", x, y)
        seq = [("mouseMoved", {}), ("mousePressed", {"buttons": 1, "clickCount": 1}),
               ("mouseReleased", {"buttons": 0, "clickCount": 1})]
        for tp, extra in seq:
            params = {"type": tp, "x": x, "y": y, "button": "left"}
            params.update(extra)
            await cap.send("Input.dispatchMouseEvent", params, timeout=15)
            await asyncio.sleep(0.3)
        await asyncio.sleep(5)
        after = json.loads(await _ev(cap, JS_FIND) or "{}")
        print("点后:", after)
        return 0 if not after.get("open") else 4
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.port, a.detail)))
