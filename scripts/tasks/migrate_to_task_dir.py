# -*- coding: utf-8 -*-
"""一次性迁移：把注册表里的脚本入口指向新的 scripts\\tasks\\，并补上 category。

背景（2026-10-28）
------------------
15 个 run_*.cmd 原来平铺在 scripts\\ 根目录，和 60+ 个业务脚本混在一起，
光看目录分不清「哪个是定时任务入口、哪个是库函数」。现已全部移到
scripts\\tasks\\ 并统一命名 run_<分类>_<任务>.cmd。

本脚本只改注册表，不动任何文件：
  * action.arguments 里的 run_via_dsh.cmd 路径
  * action.arguments 里直调的老 run_*.cmd 路径（TEMU-Stock-Track-Daily）
  * _原action.arguments 里同样的路径（否则 --revert 会还原成一个不存在的路径）
  * dsh.inner
  * 新增 category（面板分组用）

用法：
    python scripts/tasks/migrate_to_task_dir.py --dry-run
    python scripts/tasks/migrate_to_task_dir.py --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

REGISTRY = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"
BACKUP_DIR = Path(r"D:\AI-workplace\TEMU\backups\scheduled-tasks")

PROJ = r"D:\AI-workplace\TEMU"

#: 旧文件名 -> 新文件名（相对 scripts\ 而言）
RENAMES = {
    r"run_temu_watch.cmd": r"tasks\run_temu_limited_watch.cmd",
    r"run_presale_notify.cmd": r"tasks\run_temu_presale_notify.cmd",
    r"run_bel_abnormal_daily.cmd": r"tasks\run_temu_bel_abnormal.cmd",
    r"run_overdue_daily.cmd": r"tasks\run_temu_overdue_confirm.cmd",
    r"run_temu_flux.cmd": r"tasks\run_temu_flux_weekly.cmd",
    r"run_goods_daily.cmd": r"tasks\run_temu_goods_onsale.cmd",
    r"run_goods_topup.cmd": r"tasks\run_temu_goods_topup.cmd",
    r"run_today_sales.cmd": r"tasks\run_temu_today_sales.cmd",
    r"run_oms_orders.cmd": r"tasks\run_temu_oms_orders.cmd",
    r"run_stock_track.cmd": r"tasks\run_temu_stock_track.cmd",
    r"run_task_watch.cmd": r"tasks\run_ops_task_watch.cmd",
    r"run_archive_cleanup.cmd": r"tasks\run_ops_archive_cleanup.cmd",
    r"run_panel_refresh.cmd": r"tasks\run_panel_refresh.cmd",
    r"run_via_dsh.cmd": r"tasks\run_via_dsh.cmd",
}

#: 任务 -> 分类。分类是面板分组与巡检汇总的口径，取值固定：
#:   temu-store  TEMU 店铺（抓数/确认/导出）
#:   panel       面板数据与发布
#:   ops         巡检、清理、守护
#:   pw          帕沃尔海外仓
#:   reminder    提醒与建目录
#:   other       其它（WorkBuddy 自身）
CATEGORY = {
    "TEMU限流监控": "temu-store",
    "TEMU-Presale-Notify": "temu-store",
    "TEMU-BEL-Abnormal-Daily": "temu-store",
    "TEMU-Overdue-Confirm-Daily": "temu-store",
    "TEMU流量加速周更": "temu-store",
    "TEMU_Bottom_DailyStockSync": "temu-store",
    "TEMU-Goods-OnSale-Daily": "temu-store",
    "TEMU-Goods-DailyList": "temu-store",
    "TEMU-Today-Sales": "temu-store",
    "TEMU-OMS-Orders-Daily": "temu-store",
    "TEMU-Stock-Track-Daily": "temu-store",
    "AutomationPanel-Refresh": "panel",
    "TEMU-TaskWatch-30min": "ops",
    "Archive-Cleanup-Weekly": "ops",
    "WorkBuddy-Proxy-Sentinel": "ops",
    "CreateDailyFolder_Lehang_0900": "reminder",
    "DailyReminder1440": "reminder",
    "WorkBuddyAutoSignin": "other",
    "WorkBuddyGrowthPoll": "other",
}

CATEGORY_LABEL = {
    "temu-store": "TEMU 店铺",
    "panel": "面板与发布",
    "ops": "巡检与清理",
    "pw": "帕沃尔海外仓",
    "reminder": "提醒与目录",
    "other": "其它",
}


def rewrite_path(text: str) -> str:
    """把老的 scripts\\run_*.cmd 换成 scripts\\tasks\\run_*.cmd。

    注意只替换**已知文件名**，不做泛化正则 —— 业务脚本里也有 run_ 开头的，
    泛化替换会把 scripts\\power\\run_temu_buylabel.py 一起改坏。
    """
    if not text:
        return text
    out = text
    for old, new in RENAMES.items():
        # 只匹配 scripts\ 后面紧跟老文件名（可带 \ 或 " 或行尾）
        for sep in ("\\", "/"):
            out = out.replace(sep + old, sep + new)
    return out


def migrate(reg: dict, dry: bool) -> tuple[int, list[str]]:
    changed = 0
    report: list[str] = []
    for entry in reg.get("tasks") or []:
        name = entry.get("name", "")
        touched = []

        # ---- action ----
        act = entry.get("action") or {}
        old_args = act.get("arguments") or ""
        new_args = rewrite_path(old_args)
        if new_args != old_args:
            act["arguments"] = new_args
            entry["action"] = act
            touched.append("action")

        # ---- _原action（保持 --revert 可用）----
        old_act = entry.get("_原action")
        if isinstance(old_act, dict):
            oa = old_act.get("arguments") or ""
            na = rewrite_path(oa)
            if na != oa:
                old_act["arguments"] = na
                touched.append("_原action")

        # ---- dsh.inner ----
        cfg = entry.get("dsh")
        if isinstance(cfg, dict) and cfg.get("inner"):
            oi = cfg["inner"]
            ni = rewrite_path(oi)
            if ni != oi:
                cfg["inner"] = ni
                touched.append("dsh.inner")

        # ---- category ----
        want_cat = CATEGORY.get(name)
        if want_cat and entry.get("category") != want_cat:
            entry["category"] = want_cat
            touched.append(f"category={want_cat}")

        if touched:
            changed += 1
            report.append(f"  {name:34s} {' '.join(touched)}")
    return changed, report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="（默认行为，写出来只为顺手）")
    args = ap.parse_args()
    dry = not args.apply

    reg = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
    changed, report = migrate(reg, dry)

    print("\n".join(report))
    print(f"\n{'would change' if dry else 'changed'} {changed} task(s)")

    # 校验：所有 .cmd 路径都要真实存在
    missing = []
    for entry in reg.get("tasks") or []:
        for blob in (
            (entry.get("action") or {}).get("arguments") or "",
            (entry.get("_原action") or {}).get("arguments") or "",
        ):
            for old, new in RENAMES.items():
                if old in blob and "tasks\\" not in blob:
                    missing.append(entry.get("name"))
    if missing:
        print("WARN 仍有未替换的老路径: " + ", ".join(sorted(set(missing))))

    if dry:
        print("(dry run - nothing written)")
        return 0
    if changed == 0:
        print("no change; not writing")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    bak = BACKUP_DIR / f"tasks.json.{time.strftime('%Y%m%d-%H%M%S')}.bak"
    shutil.copy2(REGISTRY, bak)
    print(f"backup: {bak}")

    REGISTRY.write_text(
        json.dumps(reg, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8-sig",
        newline="\n",
    )
    print(f"written: {REGISTRY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
