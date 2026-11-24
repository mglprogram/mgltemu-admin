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


def wh_of(x: dict) -> tuple:
    """按这一票头程的归属仓决定预售量填哪个仓（用户 2026-11-24 口径）。

    返回 (归属, 填这个仓, 另一仓填)：
      · account=14450（帕沃尔/乐航）→ 填「加州三仓」，恒信加州3 填 0
      · account=LM739（恒信领星）  → 填「恒信加州3」，加州三仓 填 0
      · 未知                        → 标出来，让人自己判
    """
    a = str(x.get("account") or "").strip().upper()
    if a in ("14450", "PAWOER", "LH"):
        return "帕沃尔", "加州三仓", "0（恒信加州3）"
    if a in ("LM739", "HX", "恒信"):
        return "恒信", "恒信加州3", "0（加州三仓）"
    return "未知", "【需人工判】", "【需人工判】"


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
    # ★ 2026-11-24 用户口径：「根据头程跟踪这一票是帕沃尔的库存还是恒信仓的库存，
    #   哪个的库存，预售在途量填对应的仓库」⇒ 不再固定填某个仓，按 account 分。
    ws.append(["店铺", "货号", "尺码", "线上库存", "可售天数", "7天日均",
               "上架日", "距今天数", "在途归属", "物流账号", "最新节点", "在途量",
               "预售填到哪个仓", "填多少（在途预售库存）", "另一仓填"])
    pre = [x for x in items if x["action"] == "开预售"]
    # ★ 2026-11-19 用户：「一个货号对应的尺码整理在一起」
    #   ⇒ 先按 店铺 → 货号（按最急的可售天数）→ 尺码顺序 排，同货号的尺码连成一片。
    SZ = {"XXS": 0, "XS": 1, "S": 2, "M": 3, "L": 4, "XL": 5, "XXL": 6, "XXXL": 7,
          "3XL": 8, "4XL": 9, "5XL": 10, "15Y": 11, "16Y": 12}
    code_min = {}
    for x in pre:
        k = (x["store"], x["code"])
        code_min[k] = min(code_min.get(k, 9999), float(x.get("d0") or 9999))
    pre.sort(key=lambda x: (x["store"], code_min.get((x["store"], x["code"]), 9999),
                            x["code"], SZ.get(str(x["size"]).upper(), 99), str(x["size"])))
    for x in pre:
        side, wh, other = wh_of(x)
        ws.append([x["store"], x["code"], x["size"], x["temuStock"], x["d0"], x["daily"],
                   x["shelfDate"], x["shelfDays"], side, x["account"],
                   str(x.get("node") or "")[-40:], x["transit"], wh, x["qty"], other])
    z = wb.create_sheet("只清零")
    z.append(["店铺", "货号", "尺码", "线上库存", "可售天数", "7天日均",
              "上架日", "距今天数", "在途归属", "物流账号", "最新节点", "在途量", "改成"])
    for x in items:
        if x["action"] != "只清零":
            continue
        z.append([x["store"], x["code"], x["size"], x["temuStock"], x["d0"], x["daily"],
                  x["shelfDate"], x["shelfDays"], wh_of(x)[0], x["account"],
                  str(x.get("node") or "")[-40:], x["transit"], 0])
    g = wb.create_sheet("按货号归组")
    g.append(["店铺", "货号", "SPU", "开预售尺码", "清零尺码", "要点的次数", "预售填哪个仓"])
    grp: dict = {}
    for x in items:
        k = (x["store"], x["code"])
        e = grp.setdefault(k, {"spu": x.get("spu"), "pre": [], "zero": [], "wh": set()})
        (e["pre"] if x["action"] == "开预售" else e["zero"]).append(x["size"])
        e["wh"].add(wh_of(x)[1])
    for (st, code), e in sorted(grp.items()):
        g.append([st, code, e["spu"], ",".join(sorted(e["pre"])), ",".join(sorted(e["zero"])), 1,
                  " / ".join(sorted(e["wh"]))])

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
    for sheet, widths in ((ws, [8, 26, 7, 10, 9, 9, 12, 9, 9, 9, 40, 8, 16, 14, 18]),
                          (z, [8, 26, 7, 10, 9, 9, 12, 9, 9, 9, 40, 8, 8]),
                          (g, [8, 26, 14, 26, 26, 10, 24])):
        for i, w in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(i)].width = w
    ws.cell(row=1, column=13).fill = PatternFill("solid", fgColor="C00000")
    ws.cell(row=1, column=13).font = Font(color="FFFFFF", bold=True)
    ws.cell(row=1, column=14).fill = PatternFill("solid", fgColor="C00000")
    ws.cell(row=1, column=14).font = Font(color="FFFFFF", bold=True)

    out = a.out or str(ROOT / "out" / "v4清单_开预售与清零.xlsx")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print("开预售 %d 行 / 只清零 %d 行 → %s" % (len(pre), len([x for x in items if x['action'] == '只清零']), out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
