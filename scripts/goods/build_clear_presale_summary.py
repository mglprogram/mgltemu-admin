# -*- coding: utf-8 -*-
"""把三店后台实读快照（data/presale-live.json）整理成「货号-尺码」清单 xlsx。

用户 2026-11-18：「总结 三个 temu 店铺清零库存和开预售的货号-尺码」

产物：out/三店清零与预售_货号尺码_<YYYYMMDD>.xlsx
    · 汇总       每店一行：货号数 / 预售货号 / 清零货号（全码零 vs 部分零）
    · 已开预售   一行一个「货号-尺码」，带 TEMU 现货、预售到期、台账在途预售库存
    · 已清零     一行一个货号：零库存的尺码（合并成 M,L,XL）、仍有库存的尺码、是否全码零
只读，不改任何台账/平台。
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "data" / "presale-live.json"
LEDGER = ROOT / "data" / "presale-ledger.json"
OUT_DIR = ROOT / "out"
SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL", "5XL"]
STORES = ["BEL", "BHY", "KinglamanSport"]


def size_key(s: str):
    s = str(s or "").upper()
    return (SIZE_ORDER.index(s) if s in SIZE_ORDER else 99, s)


def join_sizes(sizes) -> str:
    return ",".join(sorted(set(sizes), key=size_key))


def main() -> int:
    live = json.loads(LIVE.read_text(encoding="utf-8"))
    led = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"items": []}
    qty = {}
    for x in led.get("items") or []:
        qty[(x.get("store"), x.get("code"), str(x.get("size")).upper())] = x.get("qty")

    rows_by = defaultdict(list)
    for st in STORES:
        for r in ((live.get("stores") or {}).get(st) or {}).get("rows") or []:
            rows_by[(st, str(r.get("code") or ""))].append(r)

    presale_rows, clear_rows, summary = [], [], []
    for st in STORES:
        codes = sorted(c for (s2, c) in rows_by if s2 == st)
        pre_codes = set()
        for (s2, code), rows in sorted(rows_by.items()):
            if s2 != st:
                continue
            pre = [r for r in rows if r.get("presaleOn")]
            zero = [r for r in rows if not r.get("presaleOn") and int(r.get("temuStock") or 0) == 0]
            ok = [r for r in rows if not r.get("presaleOn") and int(r.get("temuStock") or 0) > 0]
            if pre:
                pre_codes.add(code)
                for r in sorted(pre, key=lambda x: size_key(x.get("size"))):
                    presale_rows.append([
                        st, code, r.get("spu") or "", str(r.get("size") or "").upper(),
                        int(r.get("temuStock") or 0),
                        qty.get((st, code, str(r.get("size")).upper())) or "",
                        str(r.get("presaleEnd") or "")[:10],
                        str(r.get("readAt") or "")[:16],
                    ])
            if zero:
                all_zero = not ok and not pre
                clear_rows.append([
                    st, code, rows[0].get("spu") or "",
                    join_sizes([str(r.get("size") or "").upper() for r in zero]),
                    len(zero),
                    join_sizes([str(r.get("size") or "").upper() for r in ok]) or "—",
                    "是" if all_zero else "部分",
                    (",".join("%s:%s" % (str(r.get("size")).upper(), int(r.get("temuStock") or 0))
                              for r in sorted(ok, key=lambda x: size_key(x.get("size")))) or "—"),
                ])
        full_zero = sum(1 for r in clear_rows if r[0] == st and r[6] == "是")
        summary.append([
            st, len(codes), len(pre_codes),
            sum(1 for r in presale_rows if r[0] == st),
            sum(1 for r in clear_rows if r[0] == st),
            full_zero,
            sum(1 for r in clear_rows if r[0] == st) - full_zero,
            live.get("generatedAt") or "",
        ])

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except Exception as e:  # noqa: BLE001
        print("没有 openpyxl：", e)
        return 1

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="E8F0FE")

    def sheet(ws, headers, widths, data, freeze="A2"):
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = head_fill
            c.alignment = Alignment(horizontal="center")
        for row in data:
            ws.append(row)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = freeze
        ws.auto_filter.ref = ws.dimensions

    ws = wb.active
    ws.title = "汇总"
    sheet(ws, ["店铺", "货号数", "开预售货号", "预售尺码行", "有清零货号",
               "整款全码零", "部分尺码零", "数据时间"],
          [16, 9, 12, 12, 13, 12, 12, 20], summary)

    sheet(wb.create_sheet("已开预售"),
          ["店铺", "货号", "SPU", "尺码", "TEMU现货库存", "台账在途预售库存", "预售到期", "读取时间"],
          [16, 26, 14, 8, 14, 18, 12, 18], presale_rows)

    sheet(wb.create_sheet("已清零"),
          ["店铺", "货号", "SPU", "零库存尺码", "零码数", "仍有库存尺码", "是否全码零", "仍有库存明细"],
          [16, 26, 14, 28, 8, 22, 12, 30], clear_rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / ("三店清零与预售_货号尺码_%s.xlsx" % datetime.now().strftime("%Y%m%d"))
    wb.save(out)
    print("已写入:", out)
    print("汇总：")
    for r in summary:
        print("  %-16s 货号%-4d 预售%-3d(尺码 %d) 清零%-4d 其中整款全零 %d / 部分 %d"
              % (r[0], r[1], r[2], r[3], r[4], r[5], r[6]))
    print("预售货号（%d 个）：" % len({(r[0], r[1]) for r in presale_rows}))
    for st in STORES:
        cc = sorted({r[1] for r in presale_rows if r[0] == st})
        print("  %-16s %s" % (st, ", ".join(cc) if cc else "—"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
