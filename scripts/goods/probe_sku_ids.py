# -*- coding: utf-8 -*-
"""只读：把当前列表页每个 SKU 行的所有单元格按索引打印，用于确认哪一列是 productSkuId。"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))
import cdp_net  # noqa

JS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
Array.prototype.slice.call(document.querySelectorAll('tbody tr')).forEach(function (tr) {
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var nums = cells.filter(function (c) { return /^\d{6,}$/.test(c); });
  var sz = t(tr).match(/尺码[:：]\s*([A-Za-z0-9]+)/);
  out.push({size: sz ? sz[1] : '', nums: nums, n: cells.length});
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
