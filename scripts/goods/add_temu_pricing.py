# -*- coding: utf-8 -*-
"""把「TEMU 供货价 / TEMU 回报率」从货盘价格表补进 goods.json。

数据源（用户指定）：
    D:\\together\\全托管\\TEMU\\BEL-货盘价格汇总-7.7.xlsx → sheet「货盘价格汇总」
    列：B=货号（不带店前缀，如 `102104-5SetA`）、E=TEMU供货价、J=TEMU回报率

注意：
  · 两店**共用同一份价表**（BHY 的 89 款也都命中），所以不加店过滤；
  · 价表里 `102104-5SetA1` 与 `102104-5SetA1.1` 是不同版本，**精确匹配、不做模糊**；
  · 回报率列是小数（0.19 = 19%），前端按百分比显示。

用法：
    python scripts/goods/add_temu_pricing.py              # 写回 goods.json
    python scripts/goods/add_temu_pricing.py --dry-run    # 只看命中率
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import openpyxl

ROOT = Path(r"D:\AI-workplace\TEMU")
GOODS = ROOT / "temu-admin" / "public" / "data" / "goods.json"
XLSX = Path(r"D:\together\全托管\TEMU\BEL-货盘价格汇总-7.7.xlsx")
SHEET = "货盘价格汇总"
#: 价表列位（1-based，来自实测表头）：货号/供货价/回报率
COL_CODE, COL_PRICE, COL_ROI = 2, 5, 10


def load_prices() -> tuple[dict, str]:
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb[SHEET]
    out: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        code = str(row[COL_CODE - 1] or "").strip()
        if not code:
            continue
        price = row[COL_PRICE - 1]
        roi = row[COL_ROI - 1]
        try:
            price = round(float(price), 2) if price is not None and str(price) != "" else None
        except (TypeError, ValueError):
            price = None
        try:
            roi = round(float(roi), 4) if roi is not None and str(roi) != "" else None
        except (TypeError, ValueError):
            roi = None
        out[code] = {"temuSupplyPrice": price, "temuRoi": roi}
    return out, "%s / %s" % (XLSX.name, SHEET)


def bare_code(code: str, store: str) -> str:
    """`BEL-102104-5SetA` → `102104-5SetA`（价表里不带店前缀）。"""
    c = (code or "").strip()
    pref = store.upper() + "-"
    return c[len(pref):] if c.upper().startswith(pref) else c


def main() -> int:
    ap = argparse.ArgumentParser(description="把 TEMU 供货价/回报率补进 goods.json")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    prices, src = load_prices()
    goods = json.loads(GOODS.read_text(encoding="utf-8"))
    print("价表 %s：%d 个货号" % (src, len(prices)))

    stat = {}
    for store, s in (goods.get("stores") or {}).items():
        hit = miss = 0
        miss_codes = []
        for it in s.get("items") or []:
            key = bare_code(it.get("code") or "", store)
            p = prices.get(key)
            if p:
                it["temuSupplyPrice"] = p["temuSupplyPrice"]
                it["temuRoi"] = p["temuRoi"]
                hit += 1
            else:
                it["temuSupplyPrice"] = None
                it["temuRoi"] = None
                miss += 1
                if len(miss_codes) < 8:
                    miss_codes.append(key)
        stat[store] = {"hit": hit, "miss": miss, "missCodes": miss_codes}
        print("  %s：命中 %d / 未命中 %d %s"
              % (store, hit, miss, ("（漏: %s）" % ", ".join(miss_codes)) if miss_codes else ""))

    goods["pricingSource"] = {
        "file": XLSX.name,
        "sheet": SHEET,
        "columns": {"货号": "B", "TEMU供货价": "E", "TEMU回报率": "J"},
        "note": "两店共用同一份货盘价格表；回报率为小数（0.19 = 19%）",
        "appliedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stat": stat,
    }

    if a.dry_run:
        print("(dry-run：未写文件)")
        return 0
    GOODS.write_text(json.dumps(goods, ensure_ascii=False), encoding="utf-8")
    print("已写回:", GOODS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
