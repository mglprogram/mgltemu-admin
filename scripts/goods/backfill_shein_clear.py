# -*- coding: utf-8 -*-
"""backfill_shein_clear.py — 从历史「计划_*.xlsx」回填 SHEIN 清零台账。

用户 2026-11-12：面板「修改库存 · 清零」的 SHEIN 页签要有 BD / BAN 清零的
款号-变体-尺码。pipeline 从今天起会自己记台账，**今天之前**的清零只存在
计划表里，这里扫一遍补进去。

    python scripts\\goods\\backfill_shein_clear.py                 # 扫描并回填（幂等）
    python scripts\\goods\\backfill_shein_clear.py --print         # 顺便打印台账
    python scripts\\goods\\backfill_shein_clear.py --dir "D:\\together\\全托管\\SHEIN"

去重口径与 pipeline 一致：店+款+变体+尺码；同一 SKU 多次清零只累加次数，
firstClearedAt 取最早那次（用计划表的修改时间当清零时间）。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import shein_stock_sync as sync  # noqa: E402

DEFAULT_DIR = Path(r"D:\together\全托管\SHEIN")


def scan(dir_path: Path) -> list:
    """返回 [(when, store, plan_row_dict)]，按时间升序。"""
    import openpyxl

    out = []
    files = sorted(dir_path.rglob("计划_*.xlsx"), key=lambda p: p.stat().st_mtime)
    for f in files:
        when = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        try:
            wb = openpyxl.load_workbook(f, read_only=True)
        except Exception as e:  # noqa: BLE001
            print("  跳过 %s（%s）" % (f.name, e))
            continue
        if "明细" not in wb.sheetnames:
            continue
        ws = wb["明细"]
        for r in ws.iter_rows(min_row=2, values_only=True):
            if not r or r[0] != "清零":
                continue
            seller = str(r[1] or "")
            if "-" not in seller:
                continue
            store = seller.split("-", 1)[0].upper()
            out.append((when, store, {
                "seller": seller,
                "sku": str(r[2] or ""),
                "now": r[6],            # 清零前线上
                "total": r[5],          # 仓内库存
                "daily": r[4],          # 日均
                "days": r[3],           # 可售天数
                "wh": "",
            }))
        wb.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    ap.add_argument("--print", action="store_true", dest="do_print")
    a = ap.parse_args()

    rows = scan(Path(a.dir))
    if not rows:
        print("没有找到「动作=清零」的计划行（%s）" % a.dir)
        return 1
    total_ad, total_up = 0, 0
    for when, store, row in rows:
        ad, up, tot = sync.record_clear(store, [row], when=when, source="计划表回填")
        total_ad += ad
        total_up += up
        print("  %s %s %-24s → 台账 %d 条" % (when, store, row["seller"], tot))
    items = sync.load_clear_ledger()
    print("\n回填完成：新增 %d / 更新 %d / 台账共 %d 条 → %s"
          % (total_ad, total_up, len(items), sync.CLEAR_LEDGER))
    if a.do_print:
        for x in items:
            print("  %-4s %-22s %-8s %-4s 清零前线上 %-5s 仓内 %-5s %s（第 %d 次）"
                  % (x["store"], x["code"], x.get("variant") or "", x["size"],
                     x.get("before"), x.get("total"), x["clearedAt"], x.get("times") or 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
