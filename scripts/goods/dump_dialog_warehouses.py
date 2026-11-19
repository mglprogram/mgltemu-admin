# -*- coding: utf-8 -*-
"""弹框出现时，从 React fiber 里挖出每个发货仓的 warehouseId（不提交任何东西）。

用法：
    python scripts/goods/dump_dialog_warehouses.py --port 59670 --timeout 1800
    # 看到 line 里出现 warehouses 后按 Ctrl+C；结果同时写 _logs/dlg_warehouses.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402

LOGS = Path(__file__).resolve().parents[2] / "_logs"
OUT = LOGS / "dlg_warehouses.json"

JS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var dlg = null;
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 300 && /发货模式/.test(t(d)) && d.querySelectorAll('tbody tr').length > 0;
});
if (!boxes.length) return 'closed';
boxes.sort(function (a, b) {
  return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length; });
dlg = boxes[0];

var found = [];
function walk(o, depth, path) {
  if (depth > 6 || o === null || typeof o !== 'object' || found.length > 200) return;
  try {
    for (var k in o) {
      if (found.length > 200) return;
      var v;
      try { v = o[k]; } catch (e) { continue; }
      if (typeof v === 'string') {
        if (/^WH-\d{6,}$/.test(v)) found.push({path: path + '.' + k, wh: v});
        else if (/warehouseId|warehouseName|stockNum/i.test(k) && v.length < 40)
          found.push({path: path + '.' + k, v: v});
      } else if (typeof v === 'number') {
        if (/warehouseId|stockNum|stock/i.test(k)) found.push({path: path + '.' + k, n: v});
      } else if (typeof v === 'object' && depth < 6) {
        if (Array.isArray(v)) { for (var i = 0; i < Math.min(v.length, 8); i++) walk(v[i], depth + 1, path + '.' + k + '[' + i + ']'); }
        else walk(v, depth + 1, path + '.' + k);
      }
    }
  } catch (e) {}
}
var trs = Array.prototype.slice.call(dlg.querySelectorAll('tbody tr'));
trs.forEach(function (tr, idx) {
  var ck = Object.keys(tr).filter(function (k) { return k.indexOf('__reactFiber') === 0 || k.indexOf('__reactProps') === 0; })[0];
  if (!ck) return;
  var node = tr[ck];
  var hops = 0;
  while (node && hops < 8) {
    if (node.memoizedProps) walk(node.memoizedProps, 0, 'row' + idx + '.memoizedProps');
    node = node.return; hops += 1;
  }
});
var uniq = [];
var seen = {};
found.forEach(function (f) { var s = JSON.stringify(f); if (!seen[s]) { seen[s] = 1; uniq.push(f); } });
return JSON.stringify({rows: trs.map(t), hits: uniq.slice(0, 120)});"""


async def ev(cap, js, timeout=40):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True},
                       timeout=timeout)
    if r.get("exceptionDetails"):
        return "EXC:" + str(r["exceptionDetails"])[:200]
    return (r.get("result") or {}).get("value")


async def main_async(a) -> int:
    end = time.time() + a.timeout
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("没有商品列表页")
        return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    print("等弹框…", flush=True)
    try:
        last = None
        while time.time() < end:
            v = await ev(cap, JS)
            if str(v).startswith("{"):
                d = json.loads(v)
                whs = [h for h in d.get("hits", []) if h.get("wh")]
                if whs:
                    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
                    print(json.dumps(d, ensure_ascii=False)[:1500], flush=True)
                    print("→ 已写 %s" % OUT, flush=True)
                    return 0
                if v != last:
                    print("弹框出现但还没挖到 WH- id：%s" % json.dumps(d.get("rows"), ensure_ascii=False)[:300],
                          flush=True)
                    last = v
            await asyncio.sleep(0.4)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    print("超时", flush=True)
    return 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=1800)
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
