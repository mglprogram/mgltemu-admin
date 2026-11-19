# -*- coding: utf-8 -*-
"""按 **SPU** 查询，把该款所有尺码从「合作对接仓发货」切到「卖家自发货」（用户 2026-11-19 要求）。

为什么不逐 SKU 搜：货号下拉选 **SPU** 时，一次查询就列出该款 5 个尺码，
逐个尺码再搜一遍是 5 倍的无用功（还容易把「SKU 模糊查询」和别人同名款混在一起）。

链路（沿用 switch_mode_batch 已验证的那套）：
    SPU 查询 → 表格 5 行 → 对仍是「合作对接仓发货」的行点它自己那格铅笔
      → 弹框：发货模式下拉选「卖家自发货」→ 填两行库存 → 三层确认
      → 回读该行发货模式 = 卖家自发货
填数口径（同台账）：两行时 加州3 = max(1, 原库存-1)、kansas = 1；一行时 = max(1, 原库存)。

用法：
    python scripts/goods/switch_mode_by_spu.py --port 59670 --spus 2946934746 --apply
    python scripts/goods/switch_mode_by_spu.py --port 59670 --items _logs/bel_switch_left.json --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import goto_spu as gs  # noqa: E402
import switch_mode_batch as SMB  # noqa: E402
import zreact  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "_logs"
LEDGER = LOGS / "switch_by_spu.jsonl"

JS_ROWS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
Array.prototype.forEach.call(document.querySelectorAll('tbody tr'), function (tr) {
  var s = t(tr);
  var m = s.match(/尺码[:：]\s*([A-Za-z0-9]+)/);
  if (!m) return;
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var mode = '';
  cells.forEach(function (c) { if (c === '卖家自发货' || c === '合作对接仓发货') mode = c; });
  var sku = '';
  cells.forEach(function (c) { if (/^[A-Z]{2,3}-[A-Za-z0-9\-]+$/.test(c) && !sku) sku = c; });
  out.push({size: m[1], mode: mode, sku: sku});
});
return JSON.stringify(out);"""

JS_PENCIL_ROW = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var size = %s;
var tr = Array.prototype.slice.call(document.querySelectorAll('tbody tr')).filter(function (r) {
  var m = t(r).match(/尺码[:：]\s*([A-Za-z0-9]+)/);
  return m && m[1] === size; })[0];
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


def log(rec: dict) -> None:
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False), flush=True)


async def read_rows(cap):
    v = await gs.ev(cap, JS_ROWS)
    try:
        return json.loads(v)
    except Exception:
        return []


async def open_dialog_row(cap, size: str) -> bool:
    for _ in range(4):
        xy = await gs.ev(cap, JS_PENCIL_ROW % json.dumps(size))
        if not str(xy).startswith("["):
            return False
        await SMB._click(cap, json.loads(xy))
        for _w in range(5):
            await asyncio.sleep(1)
            if await SMB._ev(cap, SMB.JS_DLG) == "open":
                return True
    return False


async def switch_one_row(cap, size: str, stock: int) -> dict:
    out = {"size": size, "stock": stock, "ok": False}
    if not await open_dialog_row(cap, size):
        out["why"] = "弹框没打开"
        return out
    out["switched"] = await SMB._switch_mode(cap)
    out["filled"] = await SMB._fill_cells(cap, stock)
    if not out["filled"]:
        out["why"] = "库存没填上"
        await SMB._close_dialog(cap) if hasattr(SMB, "_close_dialog") else None
        return out
    await SMB._confirm_all(cap)
    await asyncio.sleep(12)
    rows = await read_rows(cap)
    row = next((r for r in rows if r["size"] == size), None)
    out["after"] = None if row is None else row.get("mode")
    out["ok"] = bool(row and row.get("mode") == "卖家自发货")
    if not out["ok"]:
        out["why"] = "回读=%s" % (out["after"],)
    return out


async def main_async(a) -> int:
    # 目标：(SPU, 货号) + {货号-尺码: 库存}
    if a.items:
        items = json.loads(Path(a.items).read_text(encoding="utf-8"))
        spu_code = {}
        stock_of = {}
        for it in items:
            code = it["sku"].rsplit("-", 1)[0]
            spu_code[it["spu"]] = code
            stock_of[it["sku"]] = int(it.get("stock") or 0)
        spus = list(dict.fromkeys(it["spu"] for it in items))
    else:
        spu_code = {}
        stock_of = {}
        spus = list(a.spus or [])
        plan = json.loads((LOGS / "bel_blocked_skus2.json").read_text(encoding="utf-8"))
        for it in plan:
            spu_code.setdefault(it["spu"], it["sku"].rsplit("-", 1)[0])
            stock_of[it["sku"]] = int(it.get("stock") or 0)

    cap = await gs.focus_list_page(a.port)
    if cap is None:
        print("没有商品列表页")
        return 2
    ok = fail = skip = 0
    try:
        await cap.send("Page.bringToFront", {}, timeout=10)
        await zreact.inject(cap)
        for spu in spus:
            code = spu_code.get(spu, "")
            got, detail = await gs.goto_spu(cap, spu, expect_code=code)
            if not got:
                log({"event": "spu-fail", "spu": spu, "code": code, "why": str(detail)[:120]})
                fail += 1
                continue
            rows = await read_rows(cap)
            log({"event": "spu", "spu": spu, "code": code,
                 "rows": [(r["size"], r["mode"]) for r in rows]})
            for r in rows:
                sku = "%s-%s" % (code, r["size"])
                if r.get("mode") == "卖家自发货":
                    skip += 1
                    log({"event": "already", "sku": sku})
                    continue
                stock = stock_of.get(sku, 0)
                if not a.apply:
                    log({"event": "would", "sku": sku, "stock": stock})
                    continue
                res = await switch_one_row(cap, r["size"], stock)
                res.update({"sku": sku, "spu": spu})
                log({"event": "row", **res})
                if res.get("ok"):
                    ok += 1
                else:
                    fail += 1
                await asyncio.sleep(2)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    log({"event": "summary", "ok": ok, "fail": fail, "skip": skip, "apply": bool(a.apply)})
    return 0 if fail == 0 else 5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=59670)
    ap.add_argument("--spus", action="append", help="按 SPU 逐个切；可重复")
    ap.add_argument("--items", default="", help="清单 json（bel_blocked_skus2.json 格式），按 SPU 归并")
    ap.add_argument("--apply", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
