# -*- coding: utf-8 -*-
"""shein_onsale_summary.py — SHEIN 在售款汇总（按款号聚合，BD / BAN）。

数据来源（都是只读）：
    ① 商家库存导出 merchant-stock-export-*.xlsx  → 这个店有哪些「商家SKU」（= 在售、可改库存）
    ② POST /open-api/stock/stock-query            → 线上当前可用/锁定/总
    ③ 面板（linxing-stock.json + pw-stock.json）  → 仓内库存、7天日均、可售天数

用法：
    python scripts\\goods\\shein_onsale_summary.py --store BD BAN
    python scripts\\goods\\shein_onsale_summary.py --store BD --out "D:\\x.xlsx"
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import openpyxl                      # noqa: E402
from openpyxl.styles import Font     # noqa: E402

import shein_stock_api as api        # noqa: E402
import shein_stock_sync as sync      # noqa: E402
import shein_zero_plan as zp         # noqa: E402

DOWNLOADS = Path.home() / "Downloads"
OUT_DIR = Path(r"D:\together\全托管\SHEIN")


def find_export(store: str) -> Path | None:
    for p in sorted(DOWNLOADS.glob("merchant-stock-export-*.xlsx"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        if "_清零" in p.name or "_v2" in p.name:
            continue
        try:
            exp = zp.load_export(str(p))
        except Exception:
            continue
        probe, seen = [], set()
        for e in exp:
            pref = e["seller"].split("-", 1)[0]
            if pref in seen:
                continue
            seen.add(pref)
            probe.append(e["sku"])
            if len(probe) >= 3:
                break
        try:
            if api.query(probe, store):
                return p
        except BaseException:
            continue
    return None


def collect(store: str, export: Path, panel: dict):
    exp = zp.load_export(str(export))
    by_seller: dict[str, dict] = {}
    for e in exp:
        by_seller.setdefault(e["seller"], e)
    codes = sorted({e["sku"] for e in by_seller.values()})
    live = api.query(codes, store) if codes else {}

    def key_of(seller: str):
        body = seller.split("-", 1)[1].lower() if "-" in seller else seller.lower()
        parts = body.split("-")
        if len(parts) < 2:
            return None
        return (parts[0], "-".join(parts[1:-1]), parts[-1])

    style_of = lambda s: s.rsplit("-", 1)[0]     # BD-204203-4SetA-XL -> BD-204203-4SetA
    rows, styles = [], {}
    for seller, e in sorted(by_seller.items()):
        lv = live.get(e["sku"]) or {}
        p = panel.get(key_of(seller) or ()) or {}
        usable = lv.get("usable")
        rows.append(dict(store=store, seller=seller, style=style_of(seller),
                         sku=e["sku"], size=seller.rsplit("-", 1)[-1],
                         usable=usable, total=lv.get("total"), locked=lv.get("locked"),
                         stock_on_hand=p.get("total"), daily=p.get("daily"),
                         days=p.get("days"), in_live=bool(lv)))
        st = styles.setdefault(style_of(seller), dict(sizes=0, skus=0, usable=0, total=0,
                                                      stock=0.0, daily=0.0, days=None, live=0))
        st["sizes"] += 1
        st["skus"] += 1
        st["usable"] += usable or 0
        st["total"] += lv.get("total") or 0
        st["stock"] += p.get("total") or 0
        st["daily"] += p.get("daily") or 0
        st["live"] += 1 if lv else 0
    for s in styles.values():
        s["days"] = round(s["stock"] / s["daily"], 1) if s["daily"] > 0 else None
    return rows, styles


def main(argv=None):
    ap = argparse.ArgumentParser(description="SHEIN 在售款汇总（按款号）")
    ap.add_argument("--store", nargs="+", default=["BD", "BAN"])
    ap.add_argument("--out", default="")
    ap.add_argument("--export", default="auto")
    a = ap.parse_args(argv)

    panel = sync.load_panel()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    all_styles = []
    print("面板: %s\n" % sync.data_ages())

    for store in [s.upper() for s in a.store]:
        export = Path(a.export) if a.export != "auto" else find_export(store)
        if not export:
            print("!! %s 找不到匹配的商家库存导出" % store)
            continue
        rows, styles = collect(store, export, panel)
        print("== %-4s 导出=%s | 在售商家SKU %d 个 / 款号 %d 个"
              % (store, export.name, len(rows), len(styles)))

        ws = wb.create_sheet("%s-款号汇总" % store)
        ws.append(["款号", "尺码数", "线上可用合计", "线上总库存", "仓内库存", "7天日均",
                   "可售天数", "线上是否有记录"])
        for k in sorted(styles, key=lambda x: (-styles[x]["usable"], x)):
            s = styles[k]
            ws.append([k, s["sizes"], s["usable"], s["total"], round(s["stock"], 1),
                       round(s["daily"], 2), s["days"], s["live"]])
            all_styles.append(dict(store=store, style=k, **s))
        for col, w in zip(ws.iter_cols(min_row=1, max_row=1), (26, 8, 14, 12, 11, 10, 10, 14)):
            ws.column_dimensions[col[0].column_letter].width = w
        for c in ws[1]:
            c.font = Font(bold=True)

        ws2 = wb.create_sheet("%s-逐尺码" % store)
        ws2.append(["款号", "商家SKU", "平台SKU", "尺码", "线上可用", "线上总", "锁定",
                    "仓内库存", "7天日均", "可售天数", "线上有记录"])
        for r in rows:
            ws2.append([r["style"], r["seller"], r["sku"], r["size"], r["usable"], r["total"],
                        r["locked"], r["stock_on_hand"], r["daily"], r["days"], r["in_live"]])
        for col, w in zip(ws2.iter_cols(min_row=1, max_row=1), (24, 24, 16, 6, 10, 10, 8, 10, 10, 10, 12)):
            ws2.column_dimensions[col[0].column_letter].width = w
        for c in ws2[1]:
            c.font = Font(bold=True)

    out = Path(a.out) if a.out else OUT_DIR / ("在售款汇总_%s.xlsx" % datetime.now().strftime("%Y%m%d_%H%M"))
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print("\n汇总表 → %s" % out)

    print("\n== 按款号汇总（线上可用降序）==")
    print("| 店 | 款号 | 尺码数 | 线上可用 | 仓内 | 7天日均 | 可售天数 |")
    print("|---|---|---|---|---|---|---|")
    for s in sorted(all_styles, key=lambda x: -(x["usable"] or 0)):
        print("| %s | %s | %d | %d | %.0f | %.2f | %s |" % (
            s["store"], s["style"], s["sizes"], s["usable"], s["stock"], s["daily"],
            s["days"] if s["days"] is not None else "—"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
