#!/usr/bin/env python3
"""Migrate the scheduled-tasks registry so every task runs via a DSH agent session.

For each migrated task this rewrites `action` to launch tasks/run_via_dsh.cmd with the
task name as its single argument, and records the original entry point as
`dsh.inner` so nothing is lost and run_via_dsh.cmd can resolve it (both on the
agent path and on the kill-switch fallback).

Tasks that must stay untouched:
  * DailyReminder1440      - the popup IS the feature; an agent adds nothing and
                             a hidden window would defeat the purpose.
  * WorkBuddyAutoSignin    - runs via pythonw.exe with no console at all; it
  * WorkBuddyGrowthPoll      already succeeds unattended, and wrapping it in an
                             LLM adds an API dependency for zero gain.
  * CreateDailyFolder_Lehang_0900 - a one-line PowerShell mkdir; nothing to fix.

Everything is reversible: `--revert` puts the original `action` back.

Usage:
    python scripts/migrate_registry_to_dsh.py --dry-run
    python scripts/migrate_registry_to_dsh.py --apply
    python scripts/migrate_registry_to_dsh.py --revert --apply
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time

REGISTRY = os.path.join(
    os.path.expanduser("~"), ".agents", "skills", "scheduled-tasks", "tasks.json"
)
BACKUP_DIR = r"D:\AI-workplace\TEMU\backups\scheduled-tasks"

PROJ = r"D:\AI-workplace\TEMU"
VBS = r"C:\Users\saing\.agents\skills\scheduled-tasks\scripts\run_hidden.vbs"
RUNNER = os.path.join(PROJ, "scripts", "tasks", "run_via_dsh.cmd")

# Task name -> the run_*.cmd (or .ps1/.bat) it already uses, relative to PROJ
# unless absolute. Only tasks whose entry point lives under PROJ are migrated
# by this script; the rest are listed so the decision is explicit.
INNER = {
    "TEMU限流监控": r"scripts\tasks\run_temu_limited_watch.cmd",
    "TEMU-Presale-Notify": r"scripts\tasks\run_temu_presale_notify.cmd",
    "AutomationPanel-Refresh": r"scripts\tasks\run_panel_refresh.cmd",
    "TEMU-BEL-Abnormal-Daily": r"scripts\tasks\run_temu_bel_abnormal.cmd",
    "TEMU-Overdue-Confirm-Daily": r"scripts\tasks\run_temu_overdue_confirm.cmd",
    "TEMU流量加速周更": r"scripts\tasks\run_temu_flux_weekly.cmd",
    "TEMU-TaskWatch-30min": r"scripts\tasks\run_ops_task_watch.cmd",
    "Archive-Cleanup-Weekly": r"scripts\tasks\run_ops_archive_cleanup.cmd",
    "TEMU-Goods-OnSale-Daily": r"scripts\tasks\run_temu_goods_onsale.cmd",
    "TEMU-Goods-DailyList": r"scripts\tasks\run_temu_goods_topup.cmd",
    "TEMU-Today-Sales": r"scripts\tasks\run_temu_today_sales.cmd",
    "TEMU-OMS-Orders-Daily": r"scripts\tasks\run_temu_oms_orders.cmd",
    "TEMU-Stock-Track-Daily": r"scripts\tasks\run_temu_stock_track.cmd",
}

# Left on the direct path, with the reason recorded in the registry.
EXCLUDED = {
    "DailyReminder1440": "弹窗提醒本身就是功能，交给 agent 没有收益且会失去窗口",
    "WorkBuddyAutoSignin": "pythonw.exe 无控制台直跑，已稳定；套 LLM 只是多一个外部依赖",
    "WorkBuddyGrowthPoll": "同上：无控制台直跑，已稳定",
    "CreateDailyFolder_Lehang_0900": "一行 PowerShell mkdir，没有可修复的空间",
    "TEMU_Bottom_DailyStockSync": "入口归 temu-bottom-stock-sync 技能自己管，不在本仓库",
}


def build_action(task: str) -> dict:
    # run_hidden.vbs receives ONE inner command line. Quoting rules:
    #   outer  "..."      -> what wscript hands to cmd
    #   ""...""           -> a literal quote inside that string
    # So the inner command is:  cmd /c "runner" "TASKNAME"
    q = '"'
    inner = "cmd /c " + q + RUNNER + q + " " + q + task + q
    quoted_inner = q + inner.replace(q, q + q) + q
    return {
        "execute": "wscript.exe",
        "arguments": "//nologo " + q + VBS + q + " " + quoted_inner,
    }


def load() -> dict:
    with open(REGISTRY, encoding="utf-8-sig") as fh:
        return json.load(fh)


def save(reg: dict) -> None:
    # Keep the BOM: PowerShell's Set-Content/ConvertTo-Json wrote one originally.
    with open(REGISTRY, "w", encoding="utf-8-sig", newline="\n") as fh:
        json.dump(reg, fh, ensure_ascii=False, indent=4)
        fh.write("\n")


def backup() -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, f"tasks.json.{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(REGISTRY, dst)
    return dst


def migrate(reg: dict, dry: bool, only: set[str] | None = None) -> int:
    changed = 0
    only = only or set()
    for entry in reg.get("tasks", []):
        name = entry.get("name")
        if only and name not in only:
            continue
        if name in EXCLUDED:
            if dry:
                print(f"  skip  {name:38s} (excluded: {EXCLUDED[name][:40]})")
            continue
        if name not in INNER:
            print(f"  WARN  {name:38s} not in INNER and not excluded - leaving as-is")
            continue
        dsh_cfg = entry.setdefault("dsh", {})
        if "inner" not in dsh_cfg:
            dsh_cfg["inner"] = INNER[name]
        dsh_cfg["enabled"] = True
        dsh_cfg["_说明"] = (
            "经 scripts\\tasks\\run_via_dsh.cmd 交给 DSH headless 会话执行。"
            "inner 是原来的入口，kill switch 下直接调它。"
        )
        new_action = build_action(name)
        if entry.get("action") != new_action:
            entry["_原action"] = entry.get("action")
            entry["action"] = new_action
            changed += 1
        if dry:
            print(f"  plan  {name:38s} -> run_via_dsh.cmd  inner={INNER[name]}")
    for name, why in EXCLUDED.items():
        for entry in reg.get("tasks", []):
            if entry.get("name") == name:
                entry.setdefault("dsh", {})["enabled"] = False
                entry["dsh"]["_不迁移原因"] = why
    return changed


def revert(reg: dict, dry: bool, only: set[str] | None = None) -> int:
    changed = 0
    only = only or set()
    for entry in reg.get("tasks", []):
        if only and entry.get("name") not in only:
            continue
        if "_原action" in entry:
            entry["action"] = entry.pop("_原action")
            cfg = entry.get("dsh")
            if isinstance(cfg, dict):
                cfg["enabled"] = False
            changed += 1
            if dry:
                print(f"  revert {entry.get('name')}")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated task names to touch")
    args = ap.parse_args()
    dry = not args.apply

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    reg = load()
    n = revert(reg, dry, only) if args.revert else migrate(reg, dry, only)
    print(f"\n{'would change' if dry else 'changed'} {n} task(s)")

    if dry:
        print("(dry run - nothing written; add --apply to write)")
        return 0
    if n == 0:
        print("no change; not writing")
        return 0

    b = backup()
    print(f"backup: {b}")
    save(reg)
    print(f"written: {REGISTRY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
