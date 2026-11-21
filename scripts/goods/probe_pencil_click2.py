# -*- coding: utf-8 -*-
"""诊断脚本：先关掉可能挡住的浮层（ESC），再点铅笔，看弹框到底出不出。"""
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

JS_OVERLAYS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
Array.prototype.forEach.call(document.querySelectorAll('div'), function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return;
  if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return;
  var r = d.getBoundingClientRect();
  if (r.width < 300 || r.height < 200) return;
  var s = t(d);
  if (!s) return;
  out.push({w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x),
            y: Math.round(r.y), text: s.slice(0, 60)});
});
return JSON.stringify(out.slice(0, 6));"""

JS_AFTER = r"""
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
var act = document.activeElement ? (document.activeElement.tagName + '|' + (document.activeElement.outerHTML || '').slice(0, 60)) : '';
return JSON.stringify({dlg: n, rows: rows, hasZr: !!window.__zr, active: act});"""


async def main_async(a) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("no page")
        return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        await cap.send("Page.bringToFront", {}, timeout=10)
        for _ in range(3):
            for tp in ("keyDown", "keyUp"):
                await cap.send("Input.dispatchKeyEvent",
                               {"type": tp, "key": "Escape", "code": "Escape",
                                "windowsVirtualKeyCode": 27}, timeout=10)
            await asyncio.sleep(0.4)
        print("overlays:", await SMB._ev(cap, JS_OVERLAYS))
        st = await SMB._search(cap, a.sku)
        print("search:", json.dumps(st, ensure_ascii=False))
        xy = await SMB._ev(cap, SMB.JS_PENCIL % json.dumps(a.sku))
        print("xy:", xy)
        if not str(xy).startswith("["):
            return 3
        await SMB._click(cap, json.loads(xy))
        for i in range(6):
            await asyncio.sleep(1.2)
            state = await SMB._ev(cap, JS_AFTER)
            print("after %ds:" % (i + 1), state)
            if '"dlg":1' in str(state) or '"dlg": 1' in str(state):
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
