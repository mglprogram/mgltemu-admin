# -*- coding: utf-8 -*-
"""读取 TEMU 商品列表「修改库存」弹窗里**正在预售**的尺码（只读）→ 写进开预售台账。

为什么要它：台账要反映平台上的真实状态。用户在后台手工开关预售，光靠 --append 手记
会漏；这里直接打开弹窗，把「在途库存预售 = 已开启」的行抓回来。

    python scripts/goods/sync_presale_from_ui.py --spu 3775612405 --code BHY-202504-10SetA
    python scripts/goods/sync_presale_from_ui.py --spu 3775612405 --code BHY-202504-10SetA --apply

不带 --apply 只打印差异；带 --apply 才写台账（并重建 presale-watch.json）：
  · 弹窗里已开启、台账里没有 → 新增（openedAt 取现在）
  · 台账里「预售中」但弹窗里已关闭 → 标为关闭（removed）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
import bel_stock_set as B  # noqa: E402
import bel_stock_batch as Batch  # noqa: E402
import topup_by_sell_days as T  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

LEDGER = Path(r"D:\AI-workplace\TEMU\data\presale-ledger.json")


async def read_presale(store: str, spu: str) -> list:
    port = probe_port(T.WINDOWS[store])
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    tid = (pages[-1].get("id") if pages else None) or await cdp_net.new_tab(port, B.URL, foreground=True)
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.any_page_ok = True
    try:
        assert await tab.open(), "工作页打不开"
        await tab.ensure()
        await zreact.inject(tab.session)
        if not await B.page_ready(tab, 60):
            raise RuntimeError("列表没加载出来")
        await B.pick_query_type(tab, "SPU")
        await B.search(tab, spu)
        if not await B.dialog_open(tab):
            await B.open_stock_dialog(tab, spu)
            await asyncio.sleep(3)
        rows = await B.ev(tab, B.JS_DIALOG + r"""
if (!b.length) return '[]';
return JSON.stringify(Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).map(function (tr) {
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var attr = cells.filter(function (c) { return /^[^\s]+-(S|M|L|XL|XXL|3XL|4XL)$/.test(c); })[0] || '';
  var tog = Array.prototype.slice.call(tr.querySelectorAll('div')).filter(function (e) {
    var p = window.__zr.findProps(e); return p && typeof p.onClick === 'function' && /已/.test(e.innerText||''); })[0];
  var date = Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) { return /^\d{4}-\d{2}-\d{2}$/.test(i.value||''); })[0];
  var q = Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) { return (i.placeholder||'').indexOf('5000')>=0; })[0];
  return {attr: attr, state: tog ? (tog.innerText||'').trim() : '', date: date ? date.value : '',
          qty: q ? parseInt(q.value || '0', 10) || 0 : 0};
}));""")
        return [r for r in (json.loads(rows or "[]") or []) if r.get("state") == "已开启"]
    finally:
        try:
            await B.ev(tab, B.JS_DIALOG + r"""
if (!b.length) return 'no-modal';
var bs = Array.prototype.slice.call(b[0].querySelectorAll('button')).filter(function (x) {
  return (x.innerText || '').trim() === '取消' && x.getClientRects().length; });
return bs.length ? window.__zr.call(bs[bs.length-1], 'onClick') : 'no-cancel';""")
        except Exception:  # noqa: BLE001
            pass
        await tab.keep_tab()


def update_ledger(store: str, code: str, spu: str, rows: list, apply: bool) -> None:
    led = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"items": []}
    items = list(led.get("items") or [])
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    live = {r["attr"].split("-")[-1]: r for r in rows}
    have = {(x.get("store"), x.get("code"), x.get("size")): x for x in items}
    adds, drops = [], []
    for size, r in live.items():
        if (store, code, size) not in have:
            adds.append({"platform": "TEMU", "store": store, "code": code, "size": size, "spu": spu,
                         "qty": r.get("qty") or 0, "date": r.get("date") or "", "openedAt": now})
    for (st, cd, sz), x in have.items():
        if st == store and cd == code and sz not in live and x.get("status") is None:
            drops.append((cd, sz))
    print("  弹窗里正在预售：", ",".join(sorted(live)) or "无")
    print("  台账新增：", [a["size"] for a in adds] or "无", "| 将要标记关闭：", [d[1] for d in drops] or "无")
    if not apply:
        return
    # 以弹窗为准：同店同款同尺码，弹窗没开的一律从台账删掉（面板会自动进「已过预售」不再显示为预售中）
    keep = [x for x in items if not (x.get("store") == store and x.get("code") == code)]
    keep.extend(adds)
    for size, r in live.items():
        old = have.get((store, code, size))
        if old:
            keep.append({**old, "spu": spu, "qty": r.get("qty") or 0, "date": r.get("date") or ""})
    LEDGER.write_text(json.dumps({"items": keep}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("  台账已更新：%d 条" % len(keep))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, choices=["BEL", "BHY"])
    ap.add_argument("--spu", required=True)
    ap.add_argument("--code", required=True, help="款号（带店铺前缀），如 BHY-202504-10SetA")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    print("读取 %s SPU %s …" % (a.store, a.spu))
    rows = await read_presale(a.store, a.spu)
    update_ledger(a.store, a.code, a.spu, rows, a.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
