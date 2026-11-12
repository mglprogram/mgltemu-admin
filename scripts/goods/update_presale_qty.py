# -*- coding: utf-8 -*-
"""update_presale_qty.py — 改**已经开着**的「在途预售库存」数量（不动开关、不动补货日期）。

什么时候用：
    · 预售已经开好了，但当时填的量不合适（比如按 7 天量填多了，占用了其他平台的共享库存）
    · 只想调数量，不想重新走「清零 → 开开关 → 选日期」那一套（重开会撞 7 天锁）

流程（用户 2026-11-12 定的顺序）：
    按 SPU 查 → 开「修改库存」→ 横滚到最右 → **多个尺码一次性写** →
    **只点一次**弹窗「确认」→ 风险框「确认」→ **重开弹窗回读**逐行核对

用法：
    # 只看现值
    python scripts\\goods\\update_presale_qty.py --store BHY --spu 8235641204 ^
        --code BHY-202504-5SetA --qty S=65,M=158

    # 真改
    python scripts\\goods\\update_presale_qty.py --store BHY --spu 8235641204 ^
        --code BHY-202504-5SetA --qty S=65,M=158,L=88,XL=85,XXL=60 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import bel_stock_set as B          # noqa: E402
import stock_presale_pipeline as P  # noqa: E402
import topup_by_sell_days as T      # noqa: E402


async def read_rows(tab) -> list:
    return json.loads(await B.ev(tab, B.JS_DIALOG + r"""
var out = [];
Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).forEach(function (tr) {
  var tds = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var attr = tds.filter(function (x) { return /-(S|M|L|XL|XXL|3XL|4XL)$/.test(x); })[0] || '';
  if (!attr) return;
  var ins = Array.prototype.slice.call(tr.querySelectorAll('input'));
  var date = ins.filter(function (i) { return /^\d{4}-\d{2}-\d{2}$/.test(i.value || ''); })[0];
  var qty = ins.filter(function (i) { return (i.placeholder || '').indexOf('5000') >= 0; })[0];
  out.push({attr: attr, date: date ? date.value : '', qty: qty ? qty.value : '',
            on: (tr.innerText || '').indexOf('\u5df2\u5f00\u542f') >= 0});
});
return JSON.stringify(out);"""))


async def write_qty(tab, attr: str, qty: int) -> dict:
    return await B.ev_json(tab, B.JS_DIALOG + r"""
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (!tr) return JSON.stringify({ok: false, why: 'no-row'});
var inp = Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) {
  return (i.placeholder || '').indexOf('5000') >= 0; })[0];
if (!inp) return JSON.stringify({ok: false, why: 'no-input'});
return JSON.stringify({ok: true, v: window.__zr.write(inp, %s)});"""
                                % (json.dumps(attr), json.dumps(str(qty))))


async def run(args) -> int:
    store = args.store.upper()
    want = {}
    for part in (args.qty or "").split(","):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition("=")
        want[k.strip().upper()] = int(v)
    if not want:
        print("!! 没给 --qty（形如 S=65,M=158）")
        return 2

    port, tid, opened = P.pick_work_page(store)
    if not port:
        print("!! 探不到窗口（%s）" % store)
        return 3
    tab = await P.open_tab(port, tid, opened, store)
    print("工作页:", str(tab.tid)[:16], "(复用)" if not opened else "(新开)")
    try:
        if not await P.step_prepare(tab, args.spu, store):
            print("!! 查询没命中该 SPU")
            return 4
        if not await P.step_open_dialog(tab, args.spu):
            print("!! 修改库存弹窗没打开")
            return 4
        await T.scroll_right(tab)
        await asyncio.sleep(0.8)
        rows = await read_rows(tab)
        print("改动前：")
        for r in rows:
            size = r["attr"].split("-")[-1]
            print("   %-12s 开关=%-4s 日期=%-11s 在途预售库存=%-6s → %s"
                  % (r["attr"], "已开启" if r["on"] else "-", r["date"] or "-",
                     r["qty"] or "-", want.get(size, "(不动)")))
        todo = [(r["attr"], want[r["attr"].split("-")[-1]]) for r in rows
                if r["attr"].split("-")[-1] in want]
        if not todo:
            print("!! 弹窗里没有要改的尺码")
            return 5
        if not args.apply:
            print("(dry-run，不写；加 --apply 才提交)")
            return 0
        for attr, q in todo:
            print("   写入 %-12s → %s（%s）" % (attr, q, await write_qty(tab, attr, q)))
        await asyncio.sleep(0.8)
        print("⑧ 提交（一次确认 + 风险框确认）：", await P.step_submit(tab, True))
        await asyncio.sleep(1.5)
        if await B.dialog_open(tab):
            print("!! 提交后弹窗没关，可能被拒")
            return 6
        # 重开弹窗回读
        if not await P.step_open_dialog(tab, args.spu):
            print("!! 回读时弹窗打不开")
            return 7
        await T.scroll_right(tab)
        await asyncio.sleep(0.8)
        after = await read_rows(tab)
        bad = []
        print("改动后（服务端）：")
        for r in after:
            size = r["attr"].split("-")[-1]
            exp = want.get(size)
            ok = (exp is None) or (str(r["qty"]) == str(exp))
            if not ok:
                bad.append((r["attr"], r["qty"], exp))
            print("   %-12s 开关=%-4s 日期=%-11s 在途预售库存=%-6s %s"
                  % (r["attr"], "已开启" if r["on"] else "-", r["date"] or "-",
                     r["qty"] or "-", "" if ok else "❌ 期望 %s" % exp))
        await B.ev(tab, B.JS_DIALOG + r"""
if (!b.length) return 'no-modal';
var bs = Array.prototype.slice.call(b[0].querySelectorAll('button')).filter(function (x) {
  return (x.innerText || '').trim() === '取消' && x.getClientRects().length; });
return bs.length ? window.__zr.call(bs[bs.length-1], 'onClick') : 'no-cancel';""")
        print("\n结论:", "✅ 全部生效" if not bad else "❌ 不一致：%s" % bad)
        return 0 if not bad else 8
    finally:
        await tab.keep_tab()


def main() -> int:
    ap = argparse.ArgumentParser(description="改已开启预售的在途预售库存（不动开关/日期）")
    ap.add_argument("--store", default="BHY", choices=["BEL", "BHY"])
    ap.add_argument("--spu", required=True)
    ap.add_argument("--code", default="", help="货号（多 SKC 时必给）")
    ap.add_argument("--qty", required=True, help="尺码=数量，逗号分隔：S=65,M=158")
    ap.add_argument("--apply", action="store_true")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
