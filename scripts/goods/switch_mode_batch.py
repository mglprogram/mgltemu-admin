# -*- coding: utf-8 -*-
"""把「合作对接仓发货」的 SKU 切成「卖家自发货」并填回原库存（三步确认，实测可行）。

链路（2026-11-18 真机验证）：
    商品列表 → 货号(SKU精准查询) 搜**完整 SKU** → 点该行「发货模式」右侧铅笔
      → 弹框点「发货模式」下拉 → 选「卖家自发货」
      → 表格出现 加州3仓（BEL 还有 kansas仓）行，库存格是 id 含 stockNum 的输入框
      → 填数（见下）→ 确认 → 「确认修改发货模式吗」确认 → 风险框确认
      → POST /visage-agent-seller/product/sku/shippingMode/edit

填数口径：
    · 只有 1 行（加州3仓）：填 max(1, 原库存)
    · 有 2 行（加州3仓 + kansas仓）：加州 = max(1, 原库存-1)、kansas = 1（两行都必须 >0）

用法：
    python scripts/goods/switch_mode_batch.py --port 59670 --items _logs/bel_blocked_skus2.json --limit 3
    python scripts/goods/switch_mode_batch.py --port 56172 --items _logs/kls_blocked_skus.json --apply
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
import zreact  # noqa: E402

URL = "https://agentseller.temu.com/goods/list"

JS_SEARCH = r"""
var kw = %s;
var ins = Array.prototype.slice.call(document.querySelectorAll('input')).filter(function (i) {
  return i.getClientRects().length && (i.placeholder || '').indexOf('多个查询') >= 0; });
if (!ins.length) return 'no-input';
ins.sort(function (a, b) { return a.getBoundingClientRect().y - b.getBoundingClientRect().y; });
var el = ins[ins.length - 1]; el.focus();
var set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
set.call(el, kw);
el.dispatchEvent(new Event('input', {bubbles: true}));
el.dispatchEvent(new Event('change', {bubbles: true}));
return window.__zr.btn('查询') ? window.__zr.call(window.__zr.btn('查询'), 'onClick') : 'no-btn';"""

JS_ROW = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var sku = %s;
var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr')).filter(function (r) {
  return t(r).indexOf(sku) >= 0; });
var mode = '';
if (trs.length) {
  Array.prototype.forEach.call(trs[0].querySelectorAll('td'), function (td) {
    var s = t(td); if (s === '卖家自发货' || s === '合作对接仓发货') mode = s; });
}
return JSON.stringify({n: trs.length, mode: mode});"""

JS_PENCIL = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var sku = %s;
var tr = Array.prototype.slice.call(document.querySelectorAll('tbody tr')).filter(function (r) {
  return t(r).indexOf(sku) >= 0; })[0];
if (!tr) return 'no-row';
var cell = Array.prototype.slice.call(tr.querySelectorAll('td')).filter(function (td) {
  var s = t(td); return s === '合作对接仓发货' || s === '卖家自发货'; })[0];
if (!cell) return 'no-cell';
var svg = cell.querySelector('[data-testid="beast-core-icon-edit"]');
if (!svg) return 'no-icon';
var el = svg.parentElement || svg;
try { el.scrollIntoView({block: 'center'}); } catch (e) {}
var r = el.getBoundingClientRect();
return JSON.stringify([Math.round(r.x+r.width/2), Math.round(r.y+r.height/2)]);"""

JS_DLG = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 300 && /发货模式/.test(t(d)) && d.querySelectorAll('tbody tr').length > 0;
});
if (!boxes.length) return 'closed';
boxes.sort(function (a, b) {
  return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length; });
window.__dlg = boxes[0];
return 'open';"""

JS_OPEN_SEL = r"""
if (!window.__dlg) return 'no-dlg';
var s = window.__dlg.querySelector('[data-testid="beast-core-select"]');
if (!s) return 'no-sel';
var h = s.querySelector('[data-testid="beast-core-select-header"]') || s;
return window.__zr.call(h, 'onClick');"""

JS_OPT = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var c = Array.prototype.slice.call(document.querySelectorAll('div,li,span')).filter(function (e) {
  return !e.children.length && t(e) === '卖家自发货' && e.getClientRects().length; });
