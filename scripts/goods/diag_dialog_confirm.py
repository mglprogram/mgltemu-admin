# -*- coding: utf-8 -*-
"""只读诊断：①「修改库存」链接是否能找到 ②确认按钮的真实 DOM / React props。

不提交、不改数据，只 dump。
    python scripts/goods/diag_dialog_confirm.py --port 50748
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
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var out = {links: [], dlgs: [], btns: [], anchors: 0};
// 1) 全页所有「修改库存」可见元素
Array.prototype.slice.call(document.querySelectorAll('a,span,div,button')).forEach(function (e) {
  if (t(e) === '修改库存' && e.getClientRects().length) {
    var r = e.getBoundingClientRect();
    out.links.push({tag: e.tagName, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                    w: Math.round(r.width)});
  }
});
// 2) 弹窗候选（多种判据都报出来）
Array.prototype.slice.call(document.querySelectorAll('div')).forEach(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return;
  var r = d.getBoundingClientRect();
  if (!(r.width > 400 && r.height > 200)) return;
  var s = t(d);
  if (d.querySelectorAll('tbody tr').length < 1) return;
  out.dlgs.push({w: Math.round(r.width), h: Math.round(r.height),
                 rows: d.querySelectorAll('tbody tr').length,
                 hasGrow: s.indexOf('库存增减') >= 0,
                 hasCur: s.indexOf('当前库存') >= 0,
                 head: s.slice(0, 60)});
});
// 3) 所有可见按钮
Array.prototype.slice.call(document.querySelectorAll('button')).forEach(function (b) {
  if (!b.getClientRects().length) return;
  var r = b.getBoundingClientRect();
  out.btns.push({txt: t(b).slice(0, 8), x: Math.round(r.x + r.width / 2),
                 y: Math.round(r.y + r.height / 2), dis: !!b.disabled,
                 cls: String(b.className).slice(0, 30)});
});
out.anchors = document.querySelectorAll('a').length;
return JSON.stringify(out);"""


async def main(port: int):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    for p in pages:
        try:
            r = await asyncio.wait_for(
                cdp_net._one_shot(p["webSocketDebuggerUrl"], "Runtime.evaluate",
                                  {"expression": "(function(){\n%s\n})()" % JS,
                                   "returnByValue": True}), timeout=20)
            v = (r.get("result") or {}).get("value")
            print(p["id"][:8], p.get("url", "")[:50])
            try:
                d = json.loads(v or "{}")
            except Exception:
                print("  raw:", str(v)[:300])
                continue
            print("  修改库存链接:", len(d.get("links") or []), d.get("links", [])[:3])
            print("  弹窗候选:", json.dumps(d.get("dlgs") or [], ensure_ascii=False)[:400])
            print("  按钮:", json.dumps(d.get("btns") or [], ensure_ascii=False)[:600])
            break
        except Exception as e:
            print(p["id"][:8], "BAD", type(e).__name__)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    raise SystemExit(asyncio.run(main(ap.parse_args().port)))
