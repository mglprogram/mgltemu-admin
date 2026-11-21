# -*- coding: utf-8 -*-
"""只读探针：看商品列表页「货号」那个下拉里有哪些选项（SKU 模糊查询 / SPU 等）。

    python scripts/goods/probe_code_select.py --port 59670
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

JS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = {selects: [], options: [], inputs: []};
Array.prototype.forEach.call(document.querySelectorAll('[data-testid="beast-core-select"]'), function (s) {
  var txt = t(s).slice(0, 40);
  if (txt) out.selects.push(txt);
});
Array.prototype.forEach.call(document.querySelectorAll('[class*="cIL_item"], [class*="select-item"], li'), function (e) {
  if (e.getClientRects().length && !e.children.length) {
    var s = t(e);
    if (s && s.length <= 24) out.options.push(s);
  }
});
Array.prototype.forEach.call(document.querySelectorAll('input'), function (i) {
  if (i.getClientRects().length) out.inputs.push({ph: i.placeholder || '', val: (i.value || '').slice(0, 40)});
});
out.options = Array.from(new Set(out.options)).slice(0, 30);
return JSON.stringify(out);"""


async def main(port: int) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("没有商品列表页")
        return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        r = await cap.send("Runtime.evaluate",
                           {"expression": "(function(){\n%s\n})()" % JS, "returnByValue": True},
                           timeout=40)
        v = (r.get("result") or {}).get("value")
        print(v if not v else json.dumps(json.loads(v), ensure_ascii=False, indent=1))
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=59670)
    raise SystemExit(asyncio.run(main(ap.parse_args().port)))
