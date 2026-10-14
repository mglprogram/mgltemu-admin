# -*- coding: utf-8 -*-
"""把 bhy_flux_boost_price.json 导出为 xlsx + csv（流量加速中的 SPU 尺码申报价对照）。"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SRC = Path(r"D:\AI-workplace\TEMU\data\bhy\bhy_flux_boost_price.json")
OUT_XLSX = Path(r"D:\AI-workplace\TEMU\data\bhy\bhy_flux_boost_price.xlsx")
OUT_CSV = Path(r"D:\AI-workplace\TEMU\data\bhy\bhy_flux_boost_price.csv")

SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "2XL", "3XL"]


def money(v: str):
    m = re.search(r"\$?\s*([\d.]+)", str(v or ""))
    return float(m.group(1)) if m else None


def size_key(s: str) -> int:
    u = (s or "").upper().replace("XL", "XL")
    return SIZE_ORDER.index(u) if u in SIZE_ORDER else 99


def build_rows(records):
    rows = []
    for rec in records:
        skus = sorted(rec["skus"], key=lambda x: size_key(x.get("size")))
        for k in skus:
            orig, after = money(k.get("orig")), money(k.get("after"))
            drop = round(orig - after, 2) if (orig is not None and after is not None) else None
            pct = round(drop / orig * 100, 1) if (orig and drop is not None) else None
            rows.append({
                "SPU ID": rec.get("spu"),
                "商品标题": (rec.get("title") or "")[:120],
                "流量加速状态": rec.get("state"),
                "加速开始": rec.get("period_from"),
                "加速到期": rec.get("period_to"),
                "SKC": k.get("skc"),
                "颜色": k.get("color"),
                "尺码": k.get("size"),
                "原日常申报价(USD)": orig,
                "调整后申报价(USD)": after,
                "降幅(USD)": drop,
                "降幅(%)": pct,
            })
    return rows


HEADERS = ["SPU ID", "商品标题", "流量加速状态", "加速开始", "加速到期",
           "SKC", "颜色", "尺码", "原日常申报价(USD)", "调整后申报价(USD)",
           "降幅(USD)", "降幅(%)"]


def main():
    records = json.loads(SRC.read_text(encoding="utf-8"))
    rows = build_rows(records)
    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "尺码申报价明细"
    ws.append(HEADERS)
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = hdr_fill
        c.alignment = Alignment(horizontal="center", vertical="center")
    for r in rows:
        ws.append([r[h] for h in HEADERS])
    widths = [13, 58, 12, 21, 21, 12, 10, 8, 18, 20, 12, 10]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    for row in ws.iter_rows(min_row=2, min_col=9, max_col=12):
        for c in row:
            if c.column in (9, 10, 11):
                c.number_format = "0.00"
            elif c.column == 12:
                c.number_format = '0.0"%"'

    # SPU 汇总页
    ws2 = wb.create_sheet("SPU汇总")
    hdr2 = ["SPU ID", "商品标题", "尺码数", "原日常申报价(USD)", "调整后申报价(USD)",
            "降幅(USD)", "降幅(%)", "加速开始", "加速到期", "状态"]
    ws2.append(hdr2)
    for c in ws2[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = hdr_fill
        c.alignment = Alignment(horizontal="center", vertical="center")
    for rec in records:
        skus = rec["skus"]
        origs = {money(s.get("orig")) for s in skus}
        afters = {money(s.get("after")) for s in skus}
        o = next((x for x in origs if x is not None), None)
        a = next((x for x in afters if x is not None), None)
        drop = round(o - a, 2) if (o is not None and a is not None) else None
        ws2.append([rec.get("spu"), (rec.get("title") or "")[:120], len(skus), o, a, drop,
                    round(drop / o * 100, 1) if (o and drop is not None) else None,
                    rec.get("period_from"), rec.get("period_to"), rec.get("state")])
    for i, w in enumerate([13, 58, 8, 18, 20, 12, 10, 21, 21, 12], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    ws2.freeze_panes = "A2"
    for row in ws2.iter_rows(min_row=2, min_col=4, max_col=7):
        for c in row:
            c.number_format = "0.0" if c.column == 7 else "0.00"

    # 数据说明页
    ws3 = wb.create_sheet("数据说明")
    info = [
        ("数据源", "TEMU 商家中心 → 经营分析 → 流量分析 → 商品流量（美国站）"),
        ("筛选", "快速筛选 → 流量加速中"),
        ("价格明细", "每行「查看效果」→「近期流量加速效果」→「流量加速申报价格明细」"),
        ("抓取时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("SPU 数", len(records)),
        ("SKU 明细行数", len(rows)),
        ("说明", "全程只读，未执行任何提效/一键提效/停止加速等写操作"),
    ]
    for k, v in info:
        ws3.append([k, v])
    ws3.column_dimensions["A"].width = 16
    ws3.column_dimensions["B"].width = 90
    for c in ws3["A"]:
        c.font = Font(bold=True)

    wb.save(OUT_XLSX)

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"SPU {len(records)}，明细 {len(rows)} 行")
    print("->", OUT_XLSX)
    print("->", OUT_CSV)


if __name__ == "__main__":
    main()