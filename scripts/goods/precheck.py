# -*- coding: utf-8 -*-
"""预检 + 回归：逐款确认「修改库存」弹窗可开，并给出成功率（供自动写入当门槛）。

为什么要它（用户 2026-10-13 要求）：
  ① 开不了弹窗的款必须**显式标 SKIPPED 并汇总**，不允许静默漏加；
  ② 自动写入前必须先跑这个，**成功率必须 100%** 才允许 `--apply`。

用法：
    python scripts/goods/precheck.py BHY            # 预检该店（写 JSON 摘要）
    python scripts/goods/precheck.py                # 两店都检
    python scripts/goods/precheck.py BHY --limit 3  # 只检前 3 款（快速验证）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cdp_net  # noqa: E402
import run_stock_topup as R  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

OUT = HERE.parents[1] / "out" / "stock-topup"
URL = R.URL_GOODS


async def precheck_store(store: str, limit: int | None = None) -> dict:
    """逐款开一次弹窗（不填数），返回 {ok:[], skipped:[{code,why}], rate}。"""
    cfg = R.load_cfg()
    codes = R.codes_of(store)
    if limit:
        codes = codes[:limit]
    port = probe_port(R.WINDOWS[store])
    res = {"store": store, "total": len(codes), "ok": [], "skipped": []}
    if not port:
        res["skipped"] = [{"code": "$ALL", "why": "探测不到窗口 CDP 端口"}]
        res["rate"] = 0.0
        return res

    # ⚠ **每款开一个干净的标签页**：同一个页里连续处理多款会状态漂移
    #   （第 2~4 款起筛选就失败）——2026-10-13 实测。用完即关（用户明确要求）。
    for i, code in enumerate(codes, 1):
        plan = R.build_plan(store, code, cfg["加库存"])
        if not plan:
            res["skipped"].append({"code": code, "why": "无可加尺码"})
            print("[%d/%d] %-26s SKIPPED 无可加尺码" % (i, len(codes), code), flush=True)
            continue
        sizes = [p["size"] for p in plan]
        tid = await cdp_net.new_tab(port, URL, foreground=True)
        tgt = None
        for _ in range(30):
            await asyncio.sleep(1)
            tgt = next((x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                        if (x.get("id") or x.get("targetId")) == tid), None)
            if tgt:
                break
        if not tgt:
            res["skipped"].append({"code": code, "why": "开标签页失败"})
            continue
        try:
            async with cdp_net.NetCapture(tgt["webSocketDebuggerUrl"]) as cap:
                for _ in range(20):
                    await asyncio.sleep(3)
                    n = await zreact.call_js(cap, "return String("
                                                "document.querySelectorAll('tbody tr').length);",
                                             timeout=60)
                    if n != "0":
                        break
                await zreact.inject(cap)
                if not await R.search_style(cap, store, code, sizes):
                    res["skipped"].append({"code": code, "why": "精准筛选失败"})
                    print("[%d/%d] %-26s SKIPPED 筛选失败" % (i, len(codes), code),
                          flush=True)
                    continue
                got = await R.open_dialog(cap, code)
                info = await R.ev_json(cap, R.DIALOG_INFO)
                ok = await R.dialog_open(cap) and info.get("sku", "").upper() == code.upper()
                if ok:
                    res["ok"].append(code)
                    print("[%d/%d] %-26s OK (%s)" % (i, len(codes), code, got), flush=True)
                else:
                    res["skipped"].append({"code": code, "why": "弹窗打不开 (%s)" % got})
                    print("[%d/%d] %-26s SKIPPED 弹窗打不开 (%s)"
                          % (i, len(codes), code, got), flush=True)
        finally:
            await cdp_net.close_tab(port, tid)      # 用完就关
    res["rate"] = round(len(res["ok"]) / max(1, len(codes)) * 100, 1)
    return res


async def main() -> int:
    ap = argparse.ArgumentParser(description="加库存预检（弹窗可开性回归）")
    ap.add_argument("stores", nargs="*", default=None, help="BHY / BEL；默认两店")
    ap.add_argument("--limit", type=int, default=None, help="每店只检前 N 款")
    a = ap.parse_args()
    stores = [s.upper() for s in (a.stores or ["BHY", "BEL"])]

    allres = []
    for st in stores:
        print("\n=== %s 预检 ===" % st, flush=True)
        allres.append(await precheck_store(st, a.limit))

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / ("precheck_%s.json" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    p.write_text(json.dumps(allres, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 预检汇总 ===")
    bad_total, total = 0, 0
    for r in allres:
        total += r["total"]
        bad_total += len(r["skipped"])
        print("  %-4s %d/%d 可加（%.1f%%）" % (r["store"], len(r["ok"]), r["total"], r["rate"]))
        for s in r["skipped"]:
            print("     SKIPPED %-26s %s" % (s["code"], s["why"]))
    print("  合计：%d/%d 可加，%d 个 SKIPPED" % (total - bad_total, total, bad_total))
    print("  摘要:", p)
    print("\n结论:", "✅ 100% 通过，允许自动写入" if bad_total == 0
          else "❌ 未 100% 通过 ⇒ 按门槛**不允许自动写入**（先修掉 SKIPPED 的款）")
    return 0 if bad_total == 0 else 7


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
