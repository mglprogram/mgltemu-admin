# -*- coding: utf-8 -*-
"""把 TEMU 商品从「合作对接仓发货」切到「卖家自发货」（加固版）。

入口（2026-11-18 实测）：商品列表 → 货号(SKU精准查询) → 点「发货模式」格右侧铅笔
    → 弹「发货模式 / 库存维护方式」（默认目标 卖家自发货 + 加州3仓）→ 确认。

加固点（2026-11-18 踩坑后加）：
  ① 自己开标签页（不碰用户那页，用户页卡死不耽误）；
  ② 搜索用**货号(SKU精准查询)**那个输入框（按 y 取最下面那个「多个查询…」）；
  ③ 点铅前必须校验：列表里目标款号的行数 == 1，且铅笔取自该行；
  ④ 确认后重查一次，货号匹配且发货模式为「卖家自发货」才算成功。

用法：
    python scripts/goods/switch_delivery_mode.py --store BEL --port 59670 --sku BEL-102105-3BHL-XXL --apply
    python scripts/goods/switch_delivery_mode.py --store BEL --port 59670 --sku-file _logs/skus.txt --apply
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

JS_FILL_KW = r"""
var kw = %s;
var ins = Array.prototype.slice.call(document.querySelectorAll('input')).filter(function (i) {
  return i.getClientRects().length && (i.placeholder || '').indexOf('多个查询') >= 0; });
if (!ins.length) return 'no-input';
ins.sort(function (a, b) { return a.getBoundingClientRect().y - b.getBoundingClientRect().y; });
var target = ins[ins.length - 1];        /* 最下面那个 = 货号(SKU精准查询) */
target.focus();
var set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
set.call(target, kw);
target.dispatchEvent(new Event('input', {bubbles: true}));
target.dispatchEvent(new Event('change', {bubbles: true}));
return 'filled:' + target.value;
"""

JS_ROWS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var code = %s;
var rows = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
var hit = [];
rows.forEach(function (tr) {
  var s = t(tr);
  if (s.indexOf('货号：' + code) >= 0 || s.indexOf('货号： ' + code) >= 0) hit.push(tr);
});
if (!hit.length) return JSON.stringify({n: 0});
var tr = hit[0];
var svg = tr.querySelector('[data-testid="beast-core-icon-edit"]');
var xy = null;
if (svg) {
  var el = svg.parentElement || svg;
  try { el.scrollIntoView({block: 'center'}); } catch (e) {}
  var r = el.getBoundingClientRect();
  xy = [Math.round(r.x + r.width/2), Math.round(r.y + r.height/2)];
}
var mode = '';
Array.prototype.forEach.call(tr.querySelectorAll('td'), function (td) {
  var s = t(td);
  if (s === '卖家自发货' || s === '合作对接仓发货') mode = s;
});
return JSON.stringify({n: hit.length, mode: mode, xy: xy});
"""

JS_DLG = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var boxes = Array.prototype.slice.call(document.querySelectorAll('[data-testid="beast-core-portal-main"], .delivery-mode-render_editMultiContent__Hdo1s'));
boxes = boxes.filter(function (d) {
  var s = t(d);
  return (/发货模式/.test(s) || /站点与发货仓关系设置/.test(s)) &&
         d.querySelectorAll('button').length > 0 && d.getClientRects().length;
});
if (!boxes.length) return 'none';
boxes.sort(function (a, b) { return a.getBoundingClientRect().width - b.getBoundingClientRect().width; });
var box = boxes[boxes.length - 1];
var b = Array.prototype.slice.call(box.querySelectorAll('button')).filter(function (x) {
  return t(x) === '确认'; })[0];
var xy = null;
if (b) { var r = b.getBoundingClientRect(); xy = [Math.round(r.x+r.width/2), Math.round(r.y+r.height/2)]; }
return JSON.stringify({text: t(box).slice(0, 160), confirm: xy});
"""


JS_CLICK_PENCIL = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var code = %s;
var rows = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
var tr = rows.filter(function (r) {
  var s = t(r);
  return s.indexOf('货号：' + code) >= 0 || s.indexOf('货号： ' + code) >= 0;
})[0];
if (!tr) return 'no-row';
var svg = tr.querySelector('[data-testid="beast-core-icon-edit"]');
if (!svg) return 'no-icon';
var el = svg.parentElement || svg;
return window.__zr.call(el, 'onClick');
"""


