# -*- coding: utf-8 -*-
"""看某个 SKU 那一行「发货模式」单元格里有没有铅笔图标（0 库存的款可能不给改）。"""
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
var sku = %s;
var tr = Array.prototype.slice.call(document.querySelectorAll('tbody tr')).filter(function (r) {
  return t(r).indexOf(sku) >= 0; })[0];
if (!tr) return 'no-row';
var cells = Array.prototype.slice.call(tr.querySelectorAll('td'));
var out = {n: cells.length, mode: '', cellHtml: '', icons: 0, disabled: 0};
cells.forEach(function (c) {
  var s = t(c);
  if (s === '合作对接仓发货' || s === '卖家自发货') { out.mode = s; out.cellHtml = c.innerHTML.slice(0, 900); }
});
out.icons = tr.querySelectorAll('[data-testid="beast-core-icon-edit"]').length;
out.disabled = tr.querySelectorAll('[disabled], [aria-disabled="true"], [class*="disabled"]').length;
out.rowText = t(tr).slice(0, 200);
return JSON.stringify(out);"""


async def main(port: int, sku: str) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("no page")
        return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        r = await cap.send("Runtime.evaluate",
                           {"expression": "(function(){\n%s\n})()" % (JS % json.dumps(sku)),
                            "returnByValue": True}, timeout=40)
        v = (r.get("result") or {}).get("value")
        print(v)
        return 0
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    ap = sys.argv
    raise SystemExit(asyncio.run(main(int(ap[1]) if len(ap) > 1 else 59670,
                                      ap[2] if len(ap) > 2 else "")))
