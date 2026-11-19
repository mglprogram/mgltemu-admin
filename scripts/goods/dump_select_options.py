# -*- coding: utf-8 -*-
"""打开页面上的下拉（beast-core-select），把选项文本（和 React props 里的 id）都 dump 出来。"""
from __future__ import annotations
import argparse, asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))
import cdp_net  # noqa
import zreact  # noqa

JS_SELECTS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var ss = Array.prototype.slice.call(document.querySelectorAll('[data-testid="beast-core-select"]'));
return JSON.stringify(ss.map(function (s, i) {
  var h = s.querySelector('[data-testid="beast-core-select-header"]') || s;
  var r = h.getBoundingClientRect();
  return {i: i, txt: t(h).slice(0, 30), x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
}));"""

JS_CLICK_SEL = r"""
var i = %d;
var ss = Array.prototype.slice.call(document.querySelectorAll('[data-testid="beast-core-select"]'));
if (!ss[i]) return 'none';
var h = ss[i].querySelector('[data-testid="beast-core-select-header"]') || ss[i];
try { return window.__zr.call(h, 'onClick'); } catch (e) { return 'ERR:' + e; }"""

JS_OPTS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
var nodes = Array.prototype.slice.call(document.querySelectorAll(
  '[class*="select-option"], [data-testid*="option"], li[role="option"], [class*="Option"]'));
nodes.forEach(function (n) {
  if (!n.getClientRects().length) return;
  var s = t(n);
  if (!s || s.length > 40) return;
  out.push(s);
});
// 顺带找 React props 里的仓库 id
var whs = [];
var all = document.querySelectorAll('*');
for (var i = 0; i < all.length && whs.length < 20; i++) {
  var ck = Object.keys(all[i]).filter(function (k) { return k.indexOf('__reactFiber') === 0; })[0];
  if (!ck) continue;
  var node = all[i][ck], hops = 0;
  while (node && hops < 5) {
    try {
      var s2 = JSON.stringify(node.memoizedProps || {});
      if (/加州|kansas|WH-/.test(s2)) {
        var m = s2.match(/WH-\d{6,}/g); if (m) m.forEach(function (x) { whs.push(x); });
        var m2 = s2.match(/"warehouseName":"[^"]{1,24}"/g); if (m2) m2.slice(0, 4).forEach(function (x) { whs.push(x); });
      }
    } catch (e) {}
    node = node.return; hops += 1;
  }
}
return JSON.stringify({opts: Array.from(new Set(out)).slice(0, 30), wh: Array.from(new Set(whs)).slice(0, 20)});"""


async def ev(cap, js, timeout=40):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True},
                       timeout=timeout)
    if r.get("exceptionDetails"):
        return "EXC:" + str(r["exceptionDetails"])[:160]
    return (r.get("result") or {}).get("value")


async def main(a):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page"]
    tgt = [x for x in pages if a.url in (x.get("url") or "")]
    if not tgt:
        print("no matching page", [x.get("url") for x in pages])
        return 2
    cap = await cdp_net.NetCapture(tgt[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        await zreact.inject(cap)
        sel = json.loads(await ev(cap, JS_SELECTS) or "[]")
        print("selects:", json.dumps(sel, ensure_ascii=False), flush=True)
        for s in sel:
            if a.only is not None and s["i"] != a.only:
                continue
            print("click sel", s["i"], s["txt"], "→", await ev(cap, JS_CLICK_SEL % s["i"]), flush=True)
            await asyncio.sleep(2.0)
            print("  opts:", (await ev(cap, JS_OPTS))[:1200], flush=True)
            await asyncio.sleep(0.3)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--url", default="stock-edit")
    ap.add_argument("--only", type=int, default=None)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