async def _ev(cap, js, timeout=40):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js,
                        "returnByValue": True}, timeout=timeout)
    if r.get("exceptionDetails"):
        return "EXC:" + str(r["exceptionDetails"])[:200]
    return (r.get("result") or {}).get("value")


async def _click(cap, xy):
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1},
                       timeout=20)
        await asyncio.sleep(0.15)


async def _search(cap, kw: str, code: str) -> dict:
    await _ev(cap, JS_FILL_KW % json.dumps(kw))
    await _ev(cap, "return window.__zr.btn('查询') ? window.__zr.call(window.__zr.btn('查询'), 'onClick') : 'no-btn';")
    await asyncio.sleep(6)
    raw = await _ev(cap, JS_ROWS % json.dumps(code))
    try:
        return json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        return {}


async def switch_one(cap, sku: str, apply: bool, log=print) -> dict:
    out = {"sku": sku, "ok": False}
    code = sku.rsplit("-", 1)[0]          # 去掉尺码得款号
    info = await _search(cap, sku, code)  # 精准查询要用完整 SKU 货号
    out["before"] = info
    if info.get("n") != 1:
        out["why"] = "列表匹配到 %s 行（要 1 行）" % info.get("n")
        return out
    if info.get("mode") == "卖家自发货":
        out.update(ok=True, skipped=True)
        return out
    if not info.get("xy"):
        out["why"] = "该行没有发货模式铅笔"
        return out
    r = await _ev(cap, JS_CLICK_PENCIL % json.dumps(code))
    out["click"] = r
    dlg = "none"
    for _ in range(3):
        for _ in range(6):
            await asyncio.sleep(1)
            dlg = await _ev(cap, JS_DLG)
            if dlg and dlg != "none":
                break
        if dlg and dlg != "none":
            break
        await _click(cap, info["xy"])
    out["dialog"] = dlg
    if not dlg or dlg == "none":
        out["why"] = "发货模式弹框没打开"
        return out
    conf = json.loads(dlg).get("confirm")
    if not apply:
        out.update(ok=True, dry=True)
        return out
    if not conf:
        out["why"] = "找不到确认按钮"
        return out
    await _click(cap, conf)
    await asyncio.sleep(4)
    after = await _search(cap, sku, code)
    out["after"] = after
    out["ok"] = after.get("mode") == "卖家自发货" and after.get("n") == 1
    if not out["ok"]:
        out["why"] = "回读发货模式=%s" % after.get("mode")
    log("  %s -> %s" % (sku, out.get("ok")))
    return out


async def main_async(a) -> int:
    skus = [a.sku] if a.sku else []
    if a.sku_file:
        skus += [x.strip() for x in Path(a.sku_file).read_text(encoding="utf-8").split()
                 if x.strip()]
    if not skus:
        print("没给 SKU")
        return 2
    tid = await cdp_net.new_tab(a.port, URL, foreground=True)
    info = next(x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
                if x.get("id") == tid)
    cap = await cdp_net.NetCapture(info["webSocketDebuggerUrl"], match=()).__aenter__()
    bad = 0
    try:
        await asyncio.sleep(10)
        await zreact.inject(cap)
        for i, sku in enumerate(skus):
            print("[%d/%d] %s" % (i + 1, len(skus), sku))
            try:
                r = await switch_one(cap, sku, a.apply)
            except Exception as e:  # noqa: BLE001
                r = {"sku": sku, "ok": False, "why": "异常:%s" % str(e)[:120]}
            print(json.dumps(r, ensure_ascii=False))
            if not r.get("ok"):
                bad += 1
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        await cdp_net.close_tab(a.port, tid)
    print("完成 %d，失败 %d" % (len(skus), bad))
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BEL")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--sku", default="")
    ap.add_argument("--sku-file", default="")
    ap.add_argument("--apply", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

