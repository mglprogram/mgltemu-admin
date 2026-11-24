# -*- coding: utf-8 -*-
"""15 天预售订单台账（用户 2026-11-24 要求）。

需求：
  · 发货总链 `split` 那一步会分「15 天预售」组（BHY / BEL 两店才有）
  · 把这些单攒到一个 Excel 放**桌面**，分 BHY / BEL 两个 sheet
  · **每天累加并去重**（同一参考单号+SKU 只留一条）

数据源：`out/<日期>/fulfill/<店>/split.json` 的 `groups.PRESALE15`
产物：`<桌面>/15天预售订单.xlsx`

用法：
    python presale15_ledger.py                  # 扫最近所有 split.json 并合并
    python presale15_ledger.py --day 2026-11-24 # 只加某天
    python presale15_ledger.py --print          # 只看不写
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(_HERE, "out")
STORES = ("BHY", "BEL")
HEADER = ["首次记录日期", "参考单号", "SKU", "数量", "款号-尺码", "原因"]
COL_W = [14, 26, 30, 8, 24, 60]


def desktop_dir() -> str:
    for p in (os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
              os.path.join(os.environ.get("USERPROFILE", ""), "OneDrive", "Desktop")):
        if os.path.isdir(p):
            return p
    return os.environ.get("USERPROFILE", ".")


def target_path() -> str:
    return os.path.join(desktop_dir(), "15天预售订单.xlsx")


def load_existing(path: str) -> dict:
    """读已有台账 → `{店: {"键": 行}}`；键 = `ord|sku`（去重口径）。"""
    out = {s: {} for s in STORES}
    if not os.path.exists(path):
        return out
    try:
        from openpyxl import load_workbook  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return out
    wb = load_workbook(path)
    for s in STORES:
        if s not in wb.sheetnames:
            continue
        ws = wb[s]
        hdr = [c.value for c in ws[1]]
        idx = {h: i for i, h in enumerate(hdr)}
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[idx.get("参考单号", 1)]:
                continue
            po = str(row[idx["参考单号"]] or "").strip()
            sku = str(row[idx["SKU"]] or "").strip()
            out[s][f"{po}|{sku}"] = {
                "day": row[idx.get("首次记录日期", 0)] or "",
                "ord": po, "sku": sku,
                "qty": row[idx.get("数量", 3)] or 1,
                "key": row[idx.get("款号-尺码", 4)] or "",
                "why": row[idx.get("原因", 5)] or "",
            }
    return out


def collect(day: str = "") -> dict:
    """扫 split.json 收 PRESALE15 → `{店: {键: 行}}`。"""
    got = {s: {} for s in STORES}
    pat = os.path.join(OUT_ROOT, day or "*", "fulfill", "*", "split.json")
    for f in sorted(glob.glob(pat)):
        store = os.path.basename(os.path.dirname(f))
        if store not in STORES:
            continue
        # 路径形如 out/<日期>/fulfill/<店>/split.json ⇒ 上推三级才是日期目录
        d = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(f))))
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for r in ((j.get("groups") or {}).get("PRESALE15") or []):
            po = str(r.get("ord") or "").strip()
            sku = str(r.get("sku") or "").strip()
            if not po:
                continue
            got[store][f"{po}|{sku}"] = {
                "day": d, "ord": po, "sku": sku,
                "qty": r.get("qty") or 1,
                "key": r.get("key") or "",
                "why": (r.get("why") or "")[:120],
            }
    return got


def write(merged: dict, path: str) -> str:
    from openpyxl import Workbook  # noqa: PLC0415
    from openpyxl.styles import Font  # noqa: PLC0415

    wb = Workbook()
    wb.remove(wb.active)
    for s in STORES:
        ws = wb.create_sheet(s)
        ws.append(HEADER)
        for c in ws[1]:
            c.font = Font(bold=True)
        rows = sorted(merged[s].values(), key=lambda r: (str(r["day"]), r["ord"], r["sku"]))
        for r in rows:
            ws.append([r["day"], r["ord"], r["sku"], r["qty"], r["key"], r["why"]])
        for i, w in enumerate(COL_W, 1):
            ws.column_dimensions[chr(64 + i)].width = w
        ws.freeze_panes = "A2"
    wb.save(path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="15 天预售订单台账（每日累加去重）")
    ap.add_argument("--day", default="", help="只加某天（YYYY-MM-DD）；空=扫全部")
    ap.add_argument("--print", action="store_true", help="只打印不写文件")
    a = ap.parse_args()

    path = target_path()
    old = load_existing(path)
    new = collect(a.day)
    merged = {s: dict(old[s]) for s in STORES}
    added = 0
    for s in STORES:
        for k, v in new[s].items():
            if k not in merged[s]:
                merged[s][k] = v
                added += 1
            else:
                # 已有则保留**首次记录日期**（用户要求「每天累加并去重」，日期留最早）
                pass
    print("台账：%s" % path)
    for s in STORES:
        print("  %-4s 累积 %d 条（本次新增 %d）"
              % (s, len(merged[s]), sum(1 for k in new[s] if k not in old[s])))
    print("合计新增 %d 条" % added)

    if a.print:
        for s in STORES:
            for r in list(merged[s].values())[:3]:
                print("   %s %s" % (s, r))
        return 0
    write(merged, path)
    print("已写入 →", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
