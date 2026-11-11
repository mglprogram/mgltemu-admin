# -*- coding: utf-8 -*-
"""shein_zero_plan.py — SHEIN「可售天数 ≤N → 清零」一条龙：出清单 + 生成导入文件。

口径（与面板/飞书预警同一套）：
    可售天数 = 总库存（恒信可用 + 帕沃尔加州3 + Kansas）÷ 7 天日均，日均 > 0
    ≤N 天的「款号-尺码」→ 清零；SHEIN 没有在途预售，所以不补货、只清。
匹配：
    领星（stock-track.json）的「款号+套系+尺码」 ↔ SHEIN 导出表的「商家SKU」
    （形如 BD-204203-4SetC-XL；供方货号不可靠，不用它）
输出：
    ① --out-plan    给人看的清零清单 xlsx（含「已是0/无需操作」行）
    ② --out-import  直接可上传后台的 import-model 模板（SKU/仓库编码/数量）

用法：
    python scripts\\goods\\shein_zero_plan.py ^
        --export "C:\\Users\\saing\\Downloads\\merchant-stock-export-2026111115285410793210.xlsx" ^
        --template "D:\\together\\全托管\\SHEIN\\BD-库存\\import-model.xlsx" ^
        --out-plan "D:\\together\\全托管\\SHEIN\\BD-库存\\清零清单.xlsx" ^
        --out-import "D:\\together\\全托管\\SHEIN\\BD-库存\\import-model_清零.xlsx"

    # 只看不动
    python scripts\\goods\\shein_zero_plan.py --export x.xlsx --days 3 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import openpyxl  # noqa: E402

STOCK_TRACK = ROOT / "temu-admin" / "public" / "data" / "stock-track.json"
SIZE_TAIL = r"-(XXS|XS|S|M|L|XL|XXL|3XL|4XL|5XL|6XL)$"


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def collect(days: float, stock_track: Path) -> tuple[list[dict], str]:
    """页面的可售天数口径：总库存 = lxTotal（恒信可用 + 帕沃尔两仓），÷ 7 天日均。"""
    data = json.loads(Path(stock_track).read_text(encoding="utf-8"))
    seen: dict[tuple, dict] = {}
    for src_key, src in (data.get("sources") or {}).items():
        for st in src.get("styles") or []:
            for v in st.get("variants") or []:
                for r in v.get("rows") or []:
                    code = str(r.get("code") or "").strip()
                    size = str(r.get("size") or "").strip()
                    if not code or not size:
                        continue
                    daily = _num(r.get("daily"))
                    if daily <= 0:
                        continue
                    total = r.get("lxTotal")
                    if total is None:
                        total = _num(r.get("stock"))     # 领星没匹配到 → 退回实际库存
                    total = _num(total)
                    k = (code, str(r.get("variant") or ""), size)
                    prev = seen.get(k)
                    if prev:
                        prev["total"] = max(prev["total"], total)
                        prev["daily"] = max(prev["daily"], daily)
                    else:
                        seen[k] = dict(code=code, variant=k[1], size=size, total=total,
                                       daily=daily, avail=_num(r.get("lxAvail")))
    items = []
    for it in seen.values():
        it["days"] = round(it["total"] / it["daily"], 2) if it["daily"] > 0 else None
        if it["days"] is not None and it["days"] <= days:
            items.append(it)
    items.sort(key=lambda x: (x["days"], -x["daily"], x["code"], x["size"]))
    return items, str(data.get("generatedAt") or "")


def load_export(path: str) -> list[dict]:
    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(x or "").strip() for x in rows[0]]
    out = []
    for r in rows[1:]:
        if not any(v not in (None, "") for v in r):
            continue
        d = dict(zip(hdr, r))
        seller = str(d.get("商家SKU") or "").strip()
        if not seller:
            continue
        out.append(dict(
            seller=seller,
            body=re.sub(SIZE_TAIL, "", seller),
            sku=str(d.get("SKU") or "").strip(),
            wh=str(d.get("仓库名称") or "").strip(),
            whcode=str(d.get("仓库编码") or "").strip(),
            attr=str(d.get("属性集") or "").strip(),
            avail=_num(d.get("可用库存")),
        ))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SHEIN 可售≤N 天 → 清零清单 + 导入文件")
    ap.add_argument("--export", required=True, help="SHEIN 后台导出的 merchant-stock-export-*.xlsx")
    ap.add_argument("--days", type=float, default=3.0)
    ap.add_argument("--stock-track", default=str(STOCK_TRACK))
    ap.add_argument("--template", default="", help="后台下载的 import-model.xlsx")
    ap.add_argument("--out-plan", default="")
    ap.add_argument("--out-import", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    items, gen = collect(a.days, Path(a.stock_track))
    exp = load_export(a.export)
    by_body: dict[str, list[dict]] = {}
    for e in exp:
        by_body.setdefault(e["body"], []).append(e)

    plan, import_rows, misses = [], [], []
    seen_sku: set[str] = set()
    for it in items:
        # 必须匹配到「尺码」级：同一个款-套系在导出表里每个尺码各占一行
        tail = "-%s-%s" % (it["code"], it["variant"]) if it["variant"] else "-%s" % it["code"]
        want = ("%s-%s" % (tail, it["size"])).lower()          # -204203-4SetC-XL
        rows = [e for e in exp if e["seller"].lower().endswith(want)]
        if not rows:
            misses.append(want[1:])
            continue
        for r in rows:
            plan.append(dict(
                seller=r["seller"], attr=r["attr"], sku=r["sku"], wh=r["wh"],
                days=it["days"], daily=round(it["daily"], 2), total=it["total"],
                sx=r["avail"], act=("清零 %g → 0" % r["avail"]) if r["avail"] > 0 else "已是 0，无需操作",
            ))
            if r["avail"] <= 0:
                continue
            if not r["whcode"] or not r["sku"]:
                raise SystemExit("导出行缺 SKU / 仓库编码：%s" % r["seller"])
            k = (r["sku"], r["whcode"])
            if k in seen_sku:
                continue
            seen_sku.add(k)
            import_rows.append((r["sku"], r["whcode"], 0.0))

    need = [p for p in plan if p["sx"] > 0]
    print("[shein] 数据 %s | 可售 ≤%g 天：%d 个款-尺码" % (gen, a.days, len(items)))
    print("[shein] 命中 SHEIN 导出表 %d 行，其中需清零 %d 行 / %g 件；忽略（表里没有）%d 个"
          % (len(plan), len(need), sum(p["sx"] for p in need), len(misses)))
    for p in need:
        print("   %-26s %-6s %-8s 可售%-6s SHEIN可用%-5g → 0" %
              (p["seller"], p["wh"], p["sku"], p["days"], p["sx"]))
    if misses:
        print("[shein] 忽略：%s" % ", ".join(sorted(set(misses))))

    if a.dry_run:
        print("[shein] --dry-run：不写文件")
        return 0

    if a.out_plan:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "清零清单"
        ws.append(["商家SKU", "属性集", "平台SKU", "仓库", "可售天数", "7天日均", "仓总库存", "SHEIN可用", "动作"])
        for p in sorted(plan, key=lambda x: (x["days"], x["seller"])):
            ws.append([p["seller"], p["attr"], p["sku"], p["wh"], p["days"], p["daily"],
                       p["total"], p["sx"], p["act"]])
        for i, w in enumerate([26, 10, 16, 16, 10, 10, 10, 12, 18], 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
        os.makedirs(os.path.dirname(a.out_plan) or ".", exist_ok=True)
        wb.save(a.out_plan)
        print("[shein] 清单 → %s" % a.out_plan)

    if a.out_import:
        if not a.template:
            raise SystemExit("要给 --out-import 就必须同时给 --template（后台下载的 import-model.xlsx）")
        from shein_fill_stock_template import fill
        if not import_rows:
            print("[shein] 没有需要清零的行，不生成导入文件")
        else:
            fill(a.template, a.out_import, import_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
