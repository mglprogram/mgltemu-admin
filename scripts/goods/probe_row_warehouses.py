# -*- coding: utf-8 -*-
"""只读：从商品列表每行的 React props 里挖 warehouseId / 发货模式 / 库存，判断能否不走弹框直接组接口参数。"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))
import cdp_net  # noqa

JS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
trs.forEach(function (tr, idx) {
  var sz = t(tr).match(/尺码[:：]\s*([A-Za-z0-9]+)/);
  var ck = Object.keys(tr).filter(function (k) { return k.indexOf('__reactFiber') === 0; })[0];
  if (!ck) return;
  var node = tr[ck], hops = 0, whs = [], modes = [], ids = [];
  while (node && hops < 10) {
    (function (props) {
      if (!props) return;
      var seen = 0;
      function walk(o, d) {
        if (d > 5 || o === null || typeof o !== 'object' || seen > 400) return;
        try {
          for (var k in o) {
            if (seen++ > 400) return;
            var v; try { v = o[k]; } catch (e) { continue; }
            if (typeof v === 'string') {
              if (/^WH-\d{6,}$/.test(v)) whs.push(v);
              else if (v === '卖家自发货' || v === '合作对接仓发货') modes.push(v);
            } else if (typeof v === 'number') {
              if (/productSkuId|^skuId$/.test(k)) ids.push(v);
            } else if (typeof v === 'object') {
              if (Array.isArray(v)) { for (var i = 0; i < Math.min(v.length, 6); i++) walk(v[i], d + 1); }
              else walk(v, d + 1);
            }
          }
        } catch (e) {}
      }
      walk(props, 0);
    })(node.memoizedProps);
    node = node.return; hops += 1;
  }
  function uniq(a) { var s = {}, r = []; a.forEach(function (x) { if (!s[x]) { s[x] = 1; r.push(x); } }); return r; }
  out.push({i: idx, size: sz ? sz[1] : '', whs: uniq(whs), modes: uniq(modes), ids: uniq(ids).slice(0, 3)});
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
                                                "returnByValue": True}, timeout=60)
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
