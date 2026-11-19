# -*- coding: utf-8 -*-
"""给「后台实读」的预售货号补上 **在途预售库存 + 补货日期**（只读弹窗，不改平台）。

背景（用户 2026-11-18 截图）：面板「预售中」里实读出来的行，`在途预售库存` 显示 0、
`补货日期` 显示 —，因为列表接口只给「开关状态」，不给填进去的数量。

本脚本按 SPU 打开「修改库存」弹窗，用 skc_blocks 按**货号**切块，读每行
「已开启 / 补货日期 / 在途预售库存」，回写到 data/presale-live.json 的
`presaleQty` / `presaleDate` 字段（不碰 data/presale-ledger.json）。

用法：
    python scripts/goods/refresh_presale_live_fields.py                 # 三个店全跑
    python scripts/goods/refresh_presale_live_fields.py --store BEL
    python scripts/goods/refresh_presale_live_fields.py --dry-run       # 只打印读到的值
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import bel_stock_set as B  # noqa: E402
import cdp_net  # noqa: E402
import refresh_presale_qty as RP  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF", "KinglamanSport": "TM-KinglamanSport"}
LIST_URL = {
    "BEL": "https://agentseller.temu.com/goods/list",
    "BHY": "https://agentseller.temu.com/goods/list",
    # ⚠ KinglamanSport 虽然是本土店（seller.kuajingmaihuo.com），但那套列表 UI 不支持
    #   「按 SPU 查询」也不带「修改库存」弹窗；同一个窗口直接开 half-managed 的
    #   agentseller 商品列表就能用（已实测：34 行、弹窗可读）。用户 2026-11-18 实测确认。
    "KinglamanSport": "https://agentseller.temu.com/goods/list",
}
LIVE = ROOT / "data" / "presale-live.json"


async def setup(store: str):
    port = probe_port(WINDOWS[store])
    if not port:
        raise SystemExit("[%s] 紫鸟窗口没开" % store)
    # 一定要开**干净的**新页：旧页可能留着上次的货号/SPU 过滤，按 SPU 查会 0 行。
    tid = await cdp_net.new_tab(port, LIST_URL[store], foreground=True)
    await asyncio.sleep(13)
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.any_page_ok = True
    if not await tab.open():
        raise SystemExit("[%s] 工作页打不开" % store)
    await tab.ensure()
    await zreact.inject(tab.session)
    if not await B.page_ready(tab, 60):
        raise SystemExit("[%s] 商品列表没加载出来" % store)
    return tab


def _size(attr: str) -> str:
    return str(attr or "").rsplit("-", 1)[-1].upper()


READ_JS = r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var b = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 400 && r.height > 200 && t(d).indexOf('\u5e93\u5b58\u589e\u51cf') >= 0 &&
         d.querySelectorAll('tbody tr').length > 0;
});
b.sort(function (a, c) { return a.querySelectorAll('tbody tr').length - c.querySelectorAll('tbody tr').length; });
var box = b[0];
if (!box) return JSON.stringify({err: 'no-dialog'});
var blocks = [], cur = null, lastAttr = '';
Array.prototype.slice.call(box.querySelectorAll('tbody tr')).forEach(function (tr) {
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var joined = cells.join(' | ');
  var m = joined.match(/\u8d27\u53f7\uff1a\s*([A-Za-z0-9\-]+)/);
  if (m) { cur = {code: m[1], rows: []}; blocks.push(cur); lastAttr = ''; }
  if (!cur) return;
  var attr = cells.filter(function (c) { return /^[^\s]+-(XXS|XS|S|M|L|XL|XXL|XXXL|3XL|4XL|5XL|\d+Y)$/.test(c); })[0] || '';
  if (attr) lastAttr = attr;
  var ins = Array.prototype.slice.call(tr.querySelectorAll('input'));
  var q = ins.filter(function (i) { return (i.placeholder || '').indexOf('5000') >= 0; })[0];
  var dt = ins.filter(function (i) { return (i.placeholder || '').indexOf('日期') >= 0 || /^\d{4}-\d{2}-\d{2}$/.test(i.value || ''); })[0];
  var on = (tr.innerText || '').indexOf('\u5df2\u5f00\u542f') >= 0;
  cur.rows.push({attr: attr || lastAttr, own: !!attr, on: on, qty: q && q.value ? parseInt(q.value, 10) || 0 : 0,
                 date: dt ? (dt.value || '') : '', cells: cells});
});
return JSON.stringify(blocks);
"""


