# -*- coding: utf-8 -*-
"""把六店「缺货与失败订单」汇总成一个表（用户 2026-11-25 要求）。

规则：
  · 每个 sheet 先写一行「店」标识列，再按**统一表头**合并
  · 不同店的 sheet 名带店前缀（`BHY 恒信仓` / `BAN 恒信仓`）⇒ 归并成 `恒信仓`
  · 统一表头：`店铺 | 参考单号 | 平台SKU | 仓库/备注 | 原始sheet`
  · 输出：`out/<日期>/<日期>-发货汇总.xlsx`（与用户截图同名风格）

用法：
    python merge_store_reports.py --day 2026-11-25
    python merge_store_reports.py --day 2026-11-25 --upload   # 顺带传金山
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(_HERE, "out")

#: 归一化 sheet 类型（去掉店前缀后的名字）
SHEET_TYPES = ("恒信仓", "帕沃尔仓", "缺货", "预售", "15天预售", "失败订单")
HEADER = ["店铺", "参考单号", "平台SKU", "仓库/备注", "来源sheet"]


def _norm_sheet(name: str) -> str:
    """`BHY 恒信仓` → `恒信仓`；`失败订单` 原样。"""
    n = str(name or "").strip()
    for t in sorted(SHEET_TYPES, key=len, reverse=True):
        if n.endswith(t):
            return t
    return n


def merge(day: str) -> str:
    from openpyxl import Workbook, load_workbook  # noqa: PLC0415
    from openpyxl.styles import Font  # noqa: PLC0415

    files = sorted(glob.glob(os.path.join(OUT_ROOT, day, "*_缺货与失败订单_*.xlsx")))
    if not files:
        raise RuntimeError("没有找到 %s 的缺货与失败订单表" % day)

    buckets: dict = {}      # 类型 → [(店, 行)]
    for f in files:
        m = re.match(r"(.+?)_缺货与失败订单_", os.path.basename(f))
        store = m.group(1) if m else os.path.basename(f)
        wb = load_workbook(f, read_only=True)
        for sn in wb.sheetnames:
            typ = _norm_sheet(sn)
            ws = wb[sn]
            rows = list(ws.iter_rows(min_row=2, values_only=True))
            if not rows:
                continue
            buckets.setdefault(typ, [])
            for r in rows:
                if not r or not r[0]:
                    continue
                vals = [("" if v is None else str(v)) for v in r]
                po = vals[0]
                sku = vals[1] if len(vals) > 1 else ""
                extra = vals[2] if len(vals) > 2 else ""
                buckets[typ].append((store, po, sku, extra, sn))
        wb.close()

    out = os.path.join(OUT_ROOT, day, "%s-发货汇总.xlsx" % day)
    wb = Workbook()
    wb.remove(wb.active)
    order = [t for t in SHEET_TYPES if t in buckets] + \
            [t for t in buckets if t not in SHEET_TYPES]
    for typ in order:
        rows = buckets[typ]
        ws = wb.create_sheet(typ[:31])
        ws.append(HEADER)
        for c in ws[1]:
            c.font = Font(bold=True)
        for store, po, sku, extra, sn in rows:
            ws.append([store, po, sku, extra, sn])
        for col, w in zip("ABCDE", (16, 26, 28, 34, 22)):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "A2"
        print("  %-10s %d 行" % (typ, len(rows)))
    wb.save(out)
    print("已生成 →", out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="六店缺货/失败汇总")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD")
    ap.add_argument("--upload", action="store_true", help="生成后传金山「失败订单」")
    a = ap.parse_args()
    out = merge(a.day)
    if a.upload:
        import kd_upload_report as U  # noqa: PLC0415
        print(U.upload_all([out]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
