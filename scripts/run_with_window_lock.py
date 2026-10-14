# -*- coding: utf-8 -*-
"""run_with_window_lock.py — 在紫鸟窗口锁内跑一条命令（给 run_*.cmd 用的通用包装）。

为什么有它（2026-10-14 立）：
    6 个任务会用到紫鸟店铺窗口。把锁逻辑写进每个业务脚本内部，要改 6 处、且各自的
    退出码语义不同（HARD STOP 42 / 3 / 4 混用）；写进每个 .cmd 又要复制 5 遍。
    这个包装把「取锁 → 跑 → 放锁 → 退出码照常透传」收敛成一处，.cmd 只改一行。

用法（在 run_*.cmd 里替换原来的 python 调用）：
    python scripts\\run_with_window_lock.py --window "BHY+BEL" --action presale ^
        -- cmd /c python scripts\\bel_goods\\run_presale.py run

拿不到锁时：
    · 默认 exit 5 + 打一行 `[WINDOW BUSY]`（**不硬插、不双跑**）；
    · 打 `--wait 120` 可先等最多 120 秒再放弃（给"知道它可能刚跑完"的场合用）。

退出码：被包裹命令的退出码**原样透传**（锁只负责互斥，不改业务语义）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import window_lock  # noqa: E402

#: 拿不到窗口锁的退出码（**独立取值**，与业务退出码 1/3/4/42 都不撞，便于日志一眼分辨）
BUSY_EXIT = 5


def main() -> int:
    ap = argparse.ArgumentParser(description="在紫鸟窗口锁内跑一条命令")
    ap.add_argument("--window", required=True,
                    help='紫鸟窗口键，如 "BHY+BEL" / "BEL"；空串 "" = 不锁（显式声明不用窗口）')
    ap.add_argument("--action", default="run", help="动作名（写进锁记事，排查时看是谁占着）")
    ap.add_argument("--wait", type=float, default=0,
                    help="拿不到锁时最多等多少秒再放弃（默认 0＝立刻失败）")
    ap.add_argument("--stale-minutes", type=float, default=window_lock.DEFAULT_STALE_MINUTES,
                    help="多久算陈旧锁可接管（默认 %d）" % window_lock.DEFAULT_STALE_MINUTES)
    ap.add_argument("--no-lock", action="store_true", help="临时关掉锁（排查用，别在定时任务里用）")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 之后是要跑的命令")
    a = ap.parse_args()

    cmd = [c for c in a.cmd if c != "--"]
    if not cmd:
        print("[ERROR] 没给要跑的命令（用 `-- <cmd>`）", file=sys.stderr)
        return 2

    def _log(msg: str) -> None:
        print(msg, flush=True)

    try:
        with window_lock.window_lock(a.window, a.action, stale_minutes=a.stale_minutes,
                                     enabled=not a.no_lock, wait_seconds=a.wait, log=_log):
            r = subprocess.run(cmd)
            return int(r.returncode or 0)
    except window_lock.WindowBusy as e:
        # 明确报出来（这条会被 run_*.cmd 记进日志、被 TaskWatch 抓到）
        print("[WINDOW BUSY] %s" % e, file=sys.stderr, flush=True)
        _log("[WINDOW BUSY] 未取得紫鸟窗口锁 %s，本次不跑（不硬插、不双跑）" % a.window)
        return BUSY_EXIT


if __name__ == "__main__":
    sys.exit(main())
