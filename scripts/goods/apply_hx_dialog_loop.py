# -*- coding: utf-8 -*-
"""按**回读真值**给恒信仓反馈控制：读当前 → 读加州三仓 → 算差值 → 写 → 回读，最多 N 轮。

为什么要它（2026-11-21 实测）：
    · 一个货号里有多个 SKC（BHY 203805 = 3BHC + 3BHL），批量时可能一次只改了其中一块；
    · 提交后弹窗重启的时机不稳，单次回读容易拿到旧值。
    ⇒ 一轮跑完**再读一次真实值**，还不对就按真值重算差值再来一轮；最多 3 轮。

用法：
    python scripts/goods/apply_hx_dialog_loop.py --store BHY --codes A,B [--apply] [--rounds 3]
    python scripts/goods/apply_hx_dialog_loop.py --store BHY --all [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
G = ROOT / "scripts" / "goods"


def run_apply(store: str, codes: list, apply: bool) -> tuple[int, str]:
    cmd = [PY, str(G / "apply_hx_dialog.py"), "--store", store, "--codes", ",".join(codes)]
    if apply:
        cmd.append("--apply")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def parse_readback(out: str) -> dict:
    """从输出里抠最后一行的「回读 {..}」。"""
    got = {}
    for line in out.splitlines():
        if "回读 {" in line:
            s = line.split("回读 {", 1)[1]
            s = "{" + s.split("}", 1)[0] + "}"
            try:
                got = json.loads(s.replace("'", '"'))
            except Exception:  # noqa: BLE001
                pass
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--codes", default="")
    ap.add_argument("--all", action="store_true", help="按当天快照里所有 hxHalf>0 的货号")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rounds", type=int, default=3)
    a = ap.parse_args()
    if a.all:
        sys.path.insert(0, str(G))
        import apply_hx_half as A  # noqa: PLC0415
        import datetime as _dt  # noqa: PLC0415
        tgt = A.load_targets(a.store, [], str(_dt.date.today()))
        codes = [c for c, v in tgt.items() if any(int(x) > 0 for x in v.values())]
    else:
        codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    print("[%s] 待同步货号 %d 个，最多 %d 轮" % (a.store, len(codes), a.rounds))
    remaining = list(codes)
    for r in range(1, a.rounds + 1):
        if not remaining:
            break
        print("\n===== 第 %d 轮：%d 个货号 =====" % (r, len(remaining)))
        code, out = run_apply(a.store, remaining, a.apply)
        bad = [c for c in remaining if ("❌" in out and c in out.split("❌")[0].split("==")[-1])]
        # 用「回读」行判定：本轮输出里出现 ❌ 的货号需要下一轮
        cur = None
        need = []
        for line in out.splitlines():
            if line.startswith("== "):
                cur = line[3:].split("（")[0].strip()
            if "❌" in line and cur:
                need.append(cur)
        print("   本轮有问题的货号：%s" % (", ".join(need) or "无"))
        remaining = [c for c in remaining if c in need]
        if not remaining:
            break
    print("\n最终仍不达标：%s" % (", ".join(remaining) or "无"))
    return 0 if not remaining else 1


if __name__ == "__main__":
    raise SystemExit(main())
