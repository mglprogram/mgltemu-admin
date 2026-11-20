"""催货表两条长期规则的落地脚本（用户 2026-11-20 定，之后每次同步都要遵守）：

① 名称清洗
   · 去掉最前面的店铺代号段（BS2 / BH / BLA / BE2 …，不含 3 位以上数字的纯代号段）
   · `-BH-H103903-2B-XXL` 这类**开头就带连字符**的，连字符和店铺段一起去掉
② 款号别名
   · `203804R` 一律写成 `203804`（C 列与 B 列同步替换），用于两个库存 sheet

两个 sheet：
   恒信库存表  worksheet_id=13  ← 领星 LM739（availableAmount / transportAmount）
   帕沃尔库存  worksheet_id=19  ← 帕沃尔 14450（博盈通豪2）加州3 + Kansas

用法：
    python scripts/goods/sync_chase_stock.py --dry      # 只出 CSV + 打印统计
    python scripts/goods/sync_chase_stock.py --apply    # 写进金山催货表
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "temu-admin" / "public" / "data"
OUT = ROOT / "out" / "kd_update"
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

#: 金山「TTSA催货跟进表」https://www.kdocs.cn/l/clmBVSbSdpaL
FILE_ID = "s3mUJgxDi1MgR2TyoRbp1xHTrmpEsfwmW"
SHEET_HX = 13
SHEET_PW = 19
PW_WHS = ("加州3仓", "kansas仓")

#: 款号别名 —— 用户 2026-11-20：203804R 统一写成 203804，
#: ⚠ 只作用于 **C 列（Name/产品名称）**；B 列（Product Name/产品名称）保持原始名不动。
CODE_ALIAS = {"203804R": "203804"}

HEAD = ("Barcode/产品条码,Product Name/产品名称,Name/产品名称,"
        "Available Inventory/可用库存,In-transit inventory/在途库存")


def strip_shop_prefix(name: str) -> str:
    """去店铺前缀（规则①）。"""
    s = str(name or "").strip().lstrip("-")
    if "-" not in s:
        return s
    head = s.split("-", 1)[0]
    if not re.fullmatch(r"[A-Za-z]{1,4}\d{0,2}", head):
        return s
    if len("".join(ch for ch in head if ch.isdigit())) >= 3:
        return s
    return s.split("-", 1)[1].lstrip("-")


def fix_code(name: str) -> str:
    """款号别名（规则②）。"""
    s = str(name or "")
    for a, b in CODE_ALIAS.items():
        s = s.replace(a, b)
    return s


def linxing_rows() -> list[tuple]:
    """领星 LM739 全量 → [(barcode, productName, name, avail, transit)]，按产品名聚合。"""
    sys.path.insert(0, str(ROOT / "scripts" / "oms"))
    from lib.oms_client import OmsClient  # noqa: PLC0415

    creds = json.loads((ROOT / "scripts" / "oms" / "oms_accounts.local.json").read_text(encoding="utf-8"))
    acc = {a["tag"]: a for a in (creds.get("accounts") or [])}
    a = acc["LM739"]
    c = OmsClient(a["appKey"], a["appSecret"], timeout=60, verbose=False)
    agg: dict[str, dict] = {}
    page = 1
    while True:
        d = c.post("/openapi/v1/integratedInventory/pageOpen",
                   {"page": page, "pageSize": 200}).get("data") or {}
        recs = d.get("records") or []
        for r in recs:
            raw = str(r.get("productName") or "").strip()
            if not raw:
                continue
            # ⚠ 2026-11-20 用户定：**B 列保持原样**（203804R 不替换），
            #   只有 C 列（去前缀后）走别名 203804R → 203804。
            #   所以这里按「原始名」聚合，别名只在返回时对第 3 列生效。
            name = raw
            dtl = r.get("productStockDtl") or {}
            e = agg.setdefault(name, {"barcode": str(r.get("sku") or ""),
                                      "avail": 0.0, "transit": 0.0})
            e["avail"] += float(dtl.get("availableAmount") or 0)
            e["transit"] += float(dtl.get("transportAmount") or 0)
        if len(recs) < 200:
            break
        page += 1
    return [(v["barcode"], k, fix_code(strip_shop_prefix(k)), round(v["avail"]), round(v["transit"]))
            for k, v in agg.items()]


def pw_rows() -> list[tuple]:
    """帕沃尔（豪2）加州3+Kansas → 同结构；按「去前缀后的名字」合并同名多行。"""
    d = json.loads((PUBLIC / "pw-stock.json").read_text(encoding="utf-8"))
    agg: dict[str, dict] = defaultdict(lambda: {"barcode": "", "pname": "", "avail": 0.0, "transit": 0.0})
    for it in d.get("items") or []:
        if str(it.get("wh") or "").strip() not in PW_WHS:
            continue
        raw = str(it.get("name") or it.get("sku") or "").strip()
        if not raw:
            continue
        # 合并键仍用 C 列口径（去前缀 + 别名），同名多行才不会被拆开；
        # 但 **B 列（e["pname"]）保留原始名**，不做别名替换。
        key = fix_code(strip_shop_prefix(raw))
        e = agg[key]
        e["barcode"] = e["barcode"] or str(it.get("fnsku") or "")
        e["pname"] = e["pname"] or raw
        e["avail"] += float(it.get("stock") or 0)
        e["transit"] += float(it.get("transit") or 0)
    return [(v["barcode"], v["pname"], k, round(v["avail"]), round(v["transit"]))
            for k, v in agg.items()]


def write_csv(tag: str, rows: list[tuple]) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: r[2])
    p = OUT / ("%s.csv" % tag)
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        f.write(HEAD + "\n")
        for r in rows:
            f.write("%s,%s,%s,%s,%s\n" % r)
    print("[%s] %d 行 → %s" % (tag, len(rows), p))
    return p


def kd(*args):
    p = subprocess.run(["kdocs-cli", *args, "--compact"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    txt = (p.stdout or "") + (p.stderr or "")
    line = next((ln for ln in txt.splitlines() if ln.strip().startswith('{"code"')), "")
    return json.loads(line) if line else {"code": -1, "_raw": txt[:300]}


def read_cells(wid: int, nrow: int, ncol: int = 5):
    rng = json.dumps({"rowFrom": 0, "rowTo": nrow, "colFrom": 0, "colTo": ncol - 1},
                     separators=(",", ":"))
    d = kd("sheet", "get-range-data", f"file_id={FILE_ID}", f"worksheet_id={wid}", "range=" + rng)
    return ((d.get("data") or {}).get("detail") or {}).get("rangeData") or []


def batch(wid: int, payload_rows: list[dict]):
    range_data = []
    for r in payload_rows:
        for col, key in enumerate(("barcode", "pname", "name", "avail", "transit")):
            v = r.get(key)
            if v is None:
                continue
            range_data.append({
                "op_type": "cell_operation_type_formula",
                "row_from": r["row"], "row_to": r["row"],
                "col_from": col, "col_to": col,
                "formula": "" if v == "" else str(v),
            })
    body = {"file_id": FILE_ID, "worksheet_id": wid, "range_data": range_data}
    p = OUT / ("payload_%s.json" % wid)
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return kd("sheet", "range-data-batch-update", "--file", str(p))


def push(wid: int, csv_rows: list[list[str]], chunk: int = 400) -> None:
    cells = read_cells(wid, 3200)
    data_cells = [c for c in cells if (c.get("originRow") or 0) > 0 and (c.get("cellText") or "").strip()]
    old_max = max([c.get("originRow") for c in data_cells], default=0)
    print("  sheet=%s 现有数据到第 %d 行 → 写 %d 行" % (wid, old_max, len(csv_rows)))

    tail = [{"row": r, "barcode": "", "pname": "", "name": "", "avail": "", "transit": ""}
            for r in range(len(csv_rows) + 1, old_max + 1)]
    for i in range(0, len(tail), chunk):
        d = batch(wid, tail[i:i + chunk])
        print("    清尾 %d 行：code=%s" % (len(tail[i:i + chunk]), d.get("code")))

    payload = [{"row": i + 1,
                "barcode": r[0] if len(r) > 0 else "",
                "pname": r[1] if len(r) > 1 else "",
                "name": r[2] if len(r) > 2 else "",
                "avail": r[3] if len(r) > 3 else "",
                "transit": r[4] if len(r) > 4 else ""}
               for i, r in enumerate(csv_rows)]
    for i in range(0, len(payload), chunk):
        d = batch(wid, payload[i:i + chunk])
        print("    写入 %d-%d 行：code=%s %s"
              % (i + 2, i + 1 + len(payload[i:i + chunk]), d.get("code"), d.get("msg") or ""))

    after = read_cells(wid, len(csv_rows) + 3)
    got = {(c.get("originRow"), c.get("originCol")): (c.get("cellText") or "") for c in after}
    print("    回读 r2: %s" % [got.get((2, c), "")[:24] for c in range(5)])
    print("    回读 r%d: %s" % (len(csv_rows) + 1,
                               [got.get((len(csv_rows) + 1, c), "")[:24] for c in range(5)]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="催货表：恒信(领星) + 帕沃尔(豪2) 库存同步")
    ap.add_argument("--apply", action="store_true", help="真写金山表")
    ap.add_argument("--dry", action="store_true", help="只出 CSV")
    ap.add_argument("--sheet", choices=["hx", "pw", "both"], default="both")
    args = ap.parse_args(argv)

    print("[chase] %s 开始（%s）" % (datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    "真写" if args.apply else "只出 CSV"))
    targets = []
    if args.sheet in ("hx", "both"):
        targets.append(("hx", SHEET_HX, linxing_rows))
    if args.sheet in ("pw", "both"):
        targets.append(("pw", SHEET_PW, pw_rows))

    for tag, wid, fn in targets:
        rows = fn()
        write_csv(tag, rows)
        if args.apply:
            csv_rows = list(csv.reader((OUT / ("%s.csv" % tag)).open(encoding="utf-8-sig")))[1:]
            push(wid, csv_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
