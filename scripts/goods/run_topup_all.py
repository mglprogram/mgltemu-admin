# -*- coding: utf-8 -*-
"""每天 08:40 接着日更：把两店「可加库存」的款·尺码**自动加进 TEMU**（只加加州仓）。

分工（与日更链路的关系）
------------------------
08:00 `TEMU-Goods-OnSale-Daily`  : 重新导出在售中库存 → 刷新看板/飞书 → 判定 → 发布面板
08:40 `TEMU-Goods-Topup-Daily`   : **本脚本** —— 读上面那份判定，逐个款把库存加上去

安全闸门（都会打印出来，失败也不静默）
--------------------------------------
① 只处理面板判为「可加库存」且建议加量 > 0 的款（口径与面板同源）；
② 每款提交前后都回读弹窗：填数与计划不一致就**中止该款不提交**；
③ 提交要点**两次确认**（第二次是"库存低于10件"的风险提示框），漏了不会生效；
④ 提交后**以服务端为准**：重开弹窗读当前库存，与「原值+加量」比对，不一致即判失败；
⑤ 连续失败 N 款（默认 3）就停，避免把错误状态放大。

用法
----
    python scripts/goods/run_topup_all.py                # 两店都跑（真提交）
    python scripts/goods/run_topup_all.py --store BHY    # 只跑一家
    python scripts/goods/run_topup_all.py --dry-run      # 只看计划，不动库存
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ENTRY = HERE / "run_stock_topup.py"
OUT = ROOT / "out" / "stock-topup"


def run_store(store: str, apply: bool, stop_after: int, precheck: bool = False,
              allow_partial: bool = False) -> int:
    """跑一家店：调 run_stock_topup.py --all（批量模式）。

    `--apply` 时会在提交前自动跑**预检**（逐款确认弹窗可开），预检不是 100% 通过
    就直接拒绝提交（除非显式给 allow_partial）。
    """
    cmd = [sys.executable, "-u", str(ENTRY), "--store", store, "--all",
           "--stop-after-fails", str(stop_after)]
    if precheck:
        cmd.append("--precheck")
    elif apply:
        cmd.append("--apply")
        if allow_partial:
            cmd.append("--allow-partial")
    print("$ %s" % " ".join(cmd[1:]), flush=True)
    t0 = time.time()
    rc = subprocess.call(cmd, cwd=str(ROOT))
    print("[%s] rc=%s 用时 %.1f 分钟" % (store, rc, (time.time() - t0) / 60), flush=True)
    return rc


def summarize() -> None:
    """跑完把批量清单汇总成一句话（便于日志与人工核对）。"""
    import json
    files = sorted(OUT.glob("*_ALL_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        print("（没有批量清单文件）", flush=True)
        return
    latest = files[0]
    try:
        d = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print("清单读取失败 %s: %s" % (latest.name, str(e)[:80]), flush=True)
        return
    res = d.get("results") or []
    ok = [r for r in res if r.get("applied") and r.get("rc") == 0]
    bad = [r for r in res if r.get("applied") and r.get("rc") not in (0, None)]
    adds = sum(p.get("add") or 0 for r in ok for p in (r.get("plan") or []))
    print("汇总(%s): 成功 %d 款 / 已加 %d 件 / 失败 %d 款"
          % (latest.name, len(ok), adds, len(bad)), flush=True)
    for r in bad:
        print("   失败: %s (rc=%s) 计划 %s"
              % (r.get("code"), r.get("rc"),
                 "、".join("%s+%s" % (p["size"], p["add"]) for p in (r.get("plan") or []))),
              flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="两店批量加库存（只加加州仓）")
    ap.add_argument("--store", default=None, help="只跑这家（BHY / BEL）；默认两店")
    ap.add_argument("--dry-run", action="store_true", help="只看计划，不提交")
    ap.add_argument("--stop-after-fails", type=int, default=3)
    ap.add_argument("--precheck", action="store_true",
                    help="只做预检：逐款确认「修改库存」弹窗可开，不填数不提交")
    ap.add_argument("--allow-partial", action="store_true",
                    help="预检没 100% 通过时也继续（默认拒绝提交）")
    a = ap.parse_args()

    stores = [a.store.upper()] if a.store else ["BHY", "BEL"]
    mode = ("只预检（不打库存）" if a.precheck
            else "dry-run 只看计划" if a.dry_run else "真提交（先预检，未 100% 通过则拒绝）")
    print("=== 批量加库存开始：%s（%s）===" % ("、".join(stores), mode), flush=True)

    rc_all = 0
    for st in stores:
        rc = run_store(st, apply=not a.dry_run, stop_after=a.stop_after_fails,
                       precheck=a.precheck, allow_partial=a.allow_partial)
        if rc:
            rc_all = rc
    summarize()
    print("=== 批量加库存结束 rc=%s ===" % rc_all, flush=True)
    return rc_all


if __name__ == "__main__":
    raise SystemExit(main())
