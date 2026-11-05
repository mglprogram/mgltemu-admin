# -*- coding: utf-8 -*-
"""把「TEMU 供货价 / TEMU 回报率」从货盘价格表补进 goods.json。

数据源（用户指定）：
    D:\\together\\全托管\\TEMU\\BEL-货盘价格汇总-7.7.xlsx → sheet「货盘价格汇总」
    列：B=货号、E=TEMU供货价、J=TEMU回报率

匹配口径（用户 2026-11-05 明确）：
    **前缀不需要对应，只要「款号-套数（变体）」对得上就行**。
    所以先剥掉店前缀（BEL-/BHY-）、剥掉 `ERROR-` 之类脏前缀，再用核心串精确匹配；
    核心串仍不中时，按「去掉一切分隔符」的压缩串再比一次。

用法：
    python scripts/goods/add_temu_pricing.py              # 写回 goods.json
    python scripts/goods/add_temu_pricing.py --dry-run    # 只看命中率
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import openpyxl

ROOT = Path(r"D:\AI-workplace\TEMU")
GOODS = ROOT / "temu-admin" / "public" / "data" / "goods.json"
XLSX = Path(r"D:\together\全托管\TEMU\BEL-货盘价格汇总-7.7.xlsx")
SHEET = "货盘价格汇总"
#: 价表列位（1-based，实测表头）：货号/供货价/回报率
COL_CODE, COL_PRICE, COL_ROI = 2, 5, 10

#: 需要剥掉的脏前缀（`ERROR-` 是作废链接留下的，不是真款号）
JUNK_PREFIX = re.compile(r"^(ERROR|TEST|X)-", re.I)
#: 任何 2~5 个字母 + 连字符的前缀（BEL/BHY/HX/…）
ANY_PREFIX = re.compile(r"^[A-Za-z]{2,5}-")


def core_of(code: str, store: str = "") -> str:
    """剥掉不参与匹配的部分：`BEL-102104-5SetA` → `102104-5SetA`。"""
    c = (code or "").strip()
    c = JUNK_PREFIX.sub("", c)
    if store and c.upper().startswith(store.upper() + "-"):
        c = c[len(store) + 1:]
    return ANY_PREFIX.sub("", c)


def squash(s: str) -> str:
    """去掉一切分隔符/空格并大写，用于「少连字符也能对上」的容错。"""
    return re.sub(r"[^0-9A-Za-z.]", "", (s or "")).upper()


def load_prices() -> tuple[dict, dict, str, int]:
    """返回 (核心串精确索引, 压缩串索引, 来源描述, 原始行数)。"""
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb[SHEET]
    exact: dict[str, dict] = {}
    squashed: dict[str, dict] = {}
    n = 0

    def num(v, nd):
        try:
            return round(float(v), nd) if v is not None and str(v) != "" else None
        except (TypeError, ValueError):
            return None

    for row in ws.iter_rows(min_row=2, values_only=True):
        raw = str(row[COL_CODE - 1] or "").strip()
        if not raw:
            continue
        n += 1
        rec = {"temuSupplyPrice": num(row[COL_PRICE - 1], 2),
               "temuRoi": num(row[COL_ROI - 1], 4)}
        key = core_of(raw)
        exact.setdefault(key, rec)
        squashed.setdefault(squash(key), rec)
    return exact, squashed, "%s / %s" % (XLSX.name, SHEET), n


def main() -> int:
    ap = argparse.ArgumentParser(description="把 TEMU 供货价/回报率补进 goods.json")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    exact, squashed, src, rows = load_prices()
    goods = json.loads(GOODS.read_text(encoding="utf-8"))
    print("价表 %s：%d 行 / 核心索引 %d" % (src, rows, len(exact)))

    stat = {}
    for store, s in (goods.get("stores") or {}).items():
        hit = hit_squash = miss = 0
        miss_codes = []
        for it in s.get("items") or []:
            key = core_of(it.get("code") or "", store)
            rec = exact.get(key)
            how = "exact"
            if not rec:
                rec = squashed.get(squash(key))
                how = "squash"
            if rec and rec.get("temuSupplyPrice") is not None:
                it["temuSupplyPrice"] = rec["temuSupplyPrice"]
                it["temuRoi"] = rec["temuRoi"]
                if how == "exact":
                    hit += 1
                else:
                    hit_squash += 1
            else:
                it["temuSupplyPrice"] = None
                it["temuRoi"] = None
                miss += 1
                if len(miss_codes) < 10:
                    miss_codes.append(key)
        stat[store] = {"hit": hit, "hitSquash": hit_squash, "miss": miss,
                       "missCodes": miss_codes}
        print("  %s：精确 %d + 容错 %d = 命中 %d / 未命中 %d %s"
              % (store, hit, hit_squash, hit + hit_squash, miss,
                 ("（漏: %s）" % ", ".join(miss_codes)) if miss_codes else ""))

    goods["pricingSource"] = {
        "file": XLSX.name,
        "sheet": SHEET,
        "columns": {"货号": "B", "TEMU供货价": "E", "TEMU回报率": "J"},
        "matchRule": "剥掉店前缀/脏前缀后按「款号-套数(变体)」匹配；再退一步按去分隔符比对",
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
