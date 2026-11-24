# -*- coding: utf-8 -*-
"""从店铺商品列表页实读「在售中」的货号清单（只读筛选，不点出数据）。

为什么必须实读：`goods.json` 里 BHY/BEL 全被标成「在售中」（93/116 个），
但它其实是「全部」页签的导出，夹带了未发布到站点 / 已下架 / 已终止的款。
用户 2026-11-23 明确：**只修改在售中的库存**。

做法：
    1. 打开（或复用）本店列表页，切到「在售中」页签；
    2. 把每页行里的「货号：XXXX」抓出来，翻页直到收全；
    3. 落盘 `data/store-db/<STORE>.onsale.txt`。

用法：
    python scripts/goods/read_onsale_codes.py --port 50748 --store BHY
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import bel_stock_set as B  # noqa: E402
import cdp_net  # noqa: E402
import zreact  # noqa: E402

OUTDIR = ROOT / "data" / "store-db"
LIST_URL = "https://agentseller.temu.com/goods/list"

JS_TAB = r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var el = Array.prototype.slice.call(document.querySelectorAll('div,span,a,li')).filter(function (e) {
  return /^在售中\s*\d+$/.test(t(e)) && e.getClientRects().length; })[0];
if (!el) return 'no-tab';
var r = el.getBoundingClientRect();
return JSON.stringify({xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)],
                       txt: t(el)});"""

JS_CODES = r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var out = {codes: [], total: 0, page: ''};
Array.prototype.slice.call(document.querySelectorAll('tbody tr')).forEach(function (tr) {
  var m = t(tr).match(/货号[:：]\s*([A-Za-z0-9\-]+)/);
  if (m) out.codes.push(m[1]);
});
var tabs = t(document.body).match(/在售中\s*(\d+)/);
if (tabs) out.total = parseInt(tabs[1], 10);
var pg = t(document.body).match(/每页\s*(\d+)/);
if (pg) out.page = pg[1];
return JSON.stringify(out);"""

JS_NEXT = r"""
var t = function (e) { return (e.innerText || '').trim(); };
var b = Array.prototype.slice.call(document.querySelectorAll('button,li,a')).filter(function (e) {
  return /^(下一页|Next|›|>)$/.test(t(e)) && e.getClientRects().length && !e.disabled; })[0];
if (!b) return 'no-next';
var r = b.getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);"""


async def ev(cap, js, timeout=40):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True},
                       timeout=timeout)
    return (r.get("result") or {}).get("value")


async def click(cap, xy):
    x, y = xy
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
            print("用页", p["id"][:8])
            break
        except Exception:
            continue
    if cap is None:
        print("没有可用列表页")
        return 2
    codes: list = []
    try:
        await zreact.inject(cap)
        tab = await ev(cap, JS_TAB)
        print("页签：", tab)
        if str(tab).startswith("{"):
            await click(cap, json.loads(tab)["xy"])
            await asyncio.sleep(5)
        seen = set()
        for _ in range(60):
            d = json.loads(await ev(cap, JS_CODES) or "{}")
            for c in d.get("codes") or []:
                if c not in seen:
                    seen.add(c)
                    codes.append(c)
            print("  已收 %d / 目标 %d" % (len(codes), d.get("total") or 0), flush=True)
            if d.get("total") and len(codes) >= int(d["total"]):
                break
            nxt = await ev(cap, JS_NEXT)
            if not str(nxt).startswith("["):
                break
            await click(cap, json.loads(nxt))
            await asyncio.sleep(4)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / ("%s.onsale.txt" % a.store)
    out.write_text("\n".join(sorted(codes)) + "\n", encoding="utf-8")
    print("在售中货号 %d 个 → %s" % (len(codes), out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--store", required=True)
    x = ap.parse_args()
    raise SystemExit(asyncio.run(main(x)))
