# -*- coding: utf-8 -*-
"""只读：从本店列表页「在售中」页签，把所有货号抓全（含翻页）。

前置：先用 `read_onsale_codes.py` 切到在售中页签；本脚本只负责把 94 条翻完。

用法：
    python scripts/goods/read_onsale_pages.py --port 50748 --store BHY --total 94
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import cdp_net  # noqa: E402

OUTDIR = ROOT / "data" / "store-db"

JS_CODES = r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var out = {codes: [], total: 0, active: ''};
Array.prototype.slice.call(document.querySelectorAll('tbody tr')).forEach(function (tr) {
  var m = t(tr).match(/货号[:：]\s*([A-Za-z0-9\-]+)/);
  if (m) out.codes.push(m[1]);
});
var tabs = t(document.body).match(/在售中\s*(\d+)/);
if (tabs) out.total = parseInt(tabs[1], 10);
var pg = Array.prototype.slice.call(document.querySelectorAll('li')).filter(function (e) {
  return /^\\d+$/.test(t(e)) && /active|selected|current/i.test(String(e.className)); })[0];
out.active = pg ? t(pg) : '';
return JSON.stringify(out);"""

JS_PAGER = r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var out = {next: null, nums: []};
Array.prototype.slice.call(document.querySelectorAll('li,button,a,span')).forEach(function (e) {
  if (!e.getClientRects().length) return;
  var s = t(e);
  if (/^\d+$/.test(s) && s.length <= 3) {
    var r = e.getBoundingClientRect();
    if (r.width > 8 && r.width < 80) out.nums.push({n: s, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)});
  }
});
return JSON.stringify(out);"""


async def ev(cap, js):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True}, timeout=40)
    return (r.get("result") or {}).get("value")


async def click(cap, x, y):
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1}, timeout=15)
        await asyncio.sleep(0.25)


async def main(a):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    cap = None
    for p in pages:
        try:
            await asyncio.wait_for(cdp_net._one_shot(p["webSocketDebuggerUrl"], "Runtime.evaluate",
                                                     {"expression": "1", "returnByValue": True}), timeout=8)
            cap = await cdp_net.NetCapture(p["webSocketDebuggerUrl"], match=()).__aenter__()
            break
        except Exception:
            continue
    if cap is None:
        print("没有可用列表页")
        return 2
    seen: list = []
    try:
        for page_no in range(1, 15):
            d = json.loads(await ev(cap, JS_CODES) or "{}")
            for c in d.get("codes") or []:
                if c not in seen:
                    seen.append(c)
            print("第 %d 页：本页 %d / 累计 %d / 总 %s"
                  % (page_no, len(d.get("codes") or []), len(seen), d.get("total")), flush=True)
            if d.get("total") and len(seen) >= int(d["total"]):
                break
            pg = json.loads(await ev(cap, JS_PAGER) or "{}")
            nums = {int(x["n"]): x for x in pg.get("nums") or []}
            want = page_no + 1
            target = nums.get(want)
            if not target:
                # 翻页按钮区：取 > 当前页号的最小数字
                bigger = [v for k, v in nums.items() if k > page_no]
                target = min(bigger, key=lambda z: int(z["n"])) if bigger else None
            if not target:
                print("  找不到下一页按钮，停止")
                break
            await click(cap, target["x"], target["y"])
            await asyncio.sleep(4)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / ("%s.onsale.txt" % a.store)
    out.write_text("\n".join(sorted(seen)) + "\n", encoding="utf-8")
    print("在售中货号 %d 个 → %s" % (len(seen), out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--store", required=True)
    x = ap.parse_args()
    raise SystemExit(asyncio.run(main(x)))
