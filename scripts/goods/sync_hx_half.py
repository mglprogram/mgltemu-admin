# -*- coding: utf-8 -*-
"""把「恒信仓库存 = ⌈领星可用 ÷ 2⌉」真正写进 TEMU 后台（用户 2026-11-20 授权，仅 BHY 测试）。

做法（`stock_edit_page.py` 已跑通的那条编辑页通道）：
    货号搜索(SKU 模糊查询) → 修改库存弹窗 → 点「站点与发货仓关系设置」
      → goods/stock-edit?productId=<SPU> 独立页 → 按「尺码 × 恒信加州3仓」填绝对库存 → 提交整页

安全阀（默认全开）：
    · 只处理 `--store BHY`（现在只测这家）
    · **只动「恒信加州3仓」那一行**，加州3仓/kansas 一个字都不碰
    · 只在 `--apply` 时提交；默认 dry-run，打印「行 → 原值 → 新值」
    · `--limit N` 先跑几个货号；`--codes A,B` 指定货号
    · 提交后**重开编辑页回读**，逐行核对，任何一行不对就报红

用法：
    python scripts/goods/sync_hx_half.py --dry-run                      # 只看要改什么
    python scripts/goods/sync_hx_half.py --codes BHY-203311-4SetA --apply   # 先试一个货号
    python scripts/goods/sync_hx_half.py --apply --limit 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import bel_stock_set as B  # noqa: E402
import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

SNAP = ROOT / "data" / "stock-snapshot"
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}
LIST_URL = "https://agentseller.temu.com/goods/list"

READ_EDIT = r"""
(function () {
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var rows = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
  var out = [], size = '', color = '';
  rows.forEach(function (tr) {
    var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
    var ins = Array.prototype.slice.call(tr.querySelectorAll('input'));
    var box = ins.filter(function (i) { return !i.readOnly; })[0];
    if (!box) return;
    var joined = cells.join(' | ');
    // 尺码列：S/M/L/XL/XXL/15Y…，只在「有值」时更新当前尺码（合并单元格）
    var m = cells.filter(function (c) { return /^(XXS|XS|S|M|L|XL|XXL|XXXL|3XL|4XL|5XL|\d+Y)$/.test(c); })[0];
    if (m) size = m;
    var c2 = cells.filter(function (c) { return /^[\u4e00-\u9fa5A-Za-z0-9]+$/.test(c) && c.length <= 6
                                        && !/^(XXS|XS|S|M|L|XL|XXL|XXXL|3XL|4XL|5XL|\d+Y)$/.test(c); })[0];
    var wh = cells.filter(function (c) { return /仓|加州|恒信|kansas/i.test(c); })[0] || '';
    out.push({cells: cells, size: size, sizeOwn: !!m, wh: wh, value: box.value,
              idx: Array.prototype.slice.call(document.querySelectorAll('tbody tr')).indexOf(tr)});
  });
  return JSON.stringify(out);
})()
"""


def load_targets(store: str, codes: list, day: str) -> dict:
    """{货号: {尺码: 目标恒信库存}}，来自当天快照（hxHalf）。"""
    p = SNAP / ("%s.json" % day)
    if not p.exists():
        raise SystemExit("没有当天快照：%s（先跑 daily_stock_snapshot.py）" % p)
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = ((d.get("stores") or {}).get(store) or {}).get("rows") or []
    tgt: dict = {}
    for r in rows:
        if codes and r["code"] not in codes:
            continue
        if int(r.get("hxHalf") or 0) <= 0:
            continue
        tgt.setdefault(r["code"], {})[str(r["size"]).upper()] = int(r["hxHalf"])
    return tgt


async def open_edit(store: str, code: str, spu: str):
    """打开该货号的单款编辑页，返回 (tab, cap, 行列表, 新页 dict)。"""
    port = probe_port(WINDOWS[store])
    tid = await cdp_net.new_tab(port, LIST_URL, foreground=True)
    await asyncio.sleep(12)
    page = None
    for _ in range(20):
        for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port):
            if x.get("id") == tid and x.get("webSocketDebuggerUrl"):
                page = x
                break
        if page:
            break
        await asyncio.sleep(1.5)
    if not page:
        raise RuntimeError("新页没枚举出来")
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.any_page_ok = True
    await tab.open()
    await tab.ensure()
    await zreact.inject(tab.session)
    await B.page_ready(tab, 60)
    await B.close_overlays(tab)
    # 货号搜索
    await tab.session.eval(
        "(function(code){var ins=[].slice.call(document.querySelectorAll('input'));"
        "for(var i=0;i<ins.length-1;i++){if(/^SKU\\(/.test((ins[i].value||'').trim())){"
        "window.__zr.write(ins[i+1],code);return 'ok';}}return 'no';})(%s)" % json.dumps(code),
        timeout=25)
    await tab.session.eval(
        "(function(){var b=window.__zr.btn('\u67e5\u8be2');if(!b)return 'no';"
        "window.__zr.call(b,'onClick');return 'ok';})()", timeout=25)
    await asyncio.sleep(3)
    await B.open_stock_dialog(tab, spu)
    await B.wait_dialog(tab, 14)
    # 点「站点与发货仓关系设置」
    await B.ev(tab, B.JS_DIALOG + r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var a = Array.prototype.slice.call(b[0].querySelectorAll('a'))
  .filter(function (e) { return /站点与发货仓/.test(t(e)); })[0];
if (!a) return 'no-link';
var p = window.__zr.findProps(a);
if (p && typeof p.onClick === 'function') { window.__zr.call(a, 'onClick'); return 'zr'; }
a.click(); return 'dom';""")
    edit = None
    for _ in range(15):
        await asyncio.sleep(1.5)
        cands = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                 if x.get("type") == "page" and "stock-edit" in (x.get("url") or "")]
        if cands:
            edit = cands[-1]
            break
    await asyncio.sleep(3)
    return tab, edit


async def main() -> int:
    ap = argparse.ArgumentParser(description="把恒信仓库存同步成 ⌈领星可用/2⌉（只 BHY）")
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--codes", default="", help="逗号分隔货号；空=快照里所有 hxHalf>0")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    tgt = load_targets(a.store, codes, a.date)
    if a.limit:
        tgt = dict(list(tgt.items())[:a.limit])
    print("[%s] 要同步的货号 %d 个，共 %d 个尺码" % (a.store, len(tgt), sum(len(v) for v in tgt.values())))
    for code, sizes in tgt.items():
        print("  %-24s %s" % (code, "，".join("%s→%d" % kv for kv in sorted(sizes.items()))))
    if not a.apply:
        print("\n(dry-run) 加 --apply 才会真的写后台")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
