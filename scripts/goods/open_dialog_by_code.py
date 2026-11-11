# -*- coding: utf-8 -*-
"""按「货号」精确打开「修改库存」弹窗（用户 2026-11-11 指正）。

症状：从**商品列表那一行**点「修改库存」时，如果点到的是同 SPU 里的另一个 SKC，
      弹窗默认停在那个 SKC 上，此时改另一块的行，提交会提示
      「至少修改一个sku」的 toast —— 也就是根本没改到目标货号。

正确做法（用户口径）：
  在列表里**按货号**（如 BHY-203805-3BHL）找到那一行/那个 SKC，再点它的「修改库存」；
  弹窗首先要停在**目标货号所在 SKC**，再去做 清零 / 开预售。

    python scripts/goods/open_dialog_by_code.py --store BHY --spu 1208899974 --code BHY-203805-3BHL
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
from ziniao_port import probe_port  # noqa: E402


async def list_rows_of_code(tab, code: str) -> list:
    return await B.ev_json(tab, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var out = [];
Array.prototype.slice.call(document.querySelectorAll('tbody tr')).forEach(function (tr, i) {
  var s = t(tr);
  if (s.indexOf('%s') < 0) return;
  var r = tr.getBoundingClientRect();
  out.push({i: i, y: Math.round(r.y + r.height / 2), text: s.slice(0, 100)});
});
return JSON.stringify(out);""" % code)


async def click_edit_in_row(tab, code: str) -> str:
    """把含该货号的商品行滚到视口，悬停出操作列，点「修改库存」。"""
    d = await B.ev_json(tab, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var tr = Array.prototype.filter.call(document.querySelectorAll('tbody tr'), function (x) {
  return t(x).indexOf('%s') >= 0; })[0];
if (!tr) return JSON.stringify({ok: false, why: 'no-row'});
tr.scrollIntoView({block: 'center'});
var r = tr.getBoundingClientRect();
var out = {ok: true, y: Math.round(r.y + r.height / 2)};
return JSON.stringify(out);""" % code)
    if not d.get("ok"):
        return "定位失败:%s" % d
    await B.mouse(tab, "mouseMoved", 400, d["y"])
    await asyncio.sleep(1)
    d2 = await B.ev_json(tab, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var tr = Array.prototype.filter.call(document.querySelectorAll('tbody tr'), function (x) {
  return t(x).indexOf('%s') >= 0; })[0];
if (!tr) return JSON.stringify({ok: false});
var a = Array.prototype.slice.call(tr.querySelectorAll('a')).filter(function (e) { return t(e) === '修改库存'; })[0];
if (!a) return JSON.stringify({ok: false, why: 'no-link'});
var r = a.getBoundingClientRect();
return JSON.stringify({ok: true, xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]});""" % code)
    if not d2.get("ok"):
        return "列表行里没有「修改库存」链接:%s" % d2
    await B.click_xy(tab, d2["xy"])
    ok = await B.wait_dialog(tab, 12)
    return "点列表行「修改库存」→ %s" % ("弹窗开了" if ok else "没弹出来")


async def dialog_block_of(tab, code: str) -> dict:
    """弹窗当前停在哪个 SKC / 货号上。"""
    return await B.ev_json(tab, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var b = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (x) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var q = d.getBoundingClientRect();
  return q.width > 400 && q.height > 200 && t(d).indexOf('库存增减') >= 0 && d.querySelectorAll('tbody tr').length > 0;
});
b.sort(function (x, y) { return x.querySelectorAll('tbody tr').length - y.querySelectorAll('tbody tr').length; });
if (!b.length) return JSON.stringify({ok: false});
var s = t(b[0]);
var codes = []; var re = /货号：\s*([A-Za-z0-9\-]+)/g, m;
while ((m = re.exec(s))) codes.push(m[1]);
var cur = (s.match(/货号：\s*([A-Za-z0-9\-]+)\s+当前SKC/) || [])[1] || '';
return JSON.stringify({ok: true, codes: codes, current: cur,
  targetInModal: codes.indexOf('%s') >= 0});""" % code)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--spu", required=True)
    ap.add_argument("--code", required=True)
    a = ap.parse_args()
    win = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}[a.store]
    port = probe_port(win)
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    tid = pages[-1].get("id")
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.store = a.store
    assert await tab.open(), "工作页打不开"
    await tab.ensure()
    await zreact.inject(tab.session)
    print("① 条件→SPU:", await B.pick_query_type(tab, "SPU"))
    print("② 查询:", await B.search(tab, a.spu))
    rows = json.loads(await list_rows_of_code(tab, a.code) or "[]")
    print("③ 含该货号的列表行:", [(r["i"], r["text"][:60]) for r in rows])
    print("④", await click_edit_in_row(tab, a.code))
    await asyncio.sleep(3)
    print("⑤ 弹窗当前块:", await dialog_block_of(tab, a.code))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
