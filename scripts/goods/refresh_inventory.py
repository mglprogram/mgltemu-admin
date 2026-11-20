# -*- coding: utf-8 -*-
"""抓最新领星总库存（LM739 恒信）+ 帕沃尔现货，刷新 linxing-stock.json / pw-stock.json。

用户 2026-11-20：「抓取最新的领星总库存 - 更新恒信仓库存，定时早上 6:30、12:30、18:30」。

本脚本是 06:30 / 12:30 / 18:30 那一轮的**第 ① 步**：
    fetch_linxing_stock.py（领星）→ fetch_pw_stock.py（帕沃尔加州3 + Kansas）
然后由 `run_hx_half_sync.cmd` 继续跑快照 + 恒信仓写入。

用法：
    python scripts/goods/refresh_inventory.py            # 拉两路库存
    python scripts/goods/refresh_inventory.py --dry       # 只看统计不落盘
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
STEPS = [
    ("领星 LM739", ROOT / "scripts" / "goods" / "fetch_linxing_stock.py"),
    ("帕沃尔", ROOT / "scripts" / "goods" / "fetch_pw_stock.py"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    rc_all = 0
    for name, script in STEPS:
        if not script.exists():
            print("[%s] 缺脚本 %s（跳过）" % (name, script.name))
            continue
        cmd = [PY, str(script)] + (["--dry"] if a.dry else [])
        print("[%s] 跑 %s" % (name, script.name))
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
        for line in tail[-3:]:
            print("   ", line[:160])
        print("   → rc=%d" % p.returncode)
        if p.returncode != 0:
            rc_all = p.returncode
    return rc_all


if __name__ == "__main__":
    raise SystemExit(main())
