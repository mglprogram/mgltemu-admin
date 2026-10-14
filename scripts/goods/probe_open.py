# -*- coding: utf-8 -*-
"""稳定性实验：连续给 N 个款开「修改库存」弹窗，统计成功率，并对比两种点法。

两种点法：
  ① React 直调（`__reactProps.onClick`）—— 目前的实现
  ② 真实鼠标点链接坐标（CDP `Input.dispatchMouseEvent`）—— 实测对"页大小下拉"有效

用法：python scripts/goods/probe_open.py BHY 8
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

JS_FILL_SKU = """
var ins = Array.prototype.slice.call(document.querySelectorAll('input'))
  .filter(function (i) { return i.getClientRects().length; });
if (ins.length < 7) return 'few:' + ins.length;
return window.__zr.write(ins[6], %s);
"""

JS_MODAL_OK = """
(function () {
  var txt = function (e) { return (e.innerText || '').replace(/[ \\t\\r\\n]+/g, ' ').trim(); };
  var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
    var st = null;
    try { st = getComputedStyle(d); } catch (e) { return false; }
    if (st.position !== 'fixed' && st.position !== 'absolute') return false;
    var r = d.getBoundingClientRect();
    if (!(r.width > 400 && r.height > 200)) return false;
    return txt(d).indexOf('库存增减') >= 0 && d.querySelectorAll('tbody tr').length > 0;
  });
  return String(boxes.length);
})()
"""

#: 找「修改库存」链接（优先 <A>），返回它自身和坐标
JS_LINK = """
var all = Array.prototype.slice.call(document.querySelectorAll('a,span,div,button'));
var links = all.filter(function (e) {
  return (e.innerText || '').trim() === '修改库存' && e.getClientRects().length > 0; });
if (!links.length) return JSON.stringify({n: 0});
var pick = null;
for (var i = 0; i < links.length; i++) {
  if (links[i].tagName === 'A') { pick = links[i]; break; }
}
if (!pick) pick = links[0];
try { pick.scrollIntoView({block: 'center'}); } catch (e) {}
var r = pick.getBoundingClientRect();
var p = window.__zr.findProps(pick);
return JSON.stringify({n: links.length, tag: pick.tagName,
  hasOnClick: !!(p && typeof p.onClick === 'function'),
  xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]});
"""

JS_CLICK_LINK = """
var all = Array.prototype.slice.call(document.querySelectorAll('a,span,div,button'));
var links = all.filter(function (e) {
  return (e.innerText || '').trim() === '修改库存' && e.getClientRects().length > 0; });
var pick = null;
for (var i = 0; i < links.length; i++) {
  var p = window.__zr.findProps(links[i]);
  if (p && typeof p.onClick === 'function') { pick = links[i]; break; }
}
if (!pick) return 'no-link';
try { pick.scrollIntoView({block: 'center'}); } catch (e) {}
return window.__zr.call(pick, 'onClick');
"""


async def modal_count(cap) -> int:
    r = await cap.send("Runtime.evaluate",
                       {"expression": JS_MODAL_OK, "returnByValue": True}, timeout=60)
    v = (r.get("result") or {}).get("value")
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


async def real_click(cap, xy) -> None:
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1},
                       timeout=20)
        await asyncio.sleep(0.2)


async def try_open(cap, method: str, code: str, dwell: float = 4.0) -> bool:
    """点一次 → 等 dwell → 看弹窗在不在。"""
    if method == "react":
        await zreact.call_js(cap, JS_CLICK_LINK, timeout=60)
    else:
        raw = await zreact.call_js(cap, JS_LINK, timeout=60)
        try:
            d = json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            d = {}
        if not d.get("xy"):
            return False
        await real_click(cap, tuple(d["xy"]))
    await asyncio.sleep(dwell)
    return (await modal_count(cap)) > 0


async def main() -> int:
    store = (sys.argv[1] if len(sys.argv) > 1 else "BHY").upper()
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    port = probe_port(WINDOWS[store])
    tid = await cdp_net.new_tab(port, URL, foreground=True)
    tgt = None
    for _ in range(30):
        await asyncio.sleep(1)
        tgt = next((x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                    if (x.get("id") or x.get("targetId")) == tid), None)
        if tgt:
            break
    # 取前 n 个「可加库存」的款
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import run_stock_topup as R  # noqa: E402
    cfg = R.load_cfg()
    codes = R.codes_of(store)[:n]
    print("测试 %d 个款: %s" % (len(codes), codes))

    stat = {"react_1st": 0, "react_retry": 0, "mouse_fallback": 0, "fail": 0}
    async with cdp_net.NetCapture(tgt["webSocketDebuggerUrl"]) as cap:
        for _ in range(20):
            await asyncio.sleep(3)
            if await zreact.call_js(cap, "return String(document.querySelectorAll"
                                        "('tbody tr').length);", timeout=60) != "0":
                break
        await zreact.inject(cap)
        for code in codes:
            sku = code + "-S"
            print("\n--- %s ---" % code)
            await zreact.call_js(cap, JS_FILL_SKU % json.dumps(sku), timeout=60)
            await zreact.call_js(cap, "return window.__zr.call(window.__zr.btn('查询'),"
                                      " 'onClick');", timeout=60)
            await asyncio.sleep(6)
            ok = await try_open(cap, "react", code)
            if ok:
                stat["react_1st"] += 1
                print("   ✅ React 一次点开")
            else:
                # 重试一次 React
                ok = await try_open(cap, "react", code)
                if ok:
                    stat["react_retry"] += 1
                    print("   ✅ React 第二次点开")
                else:
                    ok = await try_open(cap, "mouse", code)
                    if ok:
                        stat["mouse_fallback"] += 1
                        print("   ✅ 真实鼠标点开（React 两次都没开）")
                    else:
                        stat["fail"] += 1
                        print("   ❌ 两种点法都没开")
            # 关掉弹窗，跑下一个
            if await modal_count(cap) > 0:
                await zreact.call_js(cap, """
var bs = Array.prototype.slice.call(document.querySelectorAll('button'))
  .filter(function (b) { return (b.innerText || '').trim() === '取消' && b.getClientRects().length; });
if (bs.length) return window.__zr.call(bs[bs.length - 1], 'onClick');
return 'no-cancel';
""", timeout=60)
                await asyncio.sleep(2)
    await cdp_net.close_tab(port, tid)
    print("\n=== 统计（%d 个款）===" % len(codes))
    print("  React 一次成功 : %d" % stat["react_1st"])
    print("  React 二次成功 : %d" % stat["react_retry"])
    print("  鼠标兜底成功   : %d" % stat["mouse_fallback"])
    print("  全失败         : %d" % stat["fail"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
