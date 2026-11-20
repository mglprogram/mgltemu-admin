# -*- coding: utf-8 -*-
"""测试 BHY 的 06:30 库存快照链路：跑两遍看是否稳定 + 核对分类口径 + 打印待确认清单。

用法：python scripts/goods/test_snapshot_bhy.py [--rounds 2]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAP = ROOT / "data" / "stock-snapshot"
PANEL = ROOT / "temu-admin" / "public" / "data" / "stock-snapshot.json"
PENDING = SNAP / "pending-presale.json"
PY = sys.executable


def run_once(extra: list | None = None) -> tuple[int, str]:
    cmd = [PY, str(ROOT / "scripts" / "goods" / "daily_stock_snapshot.py")] + (extra or [])
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()
    ok = True

    # ① 连跑 N 遍：每次都要 rc=0，且分类总数不变（同一份数据应幂等）
    prev = None
    for i in range(1, a.rounds + 1):
        rc, out = run_once()
        line = [l for l in out.splitlines() if l.startswith("[BHY]")]
        print("第 %d 遍 rc=%d %s" % (i, rc, line[0] if line else "(无输出)"))
        if rc != 0:
            ok = False
        cur = (line[0] if line else "")
        if prev is not None and cur != prev:
            print("  ⚠ 两遍结果不一致：\n    旧 %s\n    新 %s" % (prev, cur))
            ok = False
        prev = cur

    # ② 快照文件本身
    f = SNAP / ("%s.json" % date.today())
    if not f.exists():
        print("✗ 快照没生成：", f)
        return 1
    d = json.loads(f.read_text(encoding="utf-8"))
    b = (d.get("stores") or {}).get("BHY") or {}
    rows = b.get("rows") or []
    print("\n快照：%s｜BHY SKU %d / 货号 %d" % (d.get("generatedAt"), len(rows),
                                              len({r.get("code") for r in rows})))
    # ③ 口径校验（抽样断言，任何一条不成立就报红）
    checks = [
        ("有行", len(rows) > 0),
        ("恒信一半 = ceil(领星可用/2)",
         all(r["hxHalf"] == (int(-(-(r["lxAvail"]) // 2)) if r["lxAvail"] > 0 else 0) for r in rows)),
        ("待开预售都有建议量", all(r["planQty"] > 0 for r in rows if r["class"] == "待开预售")),
        ("已开预售都带在途预售列", all("presaleQty" in r for r in rows if r["class"] == "已开预售")),
        ("15天预售不进待开预售",
         not any(r["class"] == "待开预售" and r["planAction"].startswith("不动") for r in rows)),
    ]
    for name, cond in checks:
        print(("  ✅ " if cond else "  ✗ ") + name)
        ok = ok and bool(cond)

    # ④ 面板数据 / 待确认清单
    panel = json.loads(PANEL.read_text(encoding="utf-8")) if PANEL.exists() else {}
    pend = json.loads(PENDING.read_text(encoding="utf-8")) if PENDING.exists() else {}
    print("\n面板数据：%s｜待确认 %d 条｜历史 %d 天"
          % (panel.get("generatedAt"), len(panel.get("pending") or []),
             len(panel.get("history") or [])))
    items = pend.get("items") or []
    print("待确认清单（%s）%d 条：" % (pend.get("generatedAt"), len(items)))
    for it in items:
        print("  %-24s %-4s 建议 %-4s 领星 %-5s 日均 %-6s %s"
              % (it["code"], it["size"], it["suggestQty"], it["lxAvail"], it["daily"], it["status"]))
    ok = ok and len(items) == len(panel.get("pending") or [])

    print("\n结论：", "✅ 全部通过" if ok else "❌ 有问题，见上面标红的项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
