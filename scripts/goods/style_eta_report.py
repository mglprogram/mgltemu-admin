# -*- coding: utf-8 -*-
"""统计指定款号在「今天 ~ 12-01」之间预计上架的、按尺码的数量。

用户 2026-11-17：
    「统计乐航和恒信在今天-12.1号-202504-5seta/5setb/5sete 每个尺码的数量
      以及预计上架日期；还有 204203-4seta/4setc 的」

口径
----
· 来源：shipment-track.json（恒信 = LM739 领星入库单；乐航 = 帕沃尔头程那批）。
· 时间窗：predictedShelf 落在 [今天, 2026-12-01] 之间。
· 只统计**还没上架**的（status != 4）：已上架的不需要"预计上架日期"。
· 按「款号 + 尺码」聚合件数，同时列出每票的预计上架日（多票就并列）。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
TRACK = ROOT / "temu-admin" / "public" / "data" / "shipment-track.json"

STYLES = ["202504-5SetA", "202504-5SetB", "202504-5SetE",
          "204203-4SetA", "204203-4SetC"]
UNTIL = date(2026, 12, 1)


def canon(style: str) -> str:
    """把产品名里的款号归一成用户写的写法。

    实测数据里的款号带**品牌前缀**且大小写不一：
        BS2-202504-5SetA / BS-202504-5SetE / BS2-204203-4SetC
    用户要的是 `202504-5SetA` 这种，所以剥掉前缀、统一 Set 后的大小写。
    """
    s = str(style or "").upper()
    k = s.find("202504")
    if k < 0:
        k = s.find("204203")
    if k < 0:
        return s
    return s[k:].replace("SET", "Set")


def main() -> int:
    d = json.loads(TRACK.read_text(encoding="utf-8"))
    today = date.today()
    print(f"数据抓取时间 {d.get('generatedAt')}｜统计窗口 {today} ~ {UNTIL}\n")

    # 款号+尺码 → 明细
    agg: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in d["rows"]:
        if r.get("status") == 4:          # 已上架不算"预计上架"
            continue
        ps = str(r.get("predictedShelf") or "")[:10]
        if not ps:
            continue
        try:
            pdt = date.fromisoformat(ps)
        except ValueError:
            continue
        if not (today <= pdt <= UNTIL):
            continue
        src = "乐航" if r.get("source") == "帕沃尔头程" else "恒信"
        for s in r.get("skus") or []:
            style = canon(s.get("style") or "")
            if style not in STYLES:
                continue
            agg[(style, str(s.get("size") or ""))].append({
                "qty": int(s.get("qty") or 0),
                "pred": ps,
                "src": src,
                "no": r["inboundNo"],
                "ch": r.get("channelText") or "",
            })

    if not agg:
        print("窗口内没有匹配的货件")
        return 0

    for style in STYLES:
        keys = sorted(k for k in agg if k[0] == style)
        if not keys:
            print(f"\n【{style}】窗口内无预计上架")
            continue
        print(f"\n【{style}】")
        print(f"  {'尺码':<5} {'件数':>6}  {'来源':<5} {'预计上架':<11} 单号")
        total_by_src = defaultdict(int)
        for k in keys:
            items = agg[k]
            qty = sum(x["qty"] for x in items)
            for x in items:
                total_by_src[x["src"]] += x["qty"]
            # 同尺码可能来自多票：件数合计，日期/单号并列
            first = items[0]
            srcs = ",".join(sorted({x["src"] for x in items}))
            preds = ",".join(sorted({x["pred"] for x in items}))
            nos = ",".join(sorted({x["no"] for x in items}))
            print(f"  {k[1]:<5} {qty:>6}  {srcs:<5} {preds:<11} {nos}")
        print(f"  小计：{sum(total_by_src.values())} 件"
              f"（乐航 {total_by_src.get('乐航', 0)} / 恒信 {total_by_src.get('恒信', 0)}）")

    print("\n—— 按来源汇总 ——")
    tot = defaultdict(int)
    for items in agg.values():
        for x in items:
            tot[x["src"]] += x["qty"]
    for k, v in tot.items():
        print(f"  {k}: {v} 件")
    print(f"  合计: {sum(tot.values())} 件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
