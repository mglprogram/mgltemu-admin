# -*- coding: utf-8 -*-
"""生成「待确认清零」清单（两仓都没货 + 头程开不了预售），推 TEMU 库存预警群 + 落 Excel。

用户 2026-11-24：「两仓都没货且头程也没法开预售，需清零的让我确认，给出清单，
并推送到 TEMU 库存预警群」。

判定（全部满足才进清单）：
    ① 恒信仓库存 = 0 且 帕沃尔加州3仓库存 = 0（两仓都没货）
    ② 当前**没有开预售**（已开的不在清零范围）
    ③ 头程开不了预售：无可用在途 / 上架日 > 6 天 / 物流节点不合格（plan_v4 判的「只清零」）
    ④ 该尺码在售（在售中清单里）

用法：
    python scripts/goods/zero_confirm_list.py --store BEL            # 只看
    python scripts/goods/zero_confirm_list.py --store BEL --push     # 同时推群 + 出 xlsx
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))                    # notify_push 在这层
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import notify_push  # noqa: E402

PLAN = ROOT / "data" / "presale-v4-plan.json"
LIVE = ROOT / "data" / "presale-live.json"
OUT = ROOT / "out"


def build(store: str) -> list:
    plan = json.loads(PLAN.read_text(encoding="utf-8")).get("items") or []
    live = json.loads(LIVE.read_text(encoding="utf-8"))
    lrows = {}
    for st, blk in (live.get("stores") or {}).items():
        for r in blk.get("rows") or []:
            lrows[(st, r["code"], r["size"])] = r
    seen = set()
    out = []
    for i in plan:
        if i["store"] != store:
            continue
        key = (i["code"], i["size"])
        if key in seen:
            continue
        seen.add(key)
        lr = lrows.get((store, i["code"], i["size"]))
        if not lr:                      # 不在在售中 → 不处理
            continue
        sb = lr.get("stockByWh") or {}
        hx, ca3 = int(sb.get("hx") or 0), int(sb.get("ca3") or 0)
        if hx != 0 or ca3 != 0:         # 还有货 → 不是「两仓都没货」
            continue
        if lr.get("presaleOn"):         # 已开预售 → 不清零
            continue
        if i["action"] != "只清零":      # 头程能开预售的走开预售流程
            continue
        out.append({
            "store": store, "code": i["code"], "size": i["size"], "spu": str(i.get("spu") or ""),
            "hx": hx, "ca3": ca3, "transit": i.get("transit"),
            "shelf": i.get("shelfDate") or "无",
            "days": i.get("shelfDays"),
            "node": str(i.get("node") or "无在途"),
            "d0": i.get("d0"),
        })
    SZ = {"S": 0, "M": 1, "L": 2, "XL": 3, "XXL": 4}
    out.sort(key=lambda r: (r["code"], SZ.get(r["size"], 9)))
    return out


def fmt_msg(store: str, rows: list) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["TEMU %s｜两仓无货且头程开不了预售 → 待确认清零" % store,
             "生成时间 %s，共 %d 个尺码" % (ts, len(rows)),
             "口径：恒信仓=0 且 加州3仓=0，当前未开预售，无可用在途/上架超6天/节点不合格",
             ""]
    for r in rows:
        shelf = r["shelf"] if r["shelf"] != "无" else "无上架日"
        days = ("%s天" % r["days"]) if r["days"] is not None else "—"
        lines.append("· %s %s  在途%s  上架%s(%s)" %
                     (r["code"], r["size"], r["transit"], shelf, days))
        lines.append("   节点：%s" % r["node"][-50:])
    lines += ["", "请确认是否清零（回复「清零 货号 尺码」或「全部清零」）"]
    return "\n".join(lines)


def to_xlsx(store: str, rows: list) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = "待确认清零"
    ws.append(["店铺", "货号", "尺码", "SPU", "恒信仓", "加州3仓", "在途量",
               "上架日", "距今天数", "可售天数", "最新物流节点"])
    for r in rows:
        ws.append([r["store"], r["code"], r["size"], r["spu"], r["hx"], r["ca3"],
                   r["transit"], r["shelf"], r["days"], r["d0"], r["node"]])
    for c in ws[1]:
        c.fill = PatternFill("solid", fgColor="C00000")
        c.font = Font(color="FFFFFF", bold=True)
        c.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    for i, w in enumerate([8, 24, 7, 14, 9, 10, 9, 12, 10, 10, 46], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / ("待确认清零_%s_%s.xlsx" % (store, datetime.now().strftime("%Y%m%d")))
    wb.save(p)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--push", action="store_true", help="推 TEMU 库存预警群 + 出 xlsx")
    a = ap.parse_args()
    rows = build(a.store)
    if not rows:
        print("%s：没有「两仓无货且头程开不了预售」的尺码" % a.store)
        return 0
    print("%s：待确认清零 %d 个尺码" % (a.store, len(rows)))
    for r in rows:
        print("  %-24s %-4s 在途%-6s 上架%s(%s天) 节点:%s"
              % (r["code"], r["size"], r["transit"], r["shelf"], r["days"],
                 r["node"][-34:]))
    if not a.push:
        return 0
    p = to_xlsx(a.store, rows)
    print("清单 → %s" % p)
    msg = fmt_msg(a.store, rows)
    ok, why = notify_push.push_stock_alert("", msg)
    print("推送库存预警群：%s %s" % ("成功" if ok else "失败", why))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
