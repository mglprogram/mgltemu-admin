# -*- coding: utf-8 -*-
"""build_bel_db.py — 从 TEMU 导出表构建 BEL 店「在售产品信息数据库」。

输入：C:\\Users\\saing\\AppData\\Roaming\\ziniaobrowserdatas\\ziniao browser\\TM-BEL-MGL\\商品基础信息.xlsx
输出：
  data/bel_products.db          SQLite 数据库（products / skus 两张表 + 视图）
  data/bel_products.csv         SKU 级明细（UTF-8-BOM，Excel 可直接打开）
  data/bel_products.xlsx        Excel：SKU明细 / SPU汇总 / 类目汇总 三个 sheet
"""
from __future__ import annotations

import csv
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import openpyxl

SRC = Path(os.environ.get(
    "PRODUCTS_XLSX",
    r"C:\Users\saing\AppData\Roaming\ziniaobrowserdatas\ziniao browser\TM-BEL-MGL\商品基础信息.xlsx"))
ROOT = Path(r"D:\AI-workplace\TEMU")
SLUG = os.environ.get("DB_SLUG", "bel")          # 数据子目录名
OUT_DIR = ROOT / "data" / SLUG
DB_PATH = OUT_DIR / f"{SLUG}_products.db"
CSV_PATH = OUT_DIR / f"{SLUG}_products.csv"
XLSX_PATH = OUT_DIR / f"{SLUG}_products.xlsx"

# 导出表列序（1-based）
COL = {
    1: "title",
    2: "spu_id",
    3: "skc_id",
    4: "sku_id",
    5: "skc_code",
    6: "sku_code",
    7: "category",
    8: "site",
    9: "status",
    10: "spec1",
    11: "spec2",
    12: "site_approve",
    13: "declare_price",
    14: "declare_status",
    15: "stock",
    16: "created_at",
}

SKU_FIELDS = [
    "sku_id", "sku_code", "spec1", "spec2",
    "spu_id", "skc_id", "skc_code", "category",
    "title", "site", "status", "stock",
    "declare_price", "declare_status", "site_approve", "created_at",
]


