# -*- coding: utf-8 -*-
"""批量跑 apply_hx_half：逐个货号独立进程，失败重试，汇总台账。

用户 2026-11-20：「你现在就可以去更新恒信仓的库存，拿 BHY 测试」。
批量的稳定性靠「一次只跑一个货号 + 失败重试」，而不是在一个进程里连跑（连跑会踩标签页状态）。

用法：
    python scripts/goods/run_hx_half_batch.py --limit 5            # 先试 5 个
    python scripts/goods/run_hx_half_batch.py --apply             # 全部（跳过已达标的）
    python scripts/goods/run_hx_half_batch.py --codes A,B --apply
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
LEDGER = ROOT / "data" / "hx-half-ledger.json"
PY = sys.executable


def targets(store: str, day: str, codes: list) -> dict:
    d = json.loads((SNAP / ("%s.json" % day)).read_text(encoding="utf-8"))
    rows = ((d.get("stores") or {}).get(store) or {}).get("rows") or []
    out: dict = {}
    for r in rows:
        if codes and r["code"] not in codes:
            continue
        if int(r.get("hxHalf") or 0) <= 0:
            continue
        out.setdefault(r["code"], {})[str(r["size"]).upper()] = int(r["hxHalf"])
    return out


def done_codes(store: str) -> dict:
    if not LEDGER.exists():
        return {}
    out: dict = {}
    for it in (json.loads(LEDGER.read_text(encoding="utf-8")).get("items") or []):
        out.setdefault(it["code"], {})[it["size"]] = it["hx"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--codes", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    tgt = targets(a.store, a.date, codes)
    led = done_codes(a.store)
    todo = [c for c, sizes in tgt.items() if led.get(c) != sizes]
    if a.limit:
        todo = todo[:a.limit]
    print("需要同步的货号 %d 个（已达标 %d 个，总 %d）"
          % (len(todo), len(tgt) - len(todo), len(tgt)))
    ok = fail = 0
    for i, code in enumerate(todo, 1):
        print("\n[%d/%d] %s" % (i, len(todo), code))
        cmd = [PY, str(ROOT / "scripts" / "goods" / "apply_hx_half.py"),
               "--store", a.store, "--date", a.date, "--codes", code]
        if a.apply:
            cmd.append("--apply")
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        tail = [l for l in out.splitlines() if ("回读" in l or "✗" in l or "Completion" in l)]
        for l in tail[-2:]:
            print("   ", l.strip())
        if p.returncode == 0 and "✅" in out:
            ok += 1
        else:
            fail += 1
            print("    ⚠ 这一轮没成功（rc=%d）——可重跑" % p.returncode)
    # 汇总
    led = done_codes(a.store)
    still = [c for c, sizes in tgt.items() if led.get(c) != sizes]
    print("\n本轮成功 %d / 失败 %d；全量还差 %d 个货号" % (ok, fail, len(still)))
    if still:
        print("  待补：", ", ".join(still[:12]), "…" if len(still) > 12 else "")
    return 0 if not still else 1


if __name__ == "__main__":
    raise SystemExit(main())
