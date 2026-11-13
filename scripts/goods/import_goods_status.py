# -*- coding: utf-8 -*-
"""import_goods_status.py — 把卖家中心「商品管理」导出的上架状态存成 pipeline 用的名单。

背景（用户 2026-11-13）：商家库存导出（merchant-stock-export）里**同时有已下架商品**，
pipeline 以前把它们当成在售，结果把已下架的 BW-202504-5SetB-M 清零了 126 件。

数据来源：sellerhub → 商品管理 → 已发布商品 → 「批量导出」（含 商品编码/SKC 与 上架状态）。

    python scripts\\goods\\import_goods_status.py --store BAN --file "C:\\Users\\xx\\Downloads\\商品列表.xlsx"
    python scripts\\goods\\import_goods_status.py --store BAN --file ... --print

输出 data/shein_goods_status.json：
    {"by_store": {"BAN": {"sz251121000035347401": "已下架", "BW-202504-5SetB": "已下架"}}}

写得同时按 SKC 和「商家SKU/供方货号」两个键记，pipeline 命中任一就跳过。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "shein_goods_status.json"

#: 可能的列名（不同版本的导出口径不一样）
SKC_COLS = ("SKC", "商品编码", "SKC编码", "商品SKC")
SELLER_COLS = ("商家SKU", "供方货号", "商家商品编码", "商品货号")
STATUS_COLS = ("上架状态", "商品状态", "状态", "在售状态")


def _pick(hdr: list, cands) -> int:
    for c in cands:
        if c in hdr:
            return hdr.index(c)
    return -1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--file", required=True, help="商品管理导出的 xlsx")
    ap.add_argument("--print", action="store_true", dest="do_print")
    a = ap.parse_args()
    import openpyxl

    wb = openpyxl.load_workbook(a.file, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    hdr = [str(x or "").strip() for x in next(rows)]
    i_skc = _pick(hdr, SKC_COLS)
    i_sel = _pick(hdr, SELLER_COLS)
    i_st = _pick(hdr, STATUS_COLS)
    if i_st < 0 or (i_skc < 0 and i_sel < 0):
        print("表头认不出来，需要 上架状态 + (SKC 或 商家SKU)。实际表头：")
        for i, h in enumerate(hdr):
            print("  ", i, h)
        return 1

    seg = {}
    n_off = n_on = 0
    for r in rows:
        if not r or len(r) <= i_st:
            continue
        st = str(r[i_st] or "").strip()
        if st not in ("已下架", "已上架"):
            continue
        keys = []
        if i_skc >= 0 and r[i_skc]:
            keys.append(str(r[i_skc]).strip())
        if i_sel >= 0 and r[i_sel]:
            keys.append(str(r[i_sel]).strip())
        for k in keys:
            seg[k] = st
        if st == "已下架":
            n_off += 1
        else:
            n_on += 1
    if not seg:
        print("没读到任何 上架/下架 行，检查导出文件是否包含『上架状态』列")
        return 1

    data = {"updatedAt": "", "source": str(a.file), "by_store": {}}
    if OUT.exists():
        try:
            data = json.loads(OUT.read_text(encoding="utf-8"))
            data.setdefault("by_store", {})
        except Exception:  # noqa: BLE001
            pass
    from datetime import datetime
    data["updatedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["source"] = str(a.file)
    data["by_store"][a.store.upper()] = seg
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[goods-status] %s：已上架 %d / 已下架 %d，共 %d 个键 → %s"
          % (a.store.upper(), n_on, n_off, len(seg), OUT))
    if a.do_print:
        for k, v in sorted(seg.items()):
            if v == "已下架":
                print("   已下架", k)
    return 0


if __name__ == "__main__":
    sys.exit(main())
