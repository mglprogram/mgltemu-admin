# -*- coding: utf-8 -*-
"""按「库存跟踪 可售 ≤3 天」清单 → 在售款明细按**不带店铺前缀的款号**匹配 → 到对应店铺改库存/开预售。

匹配口径（用户 2026-11-10）：
  在售款明细（goods.json = /temu/on-sale）里按**款号后缀**搜（`102104-5SetC` 配
  `BEL-102104-5SetC`、`BHY-102104-5SetC`），命中哪个店就去哪个店操作；两家店都命中就都做。
  库存跟踪只给「款号-变体-尺码」和 7 天日均，SPU 一律从在售明细拿。

        python scripts/goods/run_plan_apply.py            # dry-run，逐 SPU 打印将做什么
        python scripts/goods/run_plan_apply.py --apply    # 真执行（清零 + 开预售 + 提交 + 回读）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import presale_candidates as P  # noqa: E402
import stock_presale_pipeline as S  # noqa: E402
import importlib; importlib.reload(S)

ROOT = Path(r"D:\AI-workplace\TEMU")
OUT = ROOT / "_logs" / "stock-presale"
NEAR = 7


def build() -> list:
    """清单：库存跟踪「可售 ≤3 天」→ 去掉店铺前缀，在在售明细里找同款（两家店都查）。"""
    goods = P.load("goods.json")
    by_suffix = {}
    for st, s0 in (goods.get("stores") or {}).items():
        for it in s0.get("items") or []:
            code = it.get("code") or ""
            if "-" not in code:
                continue
            suffix = code.split("-", 1)[1]
            by_suffix.setdefault(suffix, []).append((st, code, str(it.get("spuId") or "")))
    rows = []
    for r in P.build(3, lookback=NEAR):
        d = r.get("days")
        act = "开预售" if d is not None and -NEAR <= d <= NEAR else "忽略"
        code = r["code"]
        suffix = code.split("-", 1)[1] if "-" in code else code
        for st, full, spu in by_suffix.get(suffix) or []:
            rows.append({"store": st, "code": full, "size": r["size"], "spu": spu,
                         "days": d, "eta": r.get("eta"), "transit": r.get("inboundQty"),
                         "temuStock": r.get("temuStock"), "action": act})
    return rows


def group(rows: list) -> dict:
    g = {}
    for r in rows:
        if r["action"] != "开预售" or not r["spu"]:
            continue
        it = g.setdefault((r["store"], r["spu"]), {"store": r["store"], "spu": r["spu"],
                                                   "code": r["code"], "sizes": []})
        it["sizes"].append(r["size"])
    return g


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="", help="只跑这些 SPU（逗号分隔）")
    a = ap.parse_args()
    rows = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ("plan_%s.json" % datetime.now().strftime("%H%M%S"))).write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    g = group(rows)
    print("清单 %d 行 → 待执行 %d 个「店·SPU」" % (len(rows), len(g)))
    for (st, spu), it in sorted(g.items()):
        print(" %s SPU %-12s %-22s 尺码 %s" % (st, spu, it["code"], ",".join(sorted(set(it["sizes"])))))
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    ok_n = bad_n = 0
    for (st, spu), it in sorted(g.items()):
        if only and spu not in only:
            continue
        ns = argparse.Namespace(store=st, spu=spu, code=it["code"], sizes=",".join(sorted(set(it["sizes"]))),
                                zero_sizes="", stock="zero", qty=0, presale_qty_mode="auto",
                                presale_qty=20, apply=a.apply, close=False, report=False, plan_only=False)
        try:
            rc = await S.run(ns)
            if rc == 0:
                ok_n += 1
            else:
                bad_n += 1
            print("   → %s SPU %s：%s" % (st, spu, "OK" if rc == 0 else "失败 rc=%s" % rc))
        except Exception as e:  # noqa: BLE001
            bad_n += 1
            print("!! %s SPU %s 失败：%s: %s" % (st, spu, type(e).__name__, str(e)[:200]))
    print("\n=== 完成：成功 %d / 失败 %d ===" % (ok_n, bad_n))
    return 1 if bad_n else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