async def read_code(tab, code: str, spu: str) -> dict:
    """返回 {尺码: {on, date, qty, stock}}。

    ⚠ 一个尺码有「恒信加州3」「加州三仓」两行：在途预售库存只填在其中一行，
      所以要按属性集把整个 SKC 区块的两行合并（qty 取最大、日期取第一个非空）。
    """
    try:
        await B.close_overlays(tab)
    except Exception:  # noqa: BLE001
        pass
    await B.pick_query_type(tab, "SPU")
    st = await B.search(tab, spu)
    if not (st or {}).get("hit"):
        return {"_err": "查询没命中（rows=%s）" % (st or {}).get("rows")}
    if not await B.dialog_open(tab):
        r = await B.open_stock_dialog(tab, spu)
        if not await B.wait_dialog(tab, 12):
            return {"_err": "弹窗打不开：%s" % r}
    try:
        raw = await B.ev(tab, READ_JS)
        try:
            blocks = json.loads(raw or "[]")
        except Exception:  # noqa: BLE001
            return {"_err": "解析弹窗失败"}
        if isinstance(blocks, dict):
            return {"_err": blocks.get("err") or "no-dialog"}
        blk = None
        for x in blocks:
            if str(x.get("code") or "").upper() == code.upper():
                blk = x
                break
        if blk is None:
            return {"_err": "弹窗里没有货号 %s（有：%s）"
                            % (code, ",".join(str(x.get("code")) for x in blocks))}
        out: dict = {}
        for r in blk.get("rows") or []:
            attr = str(r.get("attr") or "")
            if not attr:
                continue
            size = _size(attr)
            cur = out.setdefault(size, {"on": False, "date": "", "qty": 0, "stock": None,
                                        "whQty": {}})
            cur["on"] = cur["on"] or bool(r.get("on"))
            cur["qty"] = max(int(cur["qty"] or 0), int(r.get("qty") or 0))
            if not cur["date"] and r.get("date"):
                cur["date"] = str(r.get("date"))
            cells = r.get("cells") or []
            # 发货仓名（这一行是哪个仓）：恒信加州3 / 加州三仓 / kansas仓 …
            wh = ""
            for c in cells:
                if ("恒信" in c) or ("加州" in c) or ("kansas" in c.lower()) or ("仓" in c and len(c) <= 12):
                    wh = c
                    break
            q_in_row = int(r.get("qty") or 0)
            if wh:
                cur["whQty"][wh] = q_in_row
            # 「当前库存」= 属性集那一行里、库存在「卖家自填写库存」后面的数字
            if cur["stock"] is None:
                for i, c in enumerate(cells):
                    if c == "卖家自填写库存" and i + 1 < len(cells) and cells[i + 1].isdigit():
                        cur["stock"] = int(cells[i + 1])
                        break
        return out
    finally:
        try:
            await B.close_dialog(tab)
        except Exception:  # noqa: BLE001
            pass


async def run(stores: list, dry: bool) -> int:
    live = json.loads(LIVE.read_text(encoding="utf-8"))
    changed = 0
    for store in stores:
        blk = (live.get("stores") or {}).get(store) or {}
        rows = blk.get("rows") or []
        todo = {}
        for r in rows:
            if r.get("presaleOn"):
                todo.setdefault((r.get("code"), r.get("spu")), set()).add(str(r.get("size")).upper())
        if not todo:
            print("[%s] 没有开预售的货号，跳过" % store)
            continue
        print("[%s] %d 个预售货号要读弹窗" % (store, len(todo)))
        tab = await setup(store)
        try:
            for (code, spu), sizes in sorted(todo.items()):
                got = await read_code(tab, code, spu)
                if "_err" in got:
                    print("  %-26s ✗ %s" % (code, got["_err"]))
                    continue
                on_sizes = [s for s, f in got.items() if f.get("on")]
                print("  %-26s 弹窗已开启 %-22s %s" % (
                    code, ",".join(sorted(on_sizes)) or "无",
                    " ".join("%s:qty=%s/%s" % (s, (got[s] or {}).get("qty"), (got[s] or {}).get("date"))
                             for s in sorted(on_sizes))))
                for r in rows:
                    if r.get("code") != code:
                        continue
                    f = got.get(str(r.get("size")).upper())
                    if not f:
                        continue
                    r["presaleOn"] = bool(f.get("on")) or r.get("presaleOn")
                    r["presaleQty"] = int(f.get("qty") or 0)
                    r["presaleDate"] = str(f.get("date") or "")
                    changed += 1
        finally:
            try:
                await tab.keep_tab()
            except Exception:  # noqa: BLE001
                pass
    if not dry and changed:
        LIVE.write_text(json.dumps(live, ensure_ascii=False, indent=1), encoding="utf-8")
        print("回写 %d 行 → %s" % (changed, LIVE))
    elif dry:
        print("--dry-run：读到 %d 行，未回写" % changed)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="ALL")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    stores = list(WINDOWS) if a.store.upper() == "ALL" else [a.store]
    for s in stores:
        if s not in WINDOWS:
            raise SystemExit("未知店铺 %s" % s)
    return asyncio.run(run(stores, a.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
