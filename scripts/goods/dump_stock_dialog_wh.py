# -*- coding: utf-8 -*-
"""尝试从「修改库存」弹框里挖出各发货仓的 warehouseId（只读：只打开弹框，不提交）。

思路：发货模式铅笔点不开，但「修改库存」是文字链，React onClick 也许能打开；
它同样按仓展示库存，因此 React props 里应该带 warehouseId。
"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))
import cdp_net  # noqa
import zreact  # noqa

JS_CLICK = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var cands = Array.prototype.slice.call(document.querySelectorAll('a,span,div,button')).filter(function (e) {
  return t(e) === '修改库存' && e.getClientRects().length; });
if (!cands.length) return 'none';
cands.sort(function (a, b) { return t(a).length - t(b).length; });
var el = cands[0];
try { return window.__zr.call(el, 'onClick'); } catch (e) { return 'ERR:' + e; }"""

JS_DUMP = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 300 && /库存/.test(t(d)) && d.querySelectorAll('tbody tr').length > 0;
});
if (!boxes.length) return 'closed';
boxes.sort(function (a, b) {
  return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length; });
var dlg = boxes[0];
var found = [];
function walk(o, depth, path) {
  if (depth > 6 || o === null || typeof o !== 'object' || found.length > 300) return;
  try {
    for (var k in o) {
      if (found.length > 300) return;
      var v; try { v = o[k]; } catch (e) { continue; }
      if (typeof v === 'string') {
        if (/^WH-\d{6,}$/.test(v)) found.push([path + '.' + k, v]);
        else if (/warehouseName/i.test(k)) found.push([path + '.' + k, v]);
      } else if (typeof v === 'object' && depth < 6) {
        if (Array.isArray(v)) { for (var i = 0; i < Math.min(v.length, 6); i++) walk(v[i], depth + 1, path + '.' + k + '[' + i + ']'); }
        else walk(v, depth + 1, path + '.' + k);
      }
    }
  } catch (e) {}
}
var trs = Array.prototype.slice.call(dlg.querySelectorAll('tbody tr'));
trs.forEach(function (tr, idx) {
  var ck = Object.keys(tr).filter(function (k) { return k.indexOf('__reactFiber') === 0; })[0];
  if (!ck) return;
  var node = tr[ck], hops = 0;
  while (node && hops < 8) {
    if (node.memoizedProps) walk(node.memoizedProps, 0, 'r' + idx);
    node = node.return; hops += 1;
  }
});
return JSON.stringify({rows: trs.map(t), found: found.slice(0, 80)});"""


async def ev(cap, js, timeout=40):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True},
                       timeout=timeout)
    if r.get("exceptionDetails"):
        return "EXC:" + str(r["exceptionDetails"])[:160]
    return (r.get("result") or {}).get("value")


async def main(port):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("no page"); return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        await zreact.inject(cap)
        print("点 修改库存:", await ev(cap, JS_CLICK), flush=True)
        for i in range(10):
            await asyncio.sleep(1.5)
            v = await ev(cap, JS_DUMP)
            if str(v).startswith("{"):
                d = json.loads(v)
                whs = [f for f in d.get("found", []) if str(f[1]).startswith("WH-")]
                print(json.dumps(d, ensure_ascii=False)[:1200], flush=True)
                if whs:
                    print("WH ids:", whs, flush=True)
                    break
        else:
            print("没挖到 warehouseId", flush=True)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 59670)))
