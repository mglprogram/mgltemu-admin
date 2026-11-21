# -*- coding: utf-8 -*-
"""诊断：点某 SKU 那格的铅笔，看坐标对不对、点完有没有弹框（只读，不提交）。"""
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

JS_PROBE = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var boxes = [];
Array.prototype.forEach.call(document.querySelectorAll('div'), function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return;
  var r = d.getBoundingClientRect();
  if (r.width < 260 || r.height < 80) return;
  var s = t(d);
  if (!s || s.indexOf('发货模式') < 0) return;
  boxes.push({w: Math.round(r.width), h: Math.round(r.height), rows: d.querySelectorAll('tbody tr').length,
              text: s.slice(0, 160)});
});
var win = {vw: window.innerWidth, vh: window.innerHeight, dpr: window.devicePixelRatio,
           scrollY: window.scrollY};
return JSON.stringify({boxes: boxes, win: win});"""


async def main_async(a) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("no page")
        return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        await cap.send("Page.bringToFront", {}, timeout=10)
        st = await SMB._search(cap, a.sku)
        print("search:", json.dumps(st, ensure_ascii=False))
        xy = await SMB._ev(cap, SMB.JS_PENCIL % json.dumps(a.sku))
        print("pencil xy:", xy)
        print("before:", await SMB._ev(cap, JS_PROBE))
        if not str(xy).startswith("["):
            return 3
        await SMB._click(cap, json.loads(xy))
        await asyncio.sleep(4)
        print("after :", await SMB._ev(cap, JS_PROBE))
        return 0
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