if (!c.length) return 'none';
var r = c[c.length - 1].getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width/2), Math.round(r.y + r.height/2)]);"""

JS_OPT2 = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
Array.prototype.forEach.call(document.querySelectorAll('[class*="cIL_item"]'), function (e) {
  var s = t(e);
  if (s !== '卖家自发货' || !e.getClientRects().length) return;
  var r = e.getBoundingClientRect();
  out.push([Math.round(r.x + r.width/2), Math.round(r.y + r.height/2)]);
});
return out.length ? JSON.stringify(out[0]) : 'none';"""

JS_SEL_XY = r"""
if (!window.__dlg) return 'no-dlg';
var s = window.__dlg.querySelector('[data-testid="beast-core-select"]');
if (!s) return 'no-sel';
var h = s.querySelector('[data-testid="beast-core-select-header"]') || s;
var r = h.getBoundingClientRect();
return JSON.stringify([Math.round(r.x+r.width/2), Math.round(r.y+r.height/2)]);"""

JS_CELLS = r"""
var boxes = Array.prototype.slice.call(document.querySelectorAll('[id*="stockNum"]'));
return JSON.stringify(boxes.map(function (b) {
  var el = b.closest('td') || b;
  var r = el.getBoundingClientRect();
  return [Math.round(r.x + r.width/2), Math.round(r.y + r.height/2)];
}));"""

JS_VALS = r"""
var boxes = Array.prototype.slice.call(document.querySelectorAll('[id*="stockNum"]'));
return JSON.stringify(boxes.map(function (b) {
  var i = b.querySelector('input');
  return i ? i.value : '';
}));"""

JS_BTN = r"""
var t = function (e) { return (e.innerText || '').trim(); };
var pat = %s;
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  return new RegExp(pat).test(d.innerText || '') && d.querySelectorAll('button').length > 0; });
if (!boxes.length) return 'none';
boxes.sort(function (a, b) { return a.getBoundingClientRect().width - b.getBoundingClientRect().width; });
var b = Array.prototype.slice.call(boxes[0].querySelectorAll('button')).filter(function (x) {
  return t(x) === '确认'; })[0];
if (!b) return 'no-btn';
var r = b.getBoundingClientRect();
return JSON.stringify([Math.round(r.x+r.width/2), Math.round(r.y+r.height/2)]);"""


async def _ev(cap, js, timeout=40):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js,
                        "returnByValue": True}, timeout=timeout)
    if r.get("exceptionDetails"):
        return "EXC:" + str(r["exceptionDetails"])[:140]
    return (r.get("result") or {}).get("value")


async def _click(cap, xy):
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1},
                       timeout=20)
        await asyncio.sleep(0.2)


async def _search(cap, sku: str) -> dict:
    await _ev(cap, JS_SEARCH % json.dumps(sku))
    await asyncio.sleep(6)
    try:
        return json.loads(await _ev(cap, JS_ROW % json.dumps(sku)) or "{}")
    except Exception:  # noqa: BLE001
        return {}


async def _open_dialog(cap, sku: str) -> bool:
    for _ in range(4):
        xy = await _ev(cap, JS_PENCIL % json.dumps(sku))
        if not str(xy).startswith("["):
            return False
        await _click(cap, json.loads(xy))
        for _w in range(5):
            await asyncio.sleep(1)
            if await _ev(cap, JS_DLG) == "open":
                return True
    return False


async def _switch_mode(cap) -> bool:
    for attempt in range(6):
        # 交替：React onClick / 真实鼠标点头部
        if attempt % 2 == 0:
            await _ev(cap, JS_OPEN_SEL)
        else:
            xy = await _ev(cap, JS_SEL_XY)
            if str(xy).startswith("["):
                await _click(cap, json.loads(xy))
        await asyncio.sleep(1.3)
        opt = await _ev(cap, JS_OPT)
        if not str(opt).startswith("["):
            # 有的构建里选项在 cIL_item 容器上，再用容器匹配一次
            opt = await _ev(cap, JS_OPT2)
        if not str(opt).startswith("["):
            continue
        await _click(cap, json.loads(opt))
        await asyncio.sleep(2.5)
        await _ev(cap, JS_DLG)
        cells = await _ev(cap, JS_CELLS)
        if str(cells).startswith("[") and cells != "[]":
            return True
    return False


