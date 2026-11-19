# -*- coding: utf-8 -*-
"""BEL「修改库存」实验 v2（只读：打开弹窗 → 填数 → 取消，绝不点确认）。

v1 卡住的真因：下拉选项是 <LI>，之前用 pickLeaf（只认 div/li/span 的**文字叶子**）
匹配不到 ⇒ 下拉没切换成 SPU ⇒ 输入框还是按 SKC 查 ⇒ 0 行 ⇒ 找不到「修改库存」。
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
import run_stock_topup as R  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

SPU = "43933794478"
CODE = "BEL-T1036-3B"
SHOT = Path(r"D:\AI-workplace\TEMU\_logs")
URL = "https://agentseller.temu.com/goods/list"


async def ev(cap, body, timeout=60):
    return await zreact.call_js(cap, body, timeout=timeout)


async def mouse(cap, tp, x, y):
    await cap.send("Input.dispatchMouseEvent",
                   {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1}, timeout=20)
    await asyncio.sleep(0.15)


async def click_xy(cap, xy):
    x, y = xy
    await mouse(cap, "mouseMoved", x, y)
    await mouse(cap, "mousePressed", x, y)
    await mouse(cap, "mouseReleased", x, y)


async def shot(cap, name):
    SHOT.mkdir(parents=True, exist_ok=True)
    r = await cap.send("Page.captureScreenshot", {"format": "png"}, timeout=30)
    p = SHOT / name
    p.write_bytes(base64.b64decode(r.get("data") or ""))
    print("   截图:", p)


async def main() -> int:
    port = probe_port("TM-BEL-MGL")
    tid = await cdp_net.new_tab(port, URL, foreground=False)
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

            # ① 下拉切 SPU：先点箭头开浮层，再点 <LI>SPU
            print("① 商品ID查询 → SPU")
            xy = json.loads(await ev(cap, r"""
var i = window.__zr.inputs()[0]; var r = i.getBoundingClientRect();
return JSON.stringify([Math.round(r.right - 8), Math.round(r.y + r.height / 2)]);"""))
            await click_xy(cap, xy)
            await asyncio.sleep(1)
            opt = json.loads(await ev(cap, r"""
var t = function (e) { return (e.innerText || '').trim(); };
var li = Array.prototype.slice.call(document.querySelectorAll('li'))
  .filter(function (e) { return t(e) === 'SPU' && e.getClientRects().length; });
if (!li.length) return JSON.stringify({ok: false});
var r = li[li.length - 1].getBoundingClientRect();
return JSON.stringify({ok: true, xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]});"""))
            print("   SPU 选项:", opt)
            if not opt.get("ok"):
                print("!! 下拉没打开")
                return 4
            await click_xy(cap, opt["xy"])
            await asyncio.sleep(1)
            print("   现在条件 =", await ev(cap, "return window.__zr.inputs()[0].value;"))

            # ② 填 SPU + 查询
            await ev(cap, r"""
