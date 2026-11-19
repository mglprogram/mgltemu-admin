# -*- coding: utf-8 -*-
"""只读：读商品列表每行的 货号 / 尺码 / 发货模式 / 库存。"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))
import cdp_net  # noqa

JS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = {code: '', skuBox: '', rows: []};
var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
trs.forEach(function (tr) {
  var s = t(tr);
  var m = s.match(/货号[:：]\s*([A-Za-z0-9\-]+)/);
  if (m && !out.code) out.code = m[1];
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var sz = s.match(/尺码[:：]\s*([A-Za-z0-9]+)/);
  var mode = '';
  cells.forEach(function (c) { if (c === '合作对接仓发货' || c === '卖家自发货') mode = c; });
  if (sz || mode) out.rows.push({size: sz ? sz[1] : '', spu: !!m, mode: mode,
                                 cells: cells.slice(0, 7)});
});
Array.prototype.slice.call(document.querySelectorAll('input')).forEach(function (i) {
  var ph = i.getAttribute('placeholder') || '';
  if (ph.indexOf('多个查询请空格或逗号依次输入') >= 0 && !out.skuBox && (i.value || '').trim()) out.skuBox = (i.value || '').trim();
});
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
