# -*- coding: utf-8 -*-
"""shein_onlisting_audit.py — 「在售款 × 领星总库存」逐格对账表。

用户 2026-11-12：「重新检查 BAN 在售款，按照领星总库存去对应库存」。
把每个**在售**（商家库存导出里的）商家SKU 摊成一行，写清库存是怎么加出来的：

    领星恒信可用（LM739/恒信 USW01）
  + 帕沃尔 加州3仓 + 帕沃尔 kansas仓
  ──────────────────────────────────
  = 仓内总库存（= 可售天数的分子）

再带上 7 天日均 / 可售天数 / 线上现有 / 本轮决策，方便一眼看出「仓库里到底有没有」。

    python scripts\\goods\\shein_onlisting_audit.py --store BAN
    python scripts\\goods\\shein_onlisting_audit.py --store BD --export auto --out D:\\x.xlsx
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import fetch_stock_track as fst      # noqa: E402
import run_shein_stock as R          # noqa: E402
import shein_stock_api as api        # noqa: E402
import shein_stock_sync as sync      # noqa: E402
import shein_zero_plan as zp         # noqa: E402


def build_rows(store: str, export: str = "auto") -> list:
    path = R.find_export(store) if export == "auto" else Path(export)
    if not path:
        raise SystemExit("找不到 %s 的商家库存导出" % store)
    exp = zp.load_export(str(path))
    seen, by_seller = set(), {}
    for e in exp:
        if e["seller"] in seen:
            continue
        seen.add(e["seller"])
        by_seller[e["seller"]] = e
    panel = sync.load_panel()
    panel = sync.apply_rules(panel, store)   # 替代/拆解：替发款（如 203318-2B←MATERNITYSETS-2B）要算进来
    live = api.query([e["sku"] for e in by_seller.values()], store)
    out = []
    for seller, e in sorted(by_seller.items()):
        key = sync.norm_key(seller)      # 与面板同一口径（BS2-/N 前缀都能吃）
        p = panel.get(key) if key else None
        lv = live.get(e["sku"]) or {}
        out.append(dict(
            seller=seller, sku=e["sku"],
            code=key[0] if key else "", variant=key[1] if key else "", size=key[2] if key else "",
            线上=lv.get("usable") or 0,
            领星可用=(p or {}).get("lxAvail"),
            帕沃尔加州3=(p or {}).get("pw3"),
            帕沃尔kansas=(p or {}).get("pwk"),
            仓内总库存=(p or {}).get("total"),
            在途=(p or {}).get("transitAll"),
            日均=round(float((p or {}).get("daily") or 0), 2),
            可售天数=(p or {}).get("days"),
            面板行="有" if p else "无",
            导出仓=e.get("whcode") or "",
            线上仓=",".join((lv.get("warehouses") or {}).keys()),
        ))
    return out


def write_xlsx(rows: list, path: Path, store: str) -> Path:
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "在售款对账"
    cols = ["商家SKU", "平台SKU", "款号", "变体", "尺码", "线上现有",
            "领星可用", "帕沃尔加州3", "帕沃尔kansas", "仓内总库存", "在途",
            "7天日均", "可售天数", "面板行", "导出仓", "线上仓"]
    ws.append(cols)
    for r in rows:
        ws.append([r["seller"], r["sku"], r["code"], r["variant"], r["size"], r["线上"],
                   r["领星可用"], r["帕沃尔加州3"], r["帕沃尔kansas"], r["仓内总库存"],
                   r["在途"], r["日均"], r["可售天数"], r["面板行"], r["导出仓"], r["线上仓"]])
    for c in ws[1]:
        c.font = Font(bold=True)
    for i, w in enumerate([24, 17, 9, 8, 6, 9, 9, 11, 12, 11, 8, 9, 10, 8, 20, 30], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    ws2 = wb.create_sheet("异常")
    ws2.append(["类型", "商家SKU", "说明"])
    for r in rows:
        if r["面板行"] == "无":
            ws2.append(["面板无行", r["seller"], "领星/帕沃尔里找不到这个 款-变体-尺码，仓内未知"])
        elif (r["仓内总库存"] or 0) <= 0 and r["线上"] > 0:
            ws2.append(["仓内0但线上有货", r["seller"], "线上 %s 件、仓内 0、在途 %s"
                        % (r["线上"], r["在途"])])
        elif (r["仓内总库存"] or 0) > 0 and r["线上"] == 0:
            ws2.append(["有货但线上0", r["seller"], "仓内 %s 件，线上挂着 0"
                        % round(r["仓内总库存"] or 0)])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for i, w in enumerate([18, 24, 70], 1):
        ws2.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--export", default="auto")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    store = a.store.upper()
    rows = build_rows(store, a.export)
    base = Path(r"D:\together\全托管\SHEIN") / ("%s-库存" % store)
    out = Path(a.out) if a.out else base / ("在售款对账_%s.xlsx"
                                            % datetime.now().strftime("%Y%m%d_%H%M"))
    p = write_xlsx(rows, out, store)
    n_no = sum(1 for r in rows if r["面板行"] == "无")
    n_zero_online = sum(1 for r in rows if (r["仓内总库存"] or 0) <= 0 and r["线上"] > 0)
    n_stock_off = sum(1 for r in rows if (r["仓内总库存"] or 0) > 0 and r["线上"] == 0)
    print("[audit] %s 在售 %d 个商家SKU｜面板无行 %d｜仓内0但线上有货 %d｜有货但线上0 %d"
          % (store, len(rows), n_no, n_zero_online, n_stock_off))
    print("[audit] →", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
