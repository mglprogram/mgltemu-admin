# -*- coding: utf-8 -*-
"""presale15_orders.py —— 把「15 天预售」的单从当天的 split.json 里挑出来，单出一张表。

用户 2026-11-19：「已开 15 天预售款号-尺码数据库 …… 把这些款号-尺码收录到发货最后的表格
加一个 15 天预售的订单」。名单在 `config/temu_presale_15d.json`。

本脚本是**补表/预览**用（只读 split.json，不碰平台、不改当天正式表）：
    python scripts/power/presale15_orders.py --date 2026-11-19
    python scripts/power/presale15_orders.py --date 2026-11-19 --out out/xxx.xlsx

正式流程（明天的发货任务）不需要跑它 —— `bel_split_by_stock` 会直接把整单归到
`PRESALE15` 组，`gen_shortage_fail_xlsx` 出 `<店> 15天预售` sheet，汇总表自动带上。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_WS = _HERE.parents[1]
sys.path.insert(0, str(_HERE))

import bel_split_by_stock as B  # noqa: E402

STORES = ("BHY", "BEL")
OUT_ROOT = _WS / "scripts" / "power" / "out"


def collect(day: str) -> dict:
    """{店: [行]}；行里有 参考单号/平台SKU/件数/原分组。"""
    out: dict = {}
    for store in STORES:
        p = OUT_ROOT / day / "fulfill" / store / "split.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = []
        for grp, arr in (data.get("groups") or {}).items():
            for r in arr or []:
                sku = str(r.get("sku") or "")
                if not B.is_presale15(sku, store):
                    continue
                rows.append({
                    "店铺": store,
                    "参考单号": str(r.get("ord") or ""),
                    "平台SKU": sku,
                    "件数": int(r.get("qty") or 1),
                    "原分组": grp,
                })
        rows.sort(key=lambda x: (x["平台SKU"], x["参考单号"]))
        if rows:
            out[store] = rows
    return out


def write(day: str, data: dict, out: str) -> str:
    from openpyxl import Workbook  # noqa: PLC0415
    from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415
    from openpyxl.utils import get_column_letter  # noqa: PLC0415

    wb = Workbook()
    ws = wb.active
    ws.title = "汇总"
    ws.append(["店铺", "15天预售订单数", "行数", "件数", "名单"])
    for st, rows in data.items():
        ws.append([st, len({r["参考单号"] for r in rows}), len(rows),
                   sum(r["件数"] for r in rows), B.presale15_desc(st)])
    hdr = PatternFill("solid", fgColor="1F4E79")
    for c in ws[1]:
        c.fill = hdr
        c.font = Font(color="FFFFFF", bold=True)
        c.alignment = Alignment(horizontal="center")
    for i, w in enumerate([12, 16, 8, 8, 70], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    cols = ["参考单号", "平台SKU", "件数", "原分组"]
    for st, rows in data.items():
        s = wb.create_sheet("%s 15天预售" % st)
        s.append(cols)
        for c in s[1]:
            c.fill = PatternFill("solid", fgColor="FDE9D9")
            c.font = Font(bold=True)
            c.alignment = Alignment(horizontal="center")
        for r in rows:
            s.append([r[c] for c in cols])
        for i, w in enumerate([30, 32, 8, 12], 1):
            s.column_dimensions[get_column_letter(i)].width = w
        s.freeze_panes = "A2"
        s.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(cols)), max(1, len(rows) + 1))

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    wb.save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="挑出当天的 15 天预售订单，单出一张表")
    ap.add_argument("--date", required=True, help="日期，如 2026-11-19")
    ap.add_argument("--out", default="", help="输出 xlsx（默认 out/15天预售单_<日期>.xlsx）")
    a = ap.parse_args()
    data = collect(a.date)
    if not data:
        print("[%s] 没有 15 天预售的单（split.json 里一条都没命中）" % a.date)
        return 0
    out = a.out or str(_WS / "out" / ("15天预售单_%s.xlsx" % a.date.replace("-", "")))
    real = write(a.date, data, out)
    for st, rows in data.items():
        print("[%s] %d 单 / %d 行 / %d 件（原分组：%s）"
              % (st, len({r["参考单号"] for r in rows}), len(rows),
                 sum(r["件数"] for r in rows),
                 ",".join(sorted({r["原分组"] for r in rows}))))
    print("已写入:", real)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
