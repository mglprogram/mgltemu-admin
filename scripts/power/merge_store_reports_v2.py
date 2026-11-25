# -*- coding: utf-8 -*-
"""按用户截图的样式汇总：**每个「店 + 类型」一个 sheet**（不合并类型）。

用户 2026-11-25 截图显示 sheet 名形如：
    BHY 帕沃尔仓 | BHY 缺货 | BHY 预售 | BHY 15天预售 | BHY 失败订单
    BEL 恒信仓 | BEL 帕沃尔仓 | BEL 缺货 | BEL 预售 | BEL 15天预售 | ...
⇒ 与原单店文件同构，只是**所有店放同一本工作簿**、按店排序。

表结构保持各 sheet 原样（不加「店铺」列，因为 sheet 名已带店名）。

用法：
    python merge_store_reports.py --day 2026-11-25 --per-store
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(_HERE, "out")
#: 店铺排序（TEMU 三店在前，SHEIN 店在后）
STORE_ORDER = ["BHY", "BEL", "KinglamanSport", "BS", "BD", "BAN", "BE"]


def _store_rank(s: str) -> tuple:
    try:
        return (STORE_ORDER.index(s), s)
    except ValueError:
        return (len(STORE_ORDER), s)


def merge_per_store(day: str, upload: bool = False) -> str:
    from openpyxl import Workbook, load_workbook  # noqa: PLC0415
    wb = Workbook()
    wb.remove(wb.active)

    files = sorted(glob.glob(os.path.join(OUT_ROOT, day, "*_缺货与失败订单_*.xlsx")))
    if not files:
        raise RuntimeError("没有找到 %s 的缺货与失败订单表" % day)

    items = []
    for f in files:
        m = re.match(r"(.+?)_缺货与失败订单_", os.path.basename(f))
        store = m.group(1) if m else os.path.basename(f)
        items.append((store, f))
    items.sort(key=lambda x: _store_rank(x[0]))

    total = 0
    for store, f in items:
        src = load_workbook(f, read_only=True)
        for sn in src.sheetnames:
            ws_in = src[sn]
            rows = list(ws_in.iter_rows(values_only=True))
            if not rows:
                continue
            sheet_name = sn if sn.startswith(store) else "%s %s" % (store, sn)
            ws = wb.create_sheet(sheet_name[:31])
            for r in rows:
                ws.append([("" if v is None else v) for v in r])
            # 列宽按表头长度粗略给
            for i, c in enumerate(rows[0], 1):
                w = max(12, min(46, len(str(c or "")) + 10))
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
            ws.freeze_panes = "A2"
            n = max(0, len(rows) - 1)
            total += n
            print("  %-26s %d 行" % (sheet_name, n))
        src.close()

    out = os.path.join(OUT_ROOT, day, "%s-发货汇总.xlsx" % day)
    wb.save(out)
    print("已生成 → %s（%d 个 sheet，合计 %d 行）" % (out, len(wb.sheetnames), total))

    if upload:
        import kd_upload_report as U  # noqa: PLC0415
        print(U.upload_all([out]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="六店缺货/失败汇总")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD")
    ap.add_argument("--per-store", action="store_true",
                    help="按「店+类型」分 sheet（截图样式）；不加则按类型合并")
    ap.add_argument("--upload", action="store_true", help="生成后传金山「失败订单」")
    a = ap.parse_args()
    if a.per_store:
        merge_per_store(a.day, upload=a.upload)
        return 0
    import merge_store_reports as M  # noqa: PLC0415  (同文件走原路径)
    out = M.merge(a.day) if hasattr(M, "merge") else ""
    if a.upload and out:
        import kd_upload_report as U  # noqa: PLC0415
        print(U.upload_all([out]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
