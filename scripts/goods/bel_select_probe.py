# -*- coding: utf-8 -*-
"""BEL 查询条件下拉「SKC/SKU/SPU」诊断（只读，不动用户的页）。

只打印：下拉的可点元素、React props 键名、选项浮层的真实 DOM 结构。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402


async def ev(cap, body, timeout=60):
    return await zreact.call_js(cap, body, timeout=timeout)


async def main() -> int:
    port = probe_port("TM-BEL-MGL")
    tid = await cdp_net.new_tab(port, "https://agentseller.temu.com/goods/list", foreground=False)
    await asyncio.sleep(14)
    tgt = next((x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                if (x.get("id") or x.get("targetId")) == tid), None)
    if not tgt:
        print("!! 新页没枚举出来")
        return 3
    try:
        async with cdp_net.NetCapture(tgt["webSocketDebuggerUrl"]) as cap:
            await asyncio.sleep(3)
            await zreact.inject(cap)
            print("输入框:", await ev(cap, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
return JSON.stringify(window.__zr.inputs().slice(0, 4).map(function (i, k) {
  var box = i.parentElement, chain = [];
  for (var u = 0; u < 4 && box; u++, box = box.parentElement) {
    var keys = Object.keys(box);
    chain.push({tag: box.tagName, cls: String(box.className).slice(0, 40),
      react: keys.filter(function (k) { return k.indexOf('__react') === 0; }),
      txt: t(box).slice(0, 12)});
  }
  return {k: k, v: i.value, ph: i.placeholder, chain: chain};
}));"""))
            # 真实鼠标点下拉箭头（input 右侧）
            xy = await ev(cap, r"""
var i = window.__zr.inputs()[0];
var r = i.getBoundingClientRect();
return JSON.stringify([Math.round(r.right - 8), Math.round(r.y + r.height / 2)]);""")
            print("箭头坐标:", xy)
            x, y = json.loads(xy)
            for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
                await cap.send("Input.dispatchMouseEvent",
                               {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1},
                               timeout=20)
                await asyncio.sleep(0.2)
            await asyncio.sleep(1.5)
            print("点开后浮层:", await ev(cap, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var out = [];
Array.prototype.slice.call(document.querySelectorAll('div,ul,li,span'))
  .forEach(function (e) {
    if (!e.getClientRects().length) return;
    var s = t(e);
    if (!s || s.length > 12) return;
    if (['SKC', 'SKU', 'SPU'].indexOf(s) < 0) return;
    var r = e.getBoundingClientRect();
    var keys = Object.keys(e).filter(function (k) { return k.indexOf('__react') === 0; });
    out.push({tag: e.tagName, txt: s, cls: String(e.className).slice(0, 46),
      xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)], react: keys,
      parentCls: String(e.parentElement.className).slice(0, 40)});
  });
return JSON.stringify(out);"""))
            print("截图:", end=" ")
            import base64
            r = await cap.send("Page.captureScreenshot", {"format": "png"}, timeout=30)
            p = Path(r"D:\AI-workplace\TEMU\_logs\bel_probe_select.png")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(base64.b64decode(r.get("data") or ""))
            print(p)
    finally:
        await cdp_net.close_tab(port, tid)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
