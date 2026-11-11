# -*- coding: utf-8 -*-
"""只读抓包：点「修改库存」弹窗「确认」后，看服务端返回什么（排查 BHY 提交不生效）。

    python scripts/goods/probe_stock_submit_net.py --spu 1208899974 --code BHY-203805-3BHL --size L

只做一件事：打开弹窗 → 把该尺码「减少 当前库存」+ 开预售 + 填在途量 →
开 Network 抓包 → 点确认 → 收集所有 POST 响应体 → 打印请求 URL / 状态 / 返回 JSON。
⚠ 会真的点「确认」（不改数据的前提下也算一次提交），所以只用于排查；
   若上次已提交成功请勿重复跑（平台 7 天只能开一次）。
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
import bel_stock_set as B  # noqa: E402
import bel_stock_batch as Batch  # noqa: E402
import topup_by_sell_days as T  # noqa: E402
from ziniao_port import probe_port  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spu", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--size", required=True)
    ap.add_argument("--qty", type=int, default=0, help="在途预售库存（0=不开预售，只改库存）")
    a = ap.parse_args()

    port = probe_port("TM-BHY-YJF")
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    tid = pages[-1].get("id")
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.store = "BHY"
    assert await tab.open(), "工作页打不开"
    await tab.ensure()
    await zreact.inject(tab.session)

    # 干净重开弹窗
    await B.ev(tab, B.JS_DIALOG + r"""
var bs = Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (x) {
  return (x.innerText || '').trim() === '取消' && x.getClientRects().length; });
return bs.length ? window.__zr.call(bs[bs.length-1], 'onClick') : 'no-cancel';""")
    await asyncio.sleep(2)
    print("打开弹窗:", await B.open_stock_dialog(tab, a.spu))
    await asyncio.sleep(3)
    await T.scroll_right(tab)
    await asyncio.sleep(0.8)

    # 填：清零 + （可选）开预售 + 在途量
    row = await T.row_of_code(tab, a.code, a.size)
    print("定位:", row)
    if not row.get("ok"):
        return 3
    cur = int(await B.ev(tab, B.JS_DIALOG + r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (!tr) return '';
var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
for (var i = 0; i < cells.length; i++) if (/^\d+$/.test(cells[i])) return cells[i];
return '';""" % json.dumps(row["attr"])) or "0")
    print("当前库存:", cur)
    if cur > 0:
        print("清零:", await B.set_row(tab, row["attr"], "减少", str(cur)))
    if a.qty > 0:
        if row.get("state") != "已开启":
            await B.click_xy(tab, row["xy"])
            await asyncio.sleep(2)
            print("确认开启:", await B.ev(tab, r"""
var t = function (e) { return (e.innerText || '').trim(); };
var box = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var q = d.getBoundingClientRect(); var s = d.innerText || '';
  return q.width > 200 && q.width < 700 && q.height > 60 && q.height < 350 && s.indexOf('确认开启') >= 0 && d.querySelectorAll('button').length > 0; });
if (!box.length) return 'no-warn';
box.sort(function (x, y) { var p = x.getBoundingClientRect(), r = y.getBoundingClientRect(); return p.width*p.height - r.width*r.height; });
var bs = Array.prototype.slice.call(box[0].querySelectorAll('button')).filter(function (x) { return t(x) === '确认'; });
return bs.length ? window.__zr.call(bs[bs.length-1], 'onClick') : 'no-btn';"""))
            await asyncio.sleep(2)
            await T.scroll_right(tab)
            await asyncio.sleep(0.8)
        print("填在途量:", await B.ev(tab, B.JS_DIALOG + r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (!tr) return 'no-row';
var q = Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) { return (i.placeholder || '').indexOf('5000') >= 0; })[0];
return q ? window.__zr.write(q, '%d') : 'no-input';""" % (json.dumps(row["attr"]), a.qty)))

    # 开始抓包：直接在同一个页面上再开一条抓包会话（不关原会话）
    ws = (await tab.find())["webSocketDebuggerUrl"]
    async with cdp_net.NetCapture(ws) as nc:
        # 点「确认」（弹窗底部那个）
        xy = json.loads(await B.ev(tab, r"""
var btns = Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (b) {
  var q = b.getBoundingClientRect();
  return (b.innerText || '').trim() === '确认' && q.width > 40 && q.y > 640; });
if (!btns.length) return 'null';
var q = btns[btns.length-1].getBoundingClientRect();
return JSON.stringify([Math.round(q.x + q.width/2), Math.round(q.y + q.height/2)]);""") or "null")
        print("点确认:", xy)
        if xy:
            await B.click_xy(tab, xy)
        await asyncio.sleep(3)
        # 风险框确认（只点带 onClick 的真按钮）
        print("风险框真按钮:", await B._click_risk_confirm(tab))
        xy2 = "null"
        _skip = r"""
var t = function (e) { return (e.innerText || '').trim(); };
var box = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var q = d.getBoundingClientRect(); var s = d.innerText || '';
  return q.width > 200 && q.width < 700 && q.height > 60 && q.height < 350 && s.indexOf('建议SKU') >= 0 && d.querySelectorAll('button').length > 0; });
if (!box.length) return 'null';
box.sort(function (x, y) { var p = x.getBoundingClientRect(), r = y.getBoundingClientRect(); return p.width*p.height - r.width*r.height; });
var bs = Array.prototype.slice.call(box[0].querySelectorAll('button')).filter(function (x) { return t(x) === '确认'; });
if (!bs.length) return 'null';
var q = bs[bs.length-1].getBoundingClientRect();
return JSON.stringify([Math.round(q.x + q.width/2), Math.round(q.y + q.height/2)]);"""
        _ = _skip
        await asyncio.sleep(6)
        print("弹窗还开?", await B.dialog_open(tab))

    hits = [h for h in nc.hits if h.get("req") or "stock" in h["url"] or "presale" in h["url"]]
    print("\n=== 抓到的写请求 %d 条 ===" % len(hits))
    for h in hits[-25:]:
        j = h.get("json") or {}
        brief = {k: j.get(k) for k in ("success", "errorCode", "errorMsg", "msg", "message", "code", "result") if k in j}
        if isinstance(brief.get("result"), (dict, list)):
            brief["result"] = str(brief["result"])[:200]
        print("-", h["url"][-110:])
        if h.get("req"):
            print("   req:", h["req"][:300])
        print("   resp:", json.dumps(brief, ensure_ascii=False)[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
