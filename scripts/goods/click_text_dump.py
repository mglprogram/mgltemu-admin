# -*- coding: utf-8 -*-
"""通用小工具：在页面里按文字点一个元素（React onClick 优先），等页面变化后 dump URL/文本/WH- id。"""
from __future__ import annotations
import argparse, asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))
import cdp_net  # noqa
import zreact  # noqa

JS_CLICK = r"""
var want = %s;
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var cands = Array.prototype.slice.call(document.querySelectorAll('a,span,div,li,button')).filter(function (e) {
  return t(e) === want && e.getClientRects().length; });
if (!cands.length) return 'none';
cands.sort(function (a, b) { return a.getBoundingClientRect().width - b.getBoundingClientRect().width; });
var el = cands[0];
try { return window.__zr.call(el, 'onClick'); } catch (e) { return 'ERR:' + e; }"""

JS_INFO = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var whs = [];
var html = document.documentElement.outerHTML;
var m = html.match(/WH-\d{6,}/g);
if (m) whs = m.slice(0, 10);
var found = 0;
var all = document.querySelectorAll('*');
for (var i = 0; i < all.length && found < 12; i++) {
  var ck = Object.keys(all[i]).filter(function (k) { return k.indexOf('__reactFiber') === 0; })[0];
  if (!ck) continue;
  var node = all[i][ck], hops = 0;
  while (node && hops < 6) {
    try {
      var s = JSON.stringify(node.memoizedProps || {});
      var mm = s.match(/WH-\d{6,}/g);
      if (mm) { mm.forEach(function (x) { whs.push(x); found += 1; }); }
      var nm = s.match(/"warehouseName":"[^"]{1,20}"/g);
      if (nm && found < 12) { nm.slice(0, 6).forEach(function (x) { whs.push(x); }); }
    } catch (e) {}
    node = node.return; hops += 1;
  }
}
return JSON.stringify({url: location.href, txt: t(document.body).slice(0, 400),
                       whAlpha: Array.from(new Set(whs)).slice(0, 20)});"""


async def ev(cap, js, timeout=40):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True},
                       timeout=timeout)
    if r.get("exceptionDetails"):
        return "EXC:" + str(r["exceptionDetails"])[:160]
    return (r.get("result") or {}).get("value")


async def main(a):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "agentseller" in (x.get("url") or "")]
    pages = [x for x in pages if "goods/list" in (x.get("url") or "")] or pages
    if not pages:
        print("no page"); return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        await zreact.inject(cap)
        print("click:", await ev(cap, JS_CLICK % json.dumps(a.text)), flush=True)
        await asyncio.sleep(a.wait)
        print(json.dumps(json.loads(await ev(cap, JS_INFO) or "{}"), ensure_ascii=False, indent=1)[:2000],
              flush=True)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--wait", type=float, default=8.0)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
