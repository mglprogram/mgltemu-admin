# -*- coding: utf-8 -*-
"""把商品列表调到「一页装完在售中所有商品」——从而**完全不必筛选**。

用户 2026-10-12 截图确认：每页可选 20 / 50 / 100 / 200 / 500。
在售中 88 个商品 × 5 尺码 ≈ 440 行 ⇒ 选 **200**（或 500）后目标款必在当前页。

关键实测（踩过的坑都在这）：
  ① 分页器在页面底部、**初始在视口外**（y≈1319 > 视口高 1012）⇒ 必须先滚到底；
  ② 页大小是自定义 select，那个浮层的选项**不能用 `pick`（取最外层会选错）**，
     要用 `pickLeaf`（文本完全相等且无子文本）或按坐标真实鼠标点；
  ③ 换每页大小后要等一会儿（会重新请求列表）。

用法：python scripts/goods/set_page_size.py BHY [200|500]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

URL = "https://agentseller.temu.com/goods/list"
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}

JS_SCROLL_BOTTOM = """
var sc = document.scrollingElement || document.documentElement;
sc.scrollTop = sc.scrollHeight;
var boxes = Array.prototype.slice.call(document.querySelectorAll('div'))
  .filter(function (d) { return d.scrollHeight > d.clientHeight + 200 && d.clientHeight > 200; });
boxes.forEach(function (b) { b.scrollTop = b.scrollHeight; });
return 'scrolled ' + boxes.length;
"""

#: 页大小输入框（值就是当前页大小；不 scrollIntoView —— 实测会抛错）
JS_FIND_SIZE = """
var ins = Array.prototype.slice.call(document.querySelectorAll('input'));
var cands = ins.filter(function (i) {
  if (!i.getClientRects().length) return false;
  var v = (i.value || '').trim();
  return v === '10' || v === '20' || v === '50' || v === '100' || v === '200' || v === '500';
});
if (!cands.length) return JSON.stringify({ok: false});
var el = cands[cands.length - 1];
var r = el.getBoundingClientRect();
return JSON.stringify({ok: true, value: el.value,
  viewport: [window.innerWidth, window.innerHeight],
  rect: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]});
"""

JS_COUNT = """
var ins = Array.prototype.slice.call(document.querySelectorAll('input'));
var nums = ins.filter(function (i) {
  if (!i.getClientRects().length) return false;
  var v = (i.value || '').trim();
  if (v.length === 0 || v.length > 3) return false;
  for (var k = 0; k < v.length; k++) {
    var c = v.charAt(k);
    if (c < '0' || c > '9') return false;
  }
  return true;
});
return JSON.stringify({rows: document.querySelectorAll('tbody tr').length,
  size: nums.length ? nums[nums.length - 1].value : null});
"""


async def click_at(cap, xy):
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1},
                       timeout=20)
        await asyncio.sleep(0.2)
    await asyncio.sleep(1.5)


async def set_page_size(cap, want: str, verbose: bool = True) -> bool:
    """把每页设成 want；成功返回 True。"""
    await zreact.call_js(cap, JS_SCROLL_BOTTOM, timeout=60)
    await asyncio.sleep(1)
    info = json.loads(await zreact.call_js(cap, JS_FIND_SIZE, timeout=60) or "{}")
    if not info.get("ok"):
        if verbose:
            print("!! 找不到每页控件")
        return False
    if info["value"] == want:
        if verbose:
            print("每页已经是 %s" % want)
        return True
    if verbose:
        print("① 滚到底后点每页下拉 @ %s（当前 %s）" % (info["rect"], info["value"]))
    await click_at(cap, tuple(info["rect"]))
    leafs = await zreact.call_js(cap, "return window.__zr.numLeafs();", timeout=60)
    if verbose:
        print("② 浮层数字叶子:", str(leafs)[:300])
    got = await zreact.call_js(cap, "return window.__zr.pickLeaf(null, %s);"
                                    % json.dumps(want), timeout=60)
    if verbose:
        print("③ pickLeaf:", got)
    d = {}
    try:
        d = json.loads(got)
    except Exception:  # noqa: BLE001
        pass
    if not d.get("ok") and d.get("rect"):
        if verbose:
            print("   退回真实鼠标点", d["rect"])
        await click_at(cap, tuple(d["rect"]))
    await asyncio.sleep(10)
    after = json.loads(await zreact.call_js(cap, JS_COUNT, timeout=60) or "{}")
    if verbose:
        print("④ 现在: 每页=%s 行数=%s" % (after.get("size"), after.get("rows")))
    return str(after.get("size")) == str(want)


async def main() -> int:
    store = (sys.argv[1] if len(sys.argv) > 1 else "BHY").upper()
    want = sys.argv[2] if len(sys.argv) > 2 else "200"
    port = probe_port(WINDOWS[store])
    tid = await cdp_net.new_tab(port, URL, foreground=True)
    tgt = None
    for _ in range(30):
        await asyncio.sleep(1)
        tgt = next((x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                    if (x.get("id") or x.get("targetId")) == tid), None)
        if tgt:
            break
    async with cdp_net.NetCapture(tgt["webSocketDebuggerUrl"]) as cap:
        for _ in range(20):
            await asyncio.sleep(3)
            c = json.loads(await zreact.call_js(cap, JS_COUNT, timeout=60) or "{}")
            if c.get("rows"):
                print("初始: 每页=%s 行数=%s" % (c.get("size"), c.get("rows")))
                break
        await zreact.inject(cap)
        ok = await set_page_size(cap, want)
        print("结果:", "✅ 每页已设为 %s" % want if ok else "❌ 没设成功")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
