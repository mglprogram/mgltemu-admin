# -*- coding: utf-8 -*-
"""TEMU 库存/预售「清单驱动」pipeline 的两段式流程：

    第一阶段 `plan`  —— 只读数据，把所有款-尺码汇总成一份清单，分三类：
        · 开预售     在途预计上架 ≤7 天（库存跟踪里点「可售≤3天」→ 在途 → 头程「预测/上架」）
        · 只清零     在途预计上架 >7 天（不满足开预售前提）
        · 忽略       上架日已过期 / 没有在途 / 无日期（异常单，不动）
    第二阶段 `apply` —— 按清单逐个 SPU 进「修改库存」弹窗执行：
        开预售的尺码：清零 → 开预售（开关/风险确认/日期/在途量）→ 一次提交
        只清零的尺码：清零 → 一起提交
        最后重开弹窗回读核对。

清单落在 `_logs/stock-presale/清单_<日期>_<时间>.json`，`apply --file` 可以直接执行这份清单。

    python scripts/goods/pipeline_v2_plan.py plan                 # 出清单
    python scripts/goods/pipeline_v2_plan.py apply --apply       # 按最新清单真执行
    python scripts/goods/pipeline_v2_plan.py apply               # dry-run：只打印将做什么
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import presale_candidates as P  # noqa: E402

ROOT = Path(r"D:\AI-workplace\TEMU")
OUT = ROOT / "_logs" / "stock-presale"
NEAR_DAYS = 7


def build_plan() -> list:
    goods = P.load("goods.json")
    spu_of = {}
    for st, s0 in (goods.get("stores") or {}).items():
        for it in s0.get("items") or []:
            spu_of[(st, it.get("code"))] = it.get("spuId")
    rows = []
    for r in P.build(3):                       # stock-track：可售 ≤3 天 且有在途
        days = r.get("days")
        if days is None or days < 0:
            action, why = "忽略", "上架日已过期或无日期（异常单）"
        elif days <= NEAR_DAYS:
            action, why = "开预售", "在途预计上架 %s，%d 天 ≤ %d" % (r.get("eta"), days, NEAR_DAYS)
        else:
            action, why = "只清零", "在途预计上架 %s，%d 天 > %d，不满足开预售" % (r.get("eta"), days, NEAR_DAYS)
        # ⚠ goods.json 只有「在售中」的款；不在售的（没 SPU）只进清单、不执行
        spu = spu_of.get((r["store"], r["code"]))
        if not spu:
            rows.append({"store": r["store"], "code": r["code"], "size": r["size"], "spu": None,
                         "sellDays": None, "eta": r.get("eta"), "days": days,
                         "temuStock": r.get("temuStock"), "transit": r.get("inboundQty"),
                         "action": "忽略", "why": "不在售（在售款明细里没有这个款，无 SPU）"})
            continue
        rows.append({"store": r["store"], "code": r["code"], "size": r["size"],
                     "spu": spu,
                     "sellDays": None, "eta": r.get("eta"), "days": days,
                     "temuStock": r.get("temuStock"), "transit": r.get("inboundQty"),
                     "action": action, "why": why})
    return rows


def print_plan(rows: list) -> None:
    if not rows:
        print("库存跟踪「可售 ≤3 天」里没有带在途的款-尺码")
        return
    print("%-24s %-5s %-12s %-5s %-11s %-6s %-6s %-6s %s"
          % ("货号", "尺码", "SPU", "天数", "预测上架", "TEMU", "在途", "动作", "说明"))
    for r in rows:
        print("%-24s %-5s %-12s %-5s %-11s %-6s %-6s %-6s %s"
              % (r["code"], r["size"], r["spu"] or "-", r["days"], r["eta"] or "-",
                 r["temuStock"], r["transit"], r["action"], r["why"]))
    n1 = sum(1 for r in rows if r["action"] == "开预售")
    n2 = sum(1 for r in rows if r["action"] == "只清零")
    n3 = sum(1 for r in rows if r["action"] == "忽略")
    print("共 %d 条：开预售 %d / 只清零 %d / 忽略 %d" % (len(rows), n1, n2, n3))


def group(rows: list) -> dict:
    g = {}
    for r in rows:
        if r["action"] not in ("开预售", "只清零") or not r["spu"]:
            continue
        key = (r["store"], str(r["spu"]))
        item = g.setdefault(key, {"store": r["store"], "spu": str(r["spu"]), "code": r["code"],
                                  "presale": [], "zero": []})
        (item["presale"] if r["action"] == "开预售" else item["zero"]).append(r["size"])
    return g


def cmd_plan() -> int:
    rows = build_plan()
    print_plan(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / ("清单_%s_%s.json" % (date.today().isoformat(), datetime.now().strftime("%H%M%S")))
    p.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "rows": rows},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    print("清单已存:", p)
    return 0


async def cmd_apply(file: str, apply: bool) -> int:
    import stock_presale_pipeline as S
    if file:
        data = json.loads(Path(file).read_text(encoding="utf-8"))
    else:
        files = sorted(OUT.glob("清单_*.json")) if OUT.exists() else []
        if not files:
            print("没有清单，先跑 plan")
            return 2
        data = json.loads(files[-1].read_text(encoding="utf-8"))
        print("用清单:", files[-1])
    g = group(data.get("rows") or [])
    if not g:
        print("清单里没有要执行的款")
        return 0
    print("=== 将要执行 %d 个 SPU ===" % len(g))
    for (st, spu), it in g.items():
        print(" %s SPU %-12s %-20s 开预售:%s / 只清零:%s"
              % (st, spu, it["code"], ",".join(it["presale"]) or "-", ",".join(it["zero"]) or "-"))
    rc = 0
    for (st, spu), it in g.items():
        ns = argparse.Namespace(store=st, spu=spu, code=it["code"],
                                sizes=",".join(it["presale"]), zero_sizes=",".join(it["zero"]),
                                stock="zero", qty=0, presale_qty_mode="auto", presale_qty=20,
                                apply=apply, close=False, report=False, plan_only=False)
        rc = max(rc, await S.run(ns))
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    a2 = sub.add_parser("apply")
    a2.add_argument("--file", default="")
    a2.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.cmd == "plan":
        return cmd_plan()
    return asyncio.run(cmd_apply(a.file, a.apply))


if __name__ == "__main__":
    raise SystemExit(main())
