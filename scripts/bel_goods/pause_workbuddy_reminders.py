# -*- coding: utf-8 -*-
"""停用 WorkBuddy 里的每日必做提醒（已迁到 DSH，避免重复触发）。

做法：把 status 置为 PAUSED、next_run_at 清空。
不写 deleted_at —— 那是「删除」语义，PAUSED 更符合「停用」，
且 WorkBuddy 界面里还能看到、随时可恢复。

用法：
  python pause_workbuddy_reminders.py --dry-run
  python pause_workbuddy_reminders.py
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
BACKUP_DIR = Path(r"D:\AI-workplace\TEMU\backups\workbuddy-db-20261010")

TARGETS = [
    "每日必做任务提醒 - 早9:00",
    "每日必做任务提醒 - 下午15:00",
    "每日必做任务提醒 - 晚19:00",
]


def backup() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(DB) + suffix)
        if src.exists():
            dst = BACKUP_DIR / f"workbuddy.db{suffix}.{stamp}.bak"
            shutil.copy2(src, dst)
            print(f"  备份 {src.name} -> {dst.name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not DB.exists():
        print(f"找不到 {DB}")
        return 1

    if not a.dry_run:
        print("备份数据库：")
        backup()

    con = sqlite3.connect(str(DB), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout = 30000")
        now_ms = int(datetime.now().timestamp() * 1000)
        done = 0
        for name in TARGETS:
            row = con.execute(
                "SELECT id, name, status, next_run_at FROM automations "
                "WHERE name = ? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
                (name,),
            ).fetchone()
            if not row:
                print(f"[跳过] 找不到或已是删除态：{name}")
                continue
            print(f"[{'预览' if a.dry_run else '停用'}] {row['name']}")
            print(f"    id={row['id']}")
            print(f"    status: {row['status']} -> PAUSED")
            print(f"    next_run_at: {row['next_run_at']} -> None")
            if not a.dry_run:
                con.execute(
                    "UPDATE automations SET status = 'PAUSED', next_run_at = NULL, "
                    "updated_at = ? WHERE id = ?",
                    (now_ms, row["id"]),
                )
            done += 1
        if not a.dry_run:
            con.commit()
            print(f"\n已提交，共停用 {done} 条")
        else:
            print(f"\n预览完成，共 {done} 条（未写入）")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
