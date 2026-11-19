# -*- coding: utf-8 -*-
"""只读：把「发货模式」弹框的完整文本与列表首行文本 dump 出来，用于定位 SKU 来源。"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))
import cdp_net  # noqa

JS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = {searchKit: [], dlgText: '', dlgRows: [], firstRow: '', skcTexts: []};
Array.prototype.slice.call(document.querySelectorAll('input')).forEach(function (i) {
  var ph = i.getAttribute('placeholder') || '';
  out.searchKit.push({ph: ph, v: i.value || ''});
});
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 300 && /发货模式/.test(t(d)) && d.querySelectorAll('tbody tr').length > 0;
});
if (boxes.length) {
  boxes.sort(function (a, b) { return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length; });
  var d = boxes[0];
  out.dlgText = t(d).slice(0, 600);
  out.dlgRows = Array.prototype.slice.call(d.querySelectorAll('tbody tr')).map(function (tr) {
    return Array.prototype.slice.call(tr.querySelectorAll('td, th')).map(t);
  });
}
var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
if (trs.length) out.firstRow = t(trs[0]).slice(0, 400);
out.rowCount = trs.length;
return JSON.stringify(out);"""


async def main(port):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("no page"); return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        r = await cap.send("Runtime.evaluate", {"expression": "(function(){\n%s\n})()" % JS,
                                                "returnByValue": True}, timeout=40)
        v = (r.get("result") or {}).get("value")
        try:
            print(json.dumps(json.loads(v), ensure_ascii=False, indent=1))
        except Exception:
            print(v)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 59670)))
