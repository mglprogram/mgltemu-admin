# -*- coding: utf-8 -*-
"""build_export_api.py — 用 OpenAPI 数据生成「商家库存导出」等价的 xlsx。

为什么不需要人工导出（2026-11-26 用户问「能不能每周一去 sellerhub 导出」）
------------------------------------------------------------------------------
`TEMU-SHEIN-OnSale-Daily` 每天 07:30 已经用 OpenAPI
（product/query + spu-info + query-sku-sales + stock-query）把四个店
**已上架**的 SPU/SKC/每个尺码抓下来 → `temu-admin/public/data/shein-goods.json`，
里面每个尺码都有：

    商家SKU（supplierSku） / 平台SKU（skuCode） / 尺码 / 线上可用（stock） / 总库存（stockTotal）

这正是库存 pipeline 需要的三样东西（`商家SKU` / `SKU` / 属性集=尺码）。
所以「新上架的 SKC」第二天 07:30 就会自己出现在这里，**不用每周去网页导一次**。

产出
----
    data/shein-export/merchant-stock-export-<STORE>-api.xlsx   （列名与官方导出一致）
    data/shein-export/_summary.json                            （每店行数 + goods 的 generatedAt）

用法
----
    python scripts\\goods\\build_export_api.py            # 四店都生成
    python scripts\\goods\\build_export_api.py --store BS
    python scripts\\goods\\build_export_api.py --max-age-hours 26   # 超过就拒绝（回落到官方导出）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOODS = ROOT / "temu-admin" / "public" / "data" / "shein-goods.json"
OUT_DIR = ROOT / "data" / "shein-export"

COLS = ["SKC", "供方货号", "商品名称", "商家SKU", "SKU", "属性集",
        "仓库名称", "仓库编码", "下单锁定数", "下单支付数", "可用库存", "总库存", "预警库存"]


def load_goods() -> dict:
    if not GOODS.exists():
        raise SystemExit("[export-api] 缺 %s（先跑 TEMU-SHEIN-OnSale-Daily）" % GOODS)
    return json.loads(GOODS.read_text(encoding="utf-8"))


def age_hours(generated_at: str) -> float:
    try:
        dt = datetime.strptime(str(generated_at)[:19].replace("/", "-"), "%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return 1e9
    return (datetime.now() - dt).total_seconds() / 3600.0


def rows_for(store: str, goods: dict) -> list[dict]:
    seg = (goods.get("stores") or {}).get(store) or {}
    out = []
    for it in seg.get("items") or []:
        skcs = it.get("skc") or []
        skc = skcs[0] if skcs else ""
        for s in it.get("sizes") or []:
            seller = str(s.get("supplierSku") or s.get("skuName") or "").strip()
            code = str(s.get("skuCode") or "").strip()
            if not seller or not code:
                continue
            out.append({
                "SKC": skc,
                "供方货号": it.get("code") or "",
                "商品名称": it.get("title") or "",
                "商家SKU": seller,
                "SKU": code,
                "属性集": s.get("size") or "",
                "仓库名称": "",
                "仓库编码": "",
                "下单锁定数": 0,
                "下单支付数": 0,
                "可用库存": s.get("stock") or 0,
                "总库存": s.get("stockTotal") or 0,
                "预警库存": 0,
            })
    return out


def write_xlsx(path: Path, rows: list[dict]) -> Path:
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "库存"
    ws.append(COLS)
    for r in rows:
        ws.append([r[c] for c in COLS])
    for c in ws[1]:
        c.font = Font(bold=True)
    for w, col in zip((22, 24, 40, 24, 18, 10, 14, 16, 10, 10, 10, 10, 10),
                      ws.iter_cols(min_row=1, max_row=1)):
        ws.column_dimensions[col[0].column_letter].width = w
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OpenAPI → 商家库存导出等价 xlsx")
    ap.add_argument("--store", default="", help="只生成某个店（默认全部）")
    ap.add_argument("--max-age-hours", type=float, default=0,
                    help="goods.json 超龄就拒绝生成（0=不检查）")
    a = ap.parse_args(argv)

    goods = load_goods()
    gen = goods.get("generatedAt") or ""
    age = age_hours(gen)
    print("[export-api] shein-goods.json 生成于 %s（%.1fh 前）" % (gen, age), flush=True)
    if a.max_age_hours and age > a.max_age_hours:
        print("[export-api] ✗ 数据已 %.1f 小时未更新（>%g）→ 不生成，调用方应回落到官方导出"
              % (age, a.max_age_hours))
        return 3

    stores = [a.store.upper()] if a.store else sorted((goods.get("stores") or {}).keys())
    summary = {"generatedAt": gen, "ageHours": round(age, 2), "stores": {}}
    for st in stores:
        rows = rows_for(st, goods)
        if not rows:
            print("[export-api] %s 没有已上架 SKU，跳过" % st)
            continue
        p = write_xlsx(OUT_DIR / ("merchant-stock-export-%s-api.xlsx" % st), rows)
        summary["stores"][st] = {"rows": len(rows), "file": str(p)}
        print("[export-api] %s → %s（%d 行）" % (st, p.name, len(rows)))
    (OUT_DIR / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
