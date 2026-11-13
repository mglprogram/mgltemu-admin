# -*- coding: utf-8 -*-
"""refresh_presale_qty.py — 早/中/晚各抓一次 TEMU 在途预售库存（会变成 0）。

背景（用户 2026-11-13）：面板上的「在途预售库存」是**开预售当时的快照**，
卖着卖着平台会把它扣到 0（或预售到期自动关闭）。所以每天要回读几次。

流程：
    台账 data/presale-ledger.json 里该店的预售记录
    → 按 SPU 打开「修改库存」→ 用 skc_blocks 按**货号切块**（同 SPU 常挂多个 SKC）
    → 在目标块里按属性集读 开关/补货日期/在途预售库存
    → 写回台账（qty 以服务端为准；开关关闭记 0）→ 重建面板

用法：
    python scripts\\goods\\refresh_presale_qty.py --store BEL --dry-run
    python scripts\\goods\\refresh_presale_qty.py --store ALL --publish
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import bel_stock_set as B          # noqa: E402
import stock_presale_pipeline as P  # noqa: E402
import topup_by_sell_days as T      # noqa: E402

LEDGER = ROOT / "data" / "presale-ledger.json"


async def read_fields(tab, attrs: list) -> dict:
    """在弹窗里按**属性集全名**读 开关/补货日期/在途预售库存（attr 在块内唯一）。"""
    js = B.JS_DIALOG + r"""
var WANT = %s;
var out = {};
Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).forEach(function (tr) {
  var tds = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var attr = '';
  for (var k = 0; k < tds.length; k++) if (WANT.indexOf(tds[k]) >= 0) { attr = tds[k]; break; }
  if (!attr) return;
  var ins = Array.prototype.slice.call(tr.querySelectorAll('input'));
  var date = ins.filter(function (i) { return /^\d{4}-\d{2}-\d{2}$/.test(i.value || ''); })[0];
  var qty = ins.filter(function (i) { return (i.placeholder || '').indexOf('5000') >= 0; })[0];
  out[attr] = {on: (tr.innerText || '').indexOf('\u5df2\u5f00\u542f') >= 0,
               date: date ? date.value : '', qty: qty ? qty.value : ''};
});
return JSON.stringify(out);""" % json.dumps(attrs)
    return json.loads(await B.ev(tab, js))


async def refresh_store(store: str, dry: bool) -> int:
    led = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"items": []}
    items = led.get("items") or []
    mine = [x for x in items if (x.get("store") or "").upper() == store.upper()
            and (x.get("platform") or "TEMU").upper() == "TEMU"]
    if not mine:
        print("[%s] 台账里没有预售记录" % store)
        return 0
    groups: dict = {}
    for x in mine:
        groups.setdefault(x.get("code") or "", []).append(x)
    print("[%s] %d 条预售 / %d 个货号" % (store, len(mine), len(groups)))

    port, tid, opened = P.pick_work_page(store)
    if not port:
        print("[%s] 探不到窗口" % store)
        return 1
    tab = await P.open_tab(port, tid, opened, store)
    changed = 0
    try:
        for code, rows in groups.items():
            spu = rows[0].get("spu") or ""
            if not spu:
                continue
            if not await P.step_prepare(tab, spu, store):
                print("   %s 查询没命中，跳过" % code)
                continue
            if not await P.step_open_dialog(tab, spu):
                print("   %s 弹窗没打开，跳过" % code)
                continue
            await T.scroll_right(tab)
            await asyncio.sleep(0.8)
            blocks = await B.skc_blocks(tab)
            blk = next((b for b in blocks if (b.get("code") or "").upper() == code.upper()), None)
            if not blk:
                print("   %s 弹窗里找不到这个货号块，跳过" % code)
            else:
                attrs = [r.get("attr") for r in (blk.get("rows") or []) if r.get("attr")]
                live = await read_fields(tab, attrs)
                for x in rows:
                    size = str(x.get("size") or "").upper()
                    attr = next((a for a in attrs if a.upper().endswith("-" + size)), None)
                    r = live.get(attr or "")
                    if not r:
                        continue
                    q = int(float(r["qty"] or 0)) if (r["on"] and str(r["qty"]).strip()) else 0
                    old = x.get("qty")
                    if q != old or (r["date"] and r["date"] != x.get("date")):
                        print("   %-22s %-4s qty %s → %s ｜ 日期 %s ｜ 开关 %s"
                              % (code, size, old, q, r["date"] or "-", "已开启" if r["on"] else "已关闭"))
                        if not dry:
                            x["qty"] = q
                            if r["date"]:
                                x["date"] = r["date"]
                            x["qtyCheckedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            changed += 1
                    elif not dry:
                        x["qtyCheckedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await B.ev(tab, B.JS_DIALOG + r"""
if (!b.length) return 'no-modal';
var bs = Array.prototype.slice.call(b[0].querySelectorAll('button')).filter(function (x) {
  return (x.innerText || '').trim() === '取消' && x.getClientRects().length; });
return bs.length ? window.__zr.call(bs[bs.length-1], 'onClick') : 'no-cancel';""")
            await asyncio.sleep(1)
    finally:
        await tab.keep_tab()

    print("[%s] 变更 %d 条%s" % (store, changed, "（dry-run 未写）" if dry else ""))
    if not dry:
        LEDGER.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        print("[%s] 已写回台账" % store)
    return 0


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", choices=["BEL", "BHY", "ALL"], default="ALL")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()
    for s in (["BEL", "BHY"] if a.store == "ALL" else [a.store]):
        await refresh_store(s, a.dry_run)
    if a.publish and not a.dry_run:
        import subprocess
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "goods" / "build_presale_watch.py")],
                       cwd=str(ROOT))
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "bel_goods" / "publish_admin.py"),
                        "--build", "--production", "--skip-data"], cwd=str(ROOT))


if __name__ == "__main__":
    asyncio.run(main())