async def _fill_cells(cap, stock: int) -> list:
    cells = "none"
    for _ in range(10):
        cells = await _ev(cap, JS_CELLS)
        if str(cells).startswith("[") and cells != "[]":
            break
        await asyncio.sleep(1)
    if not str(cells).startswith("["):
        return []
    arr = json.loads(cells)
    vals = [max(1, stock - 1), 1] if len(arr) >= 2 else [max(1, stock)]
    for k, c in enumerate(arr[:2]):
        v = vals[k] if k < len(vals) else vals[-1]
        await _click(cap, c)
        await asyncio.sleep(0.5)
        await cap.send("Input.insertText", {"text": str(v)}, timeout=20)
        await asyncio.sleep(0.4)
        await cap.send("Input.dispatchKeyEvent",
                       {"type": "keyDown", "key": "Tab", "windowsVirtualKeyCode": 9}, timeout=10)
        await asyncio.sleep(0.4)
    got = json.loads(await _ev(cap, JS_VALS) or "[]")
    return got


async def _confirm_all(cap, log=print) -> None:
    for pat in ("当前库存", "确认修改发货模式", "建议SKU下每个发货仓"):
        c = await _ev(cap, JS_BTN % json.dumps(pat))
        if str(c).startswith("["):
            await _click(cap, json.loads(c))
            log("   确认：%s" % pat[:8])
        await asyncio.sleep(3)


async def switch_one(cap, sku: str, stock: int, apply: bool, log=print) -> dict:
    out = {"sku": sku, "stock": stock, "ok": False}
    st = await _search(cap, sku)
    out["before"] = st
    if st.get("n") != 1:
        out["why"] = "匹配 %s 行" % st.get("n")
        return out
    if st.get("mode") == "卖家自发货":
        out.update(ok=True, skipped=True)
        return out
    if not await _open_dialog(cap, sku):
        out["why"] = "弹框没打开"
        return out
    out["switched"] = await _switch_mode(cap)
    out["filled"] = await _fill_cells(cap, stock)
    if not apply:
        out.update(ok=True, dry=True)
        return out
    if not out["filled"]:
        out["why"] = "库存没填上"
        return out
    await _confirm_all(cap, log=log)
    await asyncio.sleep(20)
    st2 = await _search(cap, sku)
    out["after"] = st2
    out["ok"] = st2.get("mode") == "卖家自发货"
    if not out["ok"]:
        out["why"] = "回读=%s" % st2.get("mode")
    log("  %s -> %s" % (sku, out["ok"]))
    return out


async def main_async(a) -> int:
    items = json.loads(Path(a.items).read_text(encoding="utf-8"))
    if a.limit:
        items = items[:a.limit]
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if pages:
        tid = pages[-1]["id"]
        used_existing = True
    else:
        tid = await cdp_net.new_tab(a.port, URL, foreground=True)
        used_existing = False
        await asyncio.sleep(22)
    cap = await cdp_net.NetCapture(
        next(x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("id") == tid)["webSocketDebuggerUrl"], match=()).__aenter__()
    bad = 0
    try:
        await asyncio.sleep(10)
        try:
            await cap.send("Page.bringToFront", {}, timeout=10)   # 后台页会限制点击
        except Exception:  # noqa: BLE001
            pass
        await zreact.inject(cap)
        for i, it in enumerate(items):
            print("[%d/%d] %s" % (i + 1, len(items), it["sku"]))
            try:
                r = await switch_one(cap, it["sku"], int(it.get("stock") or 0), a.apply)
            except Exception as e:  # noqa: BLE001
                r = {"sku": it.get("sku"), "ok": False, "why": "异常:%s" % str(e)[:100]}
            print(json.dumps(r, ensure_ascii=False))
            if not r.get("ok"):
                bad += 1
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        # ⚠ 只关**自己开的**页：用户窗口里那个商品列表页是他在用的，
        #   以前无条件 close_tab 会把用户的页关掉（用户 2026-11-19 一再强调别杀他的标签页）。
        if not used_existing:
            await cdp_net.close_tab(a.port, tid)
    print("完成 %d，失败 %d" % (len(items), bad))
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--items", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
