# -*- coding: utf-8 -*-
"""把 v4 清单（plan_v4.py 的 json）导出成人工照着点的 Excel。

用户 2026-11-19：「你给我清单即可，我跑开预售和清零的操作」⇒ 清单要**能直接照着点**：
    ① 开预售  sheet：每店一段，一行一个「货号-尺码」，带上：线上库存 / 可售天数 / 上架日 / 物流节点 / 在途量 / **填多少** / 填哪个仓
    ② 只清零  sheet：一行一个，带线上库存（清零就是把它改 0）
    ③ 按 SPU/货号 归组，方便同一个弹窗一次点完（用户的操作习惯：按 SPU 查 → 按货号进弹窗 → 多尺码一次提交）
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="plan_v4.py --json 的输出")
    ap.add_argument("--out", default="", help="输出 xlsx")
    a = ap.parse_args()
    plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    items = plan.get("items") or []
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "开预售"
    ws.append(["店铺", "货号", "尺码", "线上库存", "可售天数", "7天日均",
               "上架日", "距今天数", "物流账号", "最新节点", "在途量",
               "填恒信加州3（在途预售库存）", "加州三仓/kansas 填"])
    pre = [x for x in items if x["action"] == "开预售"]
    for x in pre:
        ws.append([x["store"], x["code"], x["size"], x["temuStock"], x["d0"], x["daily"],
                   x["shelfDate"], x["shelfDays"], x["account"],
                   str(x.get("node") or "")[-40:], x["transit"], x["qty"], 0])
    z = wb.create_sheet("只清零")
    z.append(["店铺", "货号", "尺码", "线上库存", "可售天数", "7天日均",
              "上架日", "距今天数", "物流账号", "最新节点", "在途量", "改成"])
    for x in items:
        if x["action"] != "只清零":
            continue
        z.append([x["store"], x["code"], x["size"], x["temuStock"], x["d0"], x["daily"],
                  x["shelfDate"], x["shelfDays"], x["account"],
                  str(x.get("node") or "")[-40:], x["transit"], 0])
    g = wb.create_sheet("按货号归组")
    g.append(["店铺", "货号", "SPU", "开预售尺码", "清零尺码", "要点的次数"])
    grp: dict = {}
    for x in items:
        k = (x["store"], x["code"])
        e = grp.setdefault(k, {"spu": x.get("spu"), "pre": [], "zero": []})
        (e["pre"] if x["action"] == "开预售" else e["zero"]).append(x["size"])
    for (st, code), e in sorted(grp.items()):
        g.append([st, code, e["spu"], ",".join(sorted(e["pre"])), ",".join(sorted(e["zero"])), 1])

    # ★ 2026-11-19 用户红线：已开 15 天预售的款-尺码「不允许去动，只能加」
    lk = wb.create_sheet("不动_已开15天预售")
    lk.append(["店铺", "货号", "尺码", "线上库存", "可售天数", "7天日均", "在途量", "说明"])
    for x in items:
        if not x["action"].startswith("不动"):
            continue
        lk.append([x["store"], x["code"], x["size"], x["temuStock"], x["d0"], x["daily"],
                   x["transit"], "已开 15 天预售：不许清零、不许关/改预售；只能加库存"])

    hdr = PatternFill("solid", fgColor="1F4E79")
    for sheet in (ws, z, g):
        for c in sheet[1]:
            c.fill = hdr
            c.font = Font(color="FFFFFF", bold=True)
            c.alignment = Alignment(horizontal="center")
        sheet.freeze_panes = "A2"
    for sheet, widths in ((ws, [8, 26, 7, 10, 9, 9, 12, 9, 9, 40, 8, 20, 10]),
                          (z, [8, 26, 7, 10, 9, 9, 12, 9, 9, 40, 8, 8]),
                          (g, [8, 26, 14, 26, 26, 10])):
        for i, w in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(i)].width = w
    ws.cell(row=1, column=12).fill = PatternFill("solid", fgColor="C00000")
    ws.cell(row=1, column=12).font = Font(color="FFFFFF", bold=True)

    out = a.out or str(ROOT / "out" / "v4清单_开预售与清零.xlsx")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print("开预售 %d 行 / 只清零 %d 行 → %s" % (len(pre), len([x for x in items if x['action'] == '只清零']), out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
