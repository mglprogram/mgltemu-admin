# -*- coding: utf-8 -*-
"""按「可售天数 ≤ N」自动补库存（TEMU 在售款）—— 跑在 BEL / BHY 商品列表里。

链路（用户 2026-11-09 定）：
  ① 库存跟踪 stock-track.json 找出 **sellDays ≤ 阈值 且 有日销** 的「款·变体·尺码」
  ② goods.json（在售款明细）由「店铺 + 款号」拿到 **SPU ID**
  ③ 商品列表按 SPU 查询 → 修改库存弹窗 → 该尺码「增加 缺量」→ 确认 → 风险框再确认
     → **重开弹窗按服务端值判定成功**

缺量口径：未来 horizon 天需要 = ceil(日销 × horizon)；TEMU 当前库存已在售 ⇒ 只补差额
（差额 = 需要 − 当前 TEMU 可售；≤0 不补）。可用 --stock-mode add 强制按整量增加。

    python scripts/goods/topup_by_sell_days.py                       # dry-run 看计划
    python scripts/goods/topup_by_sell_days.py --apply               # 真提交
    python scripts/goods/topup_by_sell_days.py --store BEL --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
import bel_stock_set as B  # noqa: E402
import bel_stock_batch as Batch  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

ROOT = Path(r"D:\AI-workplace\TEMU")
TRACK = ROOT / "temu-admin" / "public" / "data" / "stock-track.json"
GOODS = ROOT / "temu-admin" / "public" / "data" / "goods.json"
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}


def load_plan(days: int, horizon: int, stores: list) -> list:
    track = json.loads(TRACK.read_text(encoding="utf-8"))
    goods = json.loads(GOODS.read_text(encoding="utf-8"))
    spu_of = {}
    for st, s in (goods.get("stores") or {}).items():
        for it in s.get("items") or []:
            spu_of[(st, it.get("code"))] = it.get("spuId")
    out, seen = [], set()
    for src in (track.get("sources") or {}).values():
        for style in src.get("styles") or []:
            for var in style.get("variants") or []:
                for r in var.get("rows") or []:
                    sd, daily = r.get("sellDays"), float(r.get("daily") or 0)
                    if sd is None or float(sd) > days or daily <= 0:
                        continue
                    base = "%s-%s" % (style["code"], var["variant"])
                    if (base, r["size"]) in seen:
                        continue
                    seen.add((base, r["size"]))
                    for st in stores:
                        code = "%s-%s" % (st, base)
                        spu = spu_of.get((st, code))
                        if not spu:
                            continue
                        out.append({"store": st, "code": code, "size": r["size"],
                                    "spu": str(spu), "sellDays": sd, "stock": r.get("stock"),
                                    "daily": daily, "need": math.ceil(daily * horizon)})
    return out


async def do_store(store: str, items: list, stock_mode: str, apply: bool) -> dict:
    """一个店一个 SPU 组：一次弹窗里把涉及的尺码全改完，再提交+回读。"""
    spu = items[0]["spu"]
    port = probe_port(WINDOWS[store])
    if not port:
        print("!! 探不到 %s 窗口" % store)
        return {"store": store, "spu": spu, "ok": False, "why": "no-window"}
    tid = await cdp_net.new_tab(port, B.URL, foreground=True)  # noqa: tab-safety 前台必需，跑完关
    tab = B.OwnTab(port, tid)
    res = {"store": store, "spu": spu, "ok": False, "rows": []}
    try:
        if not await tab.open():
            res["why"] = "new-tab-failed"
            return res
        await asyncio.sleep(12)
        # ⚠ 标签页仍在初始化（渲染进程重载）时 Runtime.evaluate 会让 CDP 连接断开：
        #   连接活不过这个窗口期 ⇒ 等它稳定（最多 40s），连不上就重新 attach。
        for attempt in range(4):
            try:
                await zreact.inject(tab.session)
                break
            except Exception as e:  # noqa: BLE001
                print("   注入辅助失败(%d/4): %s" % (attempt + 1, str(e)[:80]))
                await tab.close()
                await asyncio.sleep(3)
                if not await tab.open():
                    res["why"] = "reattach-failed"
                    return res
                await asyncio.sleep(4)
        if not await B.page_ready(tab, 60):
            res["why"] = "list-not-loaded"
            return res
        await B.pick_query_type(tab, "SPU")
        await B.search(tab, spu)
        if not await B.open_stock_dialog(tab, spu):
            res["why"] = "dialog-not-open"
            return res
        d = await B.dialog_rows(tab)
        cur = {r["attr"]: r["cur"] for r in d.get("rows", [])}
        plan, want = {}, {}
        for it in items:
            attr = None
            for a in cur:                       # 属性集形如「黑色-XXL」，按尺码尾匹配
                if a.endswith("-" + it["size"]):
                    attr = a
                    break
            if not attr:
                print("   %s 弹窗里没有尺码 %s，跳过" % (it["code"], it["size"]))
                continue
            c = int(cur[attr] or 0)
            add = it["need"] if stock_mode == "add" else max(0, it["need"] - c)
            if add <= 0:
                print("   %-6s 当前 TEMU 库存 %d 已够 %d 天，不补" % (attr, c, it["need"]))
                continue
            plan[attr] = ("增加", add)
            want[attr] = c + add
            print("   %-6s 当前=%d 日销=%.2f 补=%d → 期望 %d" % (attr, c, it["daily"], add, want[attr]))
        if not plan:
            await B.close_dialog(tab)
            res.update(ok=True, why="nothing-to-add")
            return res
        print("   逐行填写：", await Batch.fill_all_rows(tab, plan))
        d2 = await Batch.rows_of(tab)
        bad = []
        for r in d2.get("rows", []):
            if r["attr"] in want and r["after"] != str(want[r["attr"]]):
                bad.append((r["attr"], r["after"], want[r["attr"]]))
        if bad:
            print("!! 提交前回读不一致，不提交:", bad)
            await B.close_dialog(tab)
            res.update(why="prefill-mismatch:%s" % bad)
            return res
        if not apply:
            print("   dry-run 截图:", await B.shot(tab, "topup_%s_%s_dry.png" % (store, spu)))
            print("   取消:", await B.close_dialog(tab))
            res.update(ok=True, why="dry-run", rows=[dict(attr=a, want=w) for a, w in want.items()])
            return res
        print("   提交:", await B.confirm(tab))
        await asyncio.sleep(3)
        if not await B.dialog_open(tab):                 # ← 用户要求：改完必须重开弹窗校验
            await B.open_stock_dialog(tab, spu)
        d3 = await Batch.rows_of(tab)
        got = {r["attr"]: r["cur"] for r in d3.get("rows", [])}
        ok = all(str(got.get(a)) == str(w) for a, w in want.items())
        for a, w in want.items():
            print("   %-6s 服务端=%s 期望=%s %s" % (a, got.get(a), w, "✅" if str(got.get(a)) == str(w) else "❌"))
        res.update(ok=ok, why="verified" if ok else "verify-failed",
                   rows=[dict(attr=a, want=w, got=got.get(a)) for a, w in want.items()])
        return res
    finally:
        await tab.close()


async def run(plan: list, stock_mode: str, apply: bool) -> int:
    groups = {}
    for p in plan:
        groups.setdefault((p["store"], p["spu"]), []).append(p)
    print("=== 计划：%d 个「店·SPU」组 / %d 个尺码（口径：可售天数≤阈值，补足未来 N 天）==="
          % (len(groups), len(plan)))
    for (store, spu), items in groups.items():
        print(" %s SPU %-12s %-18s %s" % (store, spu, items[0]["code"],
              "、".join("%s(天数%s,日销%.2f→补%d)" % (i["size"], i["sellDays"], i["daily"], i["need"])
                       for i in items)))
        try:
            await do_store(store, items, stock_mode, apply)
        except Exception as e:  # noqa: BLE001
            print("!! %s SPU %s 失败：%s: %s" % (store, spu, type(e).__name__, str(e)[:200]))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BEL,BHY")
    ap.add_argument("--days", type=int, default=3, help="可售天数 ≤ 该值才补")
    ap.add_argument("--horizon", type=int, default=3, help="补足未来多少天：日销 × horizon")
    ap.add_argument("--stock-mode", choices=("diff", "add"), default="diff",
                    help="diff=TEMU 当前库存之外再补差额（默认）；add=直接增加整量")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    plan = load_plan(a.days, a.horizon, [s.strip().upper() for s in a.store.split(",") if s.strip()])
    if not plan:
        print("没有「可售天数 ≤ %d 且有销量」的尺码" % a.days)
        return 0
    return asyncio.run(run(plan, a.stock_mode, a.apply))


if __name__ == "__main__":
    raise SystemExit(main())
