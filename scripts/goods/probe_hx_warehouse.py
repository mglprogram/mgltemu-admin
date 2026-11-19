# -*- coding: utf-8 -*-
"""在「设置库存」（stock-edit）页把 恒信加州3 加进 发货仓。

用法：
    python scripts/goods/probe_hx_warehouse.py --store BHY --step dump
    python scripts/goods/probe_hx_warehouse.py --store BHY --step pick     # 只勾选，不提交
    python scripts/goods/probe_hx_warehouse.py --store BHY --step submit   # 勾选 + 点提交（真改）

页面来源：商品列表-在售中 → 修改库存 → 站点与发货仓关系设置 → 会新开
`/goods/stock-edit?productId=<SPU>`；发货仓多选在
`#siteWarehouseRouteMap.site_100.warehouseId`。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import bel_stock_set as B  # noqa: E402
import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}
SEL_HEAD = '#siteWarehouseRouteMap\\.site_100\\.warehouseId [data-testid="beast-core-select-header"]'

JS_HEAD_XY = r"""
var el = document.querySelector(%s);
if (!el) return 'null';
try { el.scrollIntoView({block: 'center'}); } catch (e) {}
var r = el.getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);
""" % json.dumps(SEL_HEAD)

JS_OPTS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
Array.prototype.forEach.call(document.querySelectorAll('[data-testid="beast-core-select-option"], [class*="ST_option"], li'), function (e) {
  var s = t(e);
  if (!s || s.length > 30) return;
  if (!e.getClientRects().length) return;
  var r = e.getBoundingClientRect();
  if (r.top < 0 || r.height < 8) return;
  out.push({txt: s, testid: e.getAttribute('data-testid') || '', cls: String(e.className || '').slice(0, 50),
            xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]});
});
return JSON.stringify(out);
"""

JS_TAGS = r"""
var el = document.querySelector('#siteWarehouseRouteMap\\.site_100\\.warehouseId');
if (!el) return 'null';
var tags = Array.prototype.slice.call(el.querySelectorAll('[data-testid="beast-core-tagGroup-tag"]'))
  .map(function (e) { return (e.innerText || '').trim(); });
return JSON.stringify(tags);
"""


def _xy(raw):
    try:
        return json.loads(raw or "null")
    except Exception:  # noqa: BLE001
        return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--step", default="dump")
    ap.add_argument("--text", default="恒信加州3")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()

    port = a.port or probe_port(WINDOWS[a.store])
    if not port:
        print("窗口没开：%s" % WINDOWS[a.store])
        return 2
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "stock-edit" in (x.get("url") or "")]
    if not pages:
        print("没有 stock-edit 页")
        return 3
    tid = pages[-1].get("id")
    tab = B.OwnTab(port, tid)
    tab.store = a.store
    tab.reuse_tid = tid
    tab.any_page_ok = True
    assert await tab.open(), "页打不开"
    await zreact.inject(tab.session)
    print("tab:", tab.tid, "url:", (await B.ev(tab, "return location.href;")))

    print("当前发货仓:", await B.ev(tab, JS_TAGS))

    xy = _xy(await B.ev(tab, JS_HEAD_XY))
    print("下拉位置:", xy)
    if not xy:
        return 4
    opts = json.loads(await B.ev(tab, JS_OPTS) or "[]")
    if not opts:                       # 已开的话再点会关掉 ⇒ 空才点
        await B.click_xy(tab, xy)
        await asyncio.sleep(1.2)
        opts = json.loads(await B.ev(tab, JS_OPTS) or "[]")
    if not opts:                       # 兜底再点一次
        await B.click_xy(tab, xy)
        await asyncio.sleep(1.5)
        opts = json.loads(await B.ev(tab, JS_OPTS) or "[]")
    print("选项:", json.dumps(opts, ensure_ascii=False))
    await B.shot(tab, "hx_wh_open.png")

    if a.step in ("pick", "submit"):
        target = next((o for o in opts if o["txt"] == a.text), None)
        if not target:
            print("没找到选项:", a.text)
            return 5
        await B.click_xy(tab, target["xy"])
        await asyncio.sleep(1.0)
        print("勾选后发货仓:", await B.ev(tab, JS_TAGS))
        await B.shot(tab, "hx_wh_picked.png")

    if a.step == "submit":
        xy2 = _xy(await B.ev(tab, r"""
var t = function (e) { return (e.innerText || '').trim(); };
var bs = Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (b) {
  return t(b) === '提交' && b.getClientRects().length > 0 && !b.disabled; });
if (!bs.length) return 'null';
var r = bs[bs.length - 1].getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);"""))
        print("提交按钮:", xy2)
        if not xy2:
            return 6
        await B.click_xy(tab, xy2)
        await asyncio.sleep(2.5)
        # 可能弹确认框（确认/确定/我知道了）
        for _ in range(3):
            got = _xy(await B.ev(tab, r"""
var t = function (e) { return (e.innerText || '').trim(); };
var btns = Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (b) {
  var s = t(b); return (s === '确认' || s === '确定' || s === '我知道了') && b.getClientRects().length > 0; });
if (!btns.length) return 'null';
var b = btns[btns.length - 1];
var r = b.getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);"""))
            if not got:
                break
            print("确认框:", got)
            await B.click_xy(tab, got)
            await asyncio.sleep(2)
        await asyncio.sleep(2)
        print("提交后发货仓:", await B.ev(tab, JS_TAGS))
        await B.shot(tab, "hx_wh_submitted.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
