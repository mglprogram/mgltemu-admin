# -*- coding: utf-8 -*-
"""06:30 / 12:30 / 18:30 那一轮的总入口：抓最新库存 → 刷新快照 → 更新恒信仓库存。

用户 2026-11-20：「抓取最新的领星总库存 - 更新恒信仓库存，定时早上 6:30、12:30、18:30」。

链路：
    ① refresh_inventory.py   领星 LM739 + 帕沃尔现货 → linxing-stock.json / pw-stock.json
    ② daily_stock_snapshot.py 算「一半 / 待清零 / 待开预售 / 已开预售」→ 快照 + 面板数据
    ③ apply_hx_half.py --apply（仅当 --apply）：把恒信加州3仓写成 max(0, 一半 − 加州三仓已有)
    ④ verify_hx_half.py      按台账核对

⚠ 默认 **只做 ①②④**（只读 + 出清单）；`--apply` 才真的写 TEMU 后台。
⚠ 清零/开预售 **永远不在本脚本里执行** —— 只出清单（pending-presale.json），等人确认。

用法：
    python scripts/goods/run_hx_sync.py            # 抓库存 + 出清单（不写后台）
    python scripts/goods/run_hx_sync.py --apply    # 抓库存 + 刷新快照 + 写恒信仓
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
G = ROOT / "scripts" / "goods"


def step(title: str, cmd: list, allow_fail: bool = False) -> int:
    print("\n=== %s ===" % title)
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
    for line in out[-12:]:
        print("  " + line[:180])
    print("  → rc=%d" % p.returncode)
    if p.returncode != 0 and not allow_fail:
        return p.returncode
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--apply", action="store_true", help="真的写 TEMU 后台（默认只出清单）")
    a = ap.parse_args()

    rc = step("① 抓最新库存（领星 + 帕沃尔）", [PY, str(G / "refresh_inventory.py")])
    if rc:
        print("!! 抓库存失败，后面不再继续（避免用旧数据算一半）")
        return rc

    rc = step("② 刷新快照 + 清单", [PY, str(G / "daily_stock_snapshot.py"), "--stores", a.store])
    if rc:
        return rc

    if a.apply:
        rc = step("③ 更新恒信仓库存", [PY, str(G / "apply_hx_half.py"),
                                     "--store", a.store, "--apply"])
        if rc:
            print("!! 写后台有失败项，后面只做核对")
    else:
        print("\n=== ③ 跳过写后台（没给 --apply）：清零/开预售/恒信仓都只出清单 ===")

    step("④ 按台账核对", [PY, str(G / "verify_hx_half.py"), "--store", a.store], allow_fail=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
