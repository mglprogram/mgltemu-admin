# -*- coding: utf-8 -*-
"""看页面上有没有开着的弹框（发货模式那类），把它的文字打出来，判断为什么点铅笔没弹。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402

JS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
Array.prototype.forEach.call(document.querySelectorAll('div'), function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return;
  var r = d.getBoundingClientRect();
  if (r.width < 200 || r.height < 60) return;
  var s = t(d);
  if (!s) return;
  var hit = /发货模式|库存|无可|提示|确认/.test(s);
  if (hit) out.push({w: Math.round(r.width), h: Math.round(r.height),
                     fixed: st.position === 'fixed', text: s.slice(0, 240)});
});
return JSON.stringify({n: out.length, boxes: out.slice(0, 8), body: t(document.body).slice(0, 400)});"""


async def main(port: int) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("no page")
        return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        r = await cap.send("Runtime.evaluate",
                           {"expression": "(function(){\n%s\n})()" % JS, "returnByValue": True},
                           timeout=30)
        print((r.get("result") or {}).get("value"))
        return 0
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 59670)))
