# -*- coding: utf-8 -*-
"""读 BEL 窗口里**用户自己开着的**商品列表页（只读，不点、不改、不关）。

用途：看「点击修改库存但弹窗没出来」时页面到底什么状态（遮罩？报错？新窗口？）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

SHOT = Path(r"D:\AI-workplace\TEMU\_logs")


async def main() -> int:
    port = probe_port("TM-BEL-MGL")
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("窗口里没有商品列表页")
        return 1
    ws = pages[0]["webSocketDebuggerUrl"]
    async with cdp_net.NetCapture(ws) as cap:
        await zreact.inject(cap)
        print(await zreact.call_js(cap, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var ins = window.__zr.inputs().slice(0, 3).map(function (i) { return i.value; });
var trs = document.querySelectorAll('tbody tr');
// 找所有 fixed 浮层
var overlays = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 300 && r.height > 150;
}).map(function (d) { return t(d).slice(0, 80); }).slice(0, 6);
// 「修改库存」链接存在与否 + 数量
var links = Array.prototype.slice.call(document.querySelectorAll('a'))
  .filter(function (a) { return t(a) === '修改库存' && a.getClientRects().length; });
return JSON.stringify({inputs: ins, rows: trs.length, overlays: overlays,
  editLinks: links.length, readyState: document.readyState,
  scrollY: window.scrollY, bodyH: document.body.scrollHeight});"""))
        r = await cap.send("Page.captureScreenshot", {"format": "png"}, timeout=30)
        SHOT.mkdir(parents=True, exist_ok=True)
        p = SHOT / "bel_user_tab.png"
        p.write_bytes(base64.b64decode(r.get("data") or ""))
        print("截图:", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