var ins = window.__zr.inputs();
return window.__zr.write(ins[1], %s);""" % json.dumps(SPU))
            await asyncio.sleep(1)
            print("② 查询:", await ev(cap, "return window.__zr.call(window.__zr.btn('查询'), 'onClick');"))
            await asyncio.sleep(10)
            print("   行数/命中:", await ev(cap, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
return JSON.stringify({n: trs.length,
  hit: trs.filter(function (tr) { return t(tr).indexOf('货号：%s') >= 0; }).length});""" % CODE))
            await shot(cap, "bel_probe_v2_1_list.png")

            # ③ 悬停目标行 → 点「修改库存」
            print("③ 悬停 → 修改库存")
            opened = False
            for i in range(8):
                raw = await ev(cap, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var tr = Array.prototype.slice.call(document.querySelectorAll('tbody tr'))
  .filter(function (x) { return t(x).indexOf('货号：%s') >= 0; })[0];
if (!tr) return JSON.stringify({why: 'no-row'});
var r = tr.getBoundingClientRect();
tr.scrollIntoView({block: 'center'});
r = tr.getBoundingClientRect();
var a = Array.prototype.slice.call(tr.querySelectorAll('a'))
  .filter(function (e) { return t(e) === '修改库存'; })[0];
var out = {y: Math.round(r.y + r.height / 2), rows: document.querySelectorAll('tbody tr').length};
if (a) {
  var b = a.getBoundingClientRect();
  out.xy = [Math.round(b.x + b.width / 2), Math.round(b.y + b.height / 2)];
}
return JSON.stringify(out);""" % CODE)
                d = json.loads(raw or "{}")
                print("   poll", i, raw)
                if d.get("y"):
                    await mouse(cap, "mouseMoved", 400, d["y"])
                await asyncio.sleep(1)
                if d.get("xy"):
                    print("   真实鼠标点:", d["xy"])
                    await click_xy(cap, d["xy"])
                    opened = await R.wait_dialog(cap, 12)
                    print("   dialog:", opened)
                    break
            if not opened:
                print("   react 兜底:", await ev(cap, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var tr = Array.prototype.slice.call(document.querySelectorAll('tbody tr'))
  .filter(function (x) { return t(x).indexOf('货号：%s') >= 0; })[0];
var a = tr && Array.prototype.slice.call(tr.querySelectorAll('a'))
  .filter(function (e) { return t(e) === '修改库存'; })[0];
if (!a) return 'no-link';
return window.__zr.call(a, 'onClick');""" % CODE))
                opened = await R.wait_dialog(cap, 12)
                print("   dialog(react):", opened)
            if not opened:
                print("!! 弹窗打不开")
                return 5

            # ④ 读弹窗
            info = await R.ev_json(cap, R.DIALOG_INFO)
            print("④ 弹窗 sku=", info.get("sku"))
            for row in info.get("rows", []):
                print("   ", {k: row.get(k) for k in ("size", "attr", "curStock", "afterStock", "qtyPh", "nInputs")})

            # ⑤ 填 20（增加）→ 看「修改后库存」
            print("⑤ 黑色-L 填 20:", await R.fill_add(cap, "L", 20))
            await asyncio.sleep(2)
            st = await R.ev_json(cap, R.DIALOG_INFO)
            for row in st.get("rows", []):
                print("   ", row.get("size"), "cur=", row.get("curStock"),
                      "填=", row.get("qtyValue"), "修改后=", row.get("afterStock"))
            await shot(cap, "bel_probe_v2_2_dialog.png")

            # ⑥ 试着把该行「增加」切到「减少」（看看控件怎么点）
            print("⑥ 增减控件:", await ev(cap, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var box = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 400 && r.height > 200 && t(d).indexOf('库存增减') >= 0;
});
box.sort(function (a, b) { return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length; });
var trs = Array.prototype.slice.call(box[0].querySelectorAll('tbody tr'));
var tr = trs.filter(function (x) { return t(x).indexOf('黑色-L') >= 0; })[0];
if (!tr) return 'no-row';
var sel = Array.prototype.slice.call(tr.querySelectorAll('div,span'))
  .filter(function (e) { return t(e) === '增加' && e.getClientRects().length; });
var r0 = sel.length ? sel[sel.length - 1].getBoundingClientRect() : null;
return JSON.stringify({n: sel.length, cls: sel.length ? String(sel[0].className).slice(0, 60) : '',
  xy: r0 ? [Math.round(r0.x + r0.width / 2), Math.round(r0.y + r0.height / 2)] : null});"""))
            # ⑦ 取消
            print("⑦ 取消:", await ev(cap, r"""
var bs = Array.prototype.slice.call(document.querySelectorAll('button'))
  .filter(function (b) { return b.innerText.trim() === '取消' && b.getClientRects().length; });
return bs.length ? window.__zr.call(bs[bs.length - 1], 'onClick') : 'no-cancel';"""))
    finally:
        await cdp_net.close_tab(port, tid)
        print("已关掉实验页")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