def norm(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def read_rows():
    wb = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        if raw is None or all(c in (None, "") for c in raw):
            continue
        rec = {}
        for idx, field in COL.items():
            rec[field] = norm(raw[idx - 1]) if idx <= len(raw) else ""
        rec["title"] = str(rec["title"] or "").strip()
        rec["stock"] = int(rec["stock"]) if str(rec["stock"]).strip() not in ("", "None") else 0
        # 规格2 形如 "尺码：S" → 拆出尺码值
        s2 = str(rec["spec2"] or "")
        rec["spec2"] = s2.split("：", 1)[1].strip() if "：" in s2 else s2.strip()
        s1 = str(rec["spec1"] or "")
        rec["spec1"] = s1.split("：", 1)[1].strip() if "：" in s1 else s1.strip()
        rows.append(rec)
    wb.close()
    return rows


def build_sqlite(rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE products (
            spu_id      TEXT PRIMARY KEY,
            title       TEXT,
            category    TEXT,
            site        TEXT,
            status      TEXT,
            skc_count   INTEGER,
            sku_count   INTEGER,
            total_stock INTEGER,
            created_at  TEXT
        )""")
    cur.execute("""
        CREATE TABLE skus (
            sku_id         TEXT PRIMARY KEY,
            sku_code       TEXT,
            spec1          TEXT,
            spec2          TEXT,
            spu_id         TEXT,
            skc_id         TEXT,
            skc_code       TEXT,
            category       TEXT,
            title          TEXT,
            site           TEXT,
            status         TEXT,
            stock          INTEGER,
            declare_price  REAL,
            declare_status TEXT,
            site_approve   TEXT,
            created_at     TEXT
        )""")
    for f in ("spu_id", "skc_id", "category", "status", "sku_code", "skc_code"):
        cur.execute(f"CREATE INDEX idx_skus_{f} ON skus({f})")
    cur.execute("CREATE INDEX idx_skus_spu ON skus(spu_id)")

    cur.executemany(
        f"INSERT OR REPLACE INTO skus ({','.join(SKU_FIELDS)}) "
        f"VALUES ({','.join('?' * len(SKU_FIELDS))})",
        [tuple(r.get(f) for f in SKU_FIELDS) for r in rows],
    )

    # SPU 级汇总
    agg = {}
    for r in rows:
        spu = str(r["spu_id"])
        a = agg.setdefault(spu, {
            "spu_id": spu, "title": r["title"], "category": r["category"],
            "site": r["site"], "status": r["status"], "created_at": r["created_at"],
            "skcs": set(), "skus": set(), "stock": 0,
        })
        a["skcs"].add(str(r["skc_id"]))
        a["skus"].add(str(r["sku_id"]))
        a["stock"] += int(r["stock"] or 0)

    cur.executemany(
        "INSERT OR REPLACE INTO products (spu_id,title,category,site,status,"
        "skc_count,sku_count,total_stock,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [(a["spu_id"], a["title"], a["category"], a["site"], a["status"],
          len(a["skcs"]), len(a["skus"]), a["stock"], a["created_at"])
         for a in agg.values()],
    )

    cur.execute("""CREATE VIEW v_product_sku AS
        SELECT p.spu_id, p.title, p.category, p.site, p.status AS product_status,
               s.sku_id, s.sku_code, s.spec1, s.spec2, s.stock, s.declare_price
        FROM products p JOIN skus s ON s.spu_id = p.spu_id""")
    cur.execute("""CREATE VIEW v_category AS
        SELECT category, COUNT(DISTINCT spu_id) AS spu_count,
               COUNT(DISTINCT skc_id) AS skc_count, COUNT(*) AS sku_count,
               SUM(stock) AS total_stock
        FROM skus GROUP BY category""")
    cur.execute("""CREATE VIEW v_款式 AS
        SELECT skc_code, spu_id, skc_id, category, title,
               COUNT(*) AS sku_count, SUM(stock) AS total_stock
        FROM skus GROUP BY skc_code""")
    con.commit()
    con.close()
    return agg


def build_csv(rows):
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(SKU_FIELDS)
        for r in rows:
            w.writerow([r.get(k) for k in SKU_FIELDS])


def build_xlsx(rows, agg):
    wb = openpyxl.Workbook()

    def sheet(name, header, data):
        ws = wb.create_sheet(name)
        ws.append(header)
        for c in ws[1]:
            c.font = openpyxl.styles.Font(bold=True)
            c.fill = openpyxl.styles.PatternFill("solid", fgColor="DDEBF7")
        for d in data:
            ws.append(list(d))
        for i, col in enumerate(header, 1):
            w = max(len(str(col)), *(len(str(r[i - 1])) for r in data)) if data else len(str(col))
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = min(max(w * 1.6, 10), 60)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        return ws

    wb.remove(wb.active)

    sheet("SKU明细",
          ["SKU ID", "SKU货号", "规格1(颜色)", "规格2(尺码)", "SPU ID", "SKC ID",
           "SKC货号(款号)", "叶子类目", "商品标题", "经营站点", "商品状态", "库存",
           "申报价格USD", "申报状态", "站点申报", "创建时间"],
          [[r["sku_id"], r["sku_code"], r["spec1"], r["spec2"], r["spu_id"], r["skc_id"],
            r["skc_code"], r["category"], r["title"], r["site"], r["status"], r["stock"],
            r["declare_price"], r["declare_status"], r["site_approve"], r["created_at"]]
           for r in rows])

    sheet("SPU汇总",
          ["SPU ID", "商品标题", "叶子类目", "经营站点", "商品状态", "SKC数", "SKU数",
           "总库存", "创建时间"],
          [[a["spu_id"], a["title"], a["category"], a["site"], a["status"],
            len(a["skcs"]), len(a["skus"]), a["stock"], a["created_at"]]
           for a in agg.values()])

    style = {}
    for r in rows:
        c = style.setdefault(r["category"], {"spu": set(), "skc": set(), "sku": 0, "stock": 0})
        c["spu"].add(r["spu_id"]); c["skc"].add(r["skc_id"]); c["sku"] += 1; c["stock"] += r["stock"]

    sheet("类目汇总",
          ["叶子类目", "SPU数", "SKC数", "SKU数", "总库存"],
          [[k, len(v["spu"]), len(v["skc"]), v["sku"], v["stock"]]
           for k, v in sorted(style.items(), key=lambda x: -x[1]["sku"])])

    wb.save(XLSX_PATH)


def main():
    rows = read_rows()
    agg = build_sqlite(rows)
    build_csv(rows)
    build_xlsx(rows, agg)
    print(f"SKU 行数        : {len(rows)}")
    print(f"SPU(SKU) 数     : {len(agg)}")
    print(f"SKC 数          : {len({r['skc_id'] for r in rows})}")
    print(f"款号(SKC货号) 数: {len({r['skc_code'] for r in rows})}")
    print(f"类目数          : {len({r['category'] for r in rows})}")
    print(f"总库存          : {sum(r['stock'] for r in rows)}")
    print(f"导出时刻        : {datetime.now():%Y-%m-%d %H:%M:%S}")
    print()
    print("DB  :", DB_PATH)
    print("CSV :", CSV_PATH)
    print("XLSX:", XLSX_PATH)


if __name__ == "__main__":
    main()