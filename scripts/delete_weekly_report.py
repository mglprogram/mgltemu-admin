# -*- coding: utf-8 -*-
"""删除 WorkBuddy 自动化「每周五工作周报」。

背景：该任务不在 Windows 计划任务、也不在 scheduled-tasks 注册表里，
只存在于 WorkBuddy 自带调度器（workbuddy.db 的 automations 表）。

做法：软删除（置 deleted_at），不是物理 DELETE ——
  * 与库里其他已退休任务（每日发货、每周清理）保持一致的可回溯口径
  * WorkBuddy 界面里不再显示/不再触发
  * 需要时可一条 UPDATE 复原

用法：
  python delete_weekly_report.py --dry-run
  python delete_weekly_report.py
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = Path(r"C:\Users\saing\.workbuddy\workbuddy.db")
BACKUP_DIR = Path(r"D:\AI-workplace\TEMU\backups\workbuddy-db")
TARGET_NAME = "每周五工作周报"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not DB.exists():
        print(f"找不到 {DB}")
        return 1

    # 备份（含 wal/shm，避免漏掉未落盘数据）
    if not a.dry_run:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(DB) + suffix)
            if src.exists():
                dst = BACKUP_DIR / f"workbuddy.db{suffix}.{stamp}.bak"
                shutil.copy2(src, dst)
                print(f"  备份 {src.name} -> {dst.name}")
        print()

    con = sqlite3.connect(str(DB), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout = 30000")
        rows = list(con.execute(
            "SELECT id, name, status, rrule, next_run_at, last_run_at, deleted_at "
            "FROM automations WHERE name = ? AND deleted_at IS NULL ORDER BY created_at",
            (TARGET_NAME,),
        ))
        if not rows:
            print(f"未找到未删除的「{TARGET_NAME}」（可能已删）")
            return 0

        now_ms = int(datetime.now().timestamp() * 1000)
        for r in rows:
            print(f"[{'预览' if a.dry_run else '删除'}] {r['name']}")
            print(f"    id       = {r['id']}")
            print(f"    status   = {r['status']}  ->  (置 deleted_at)")
            print(f"    rrule    = {r['rrule']}")
            print(f"    next_run = {r['next_run_at']}")
            if not a.dry_run:
                con.execute(
                    "UPDATE automations SET deleted_at = ?, updated_at = ?, "
                    "next_run_at = NULL WHERE id = ?",
                    (now_ms, now_ms, r["id"]),
                )
        if not a.dry_run:
            con.commit()
            print(f"\n已软删除 {len(rows)} 条")
        else:
            print(f"\n（dry-run，未写入。共 {len(rows)} 条）")

        # 复核
        left = list(con.execute(
            "SELECT name, status, deleted_at FROM automations "
            "WHERE deleted_at IS NULL ORDER BY created_at"))
        print("\n=== 剩余未删除任务 ===")
        for r in left:
            print(f"  [{r['status']:7s}] {r['name']}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
