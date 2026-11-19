# -*- coding: utf-8 -*-
"""用 11-12 那轮的真实 run.json 回算：新口径会不会判成「带告警成功」。

只读：跑 verdict_code()，不触发任何业务动作。
"""
import glob
import io
import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJ, "scripts", "tasks"))
import run_fulfill_daily_inner as F  # noqa: E402

DAY = sys.argv[1] if len(sys.argv) > 1 else "2026-11-12"
pat = os.path.join(PROJ, "scripts", "power", "out", DAY, "fulfill", "*", "run.json")

results = []
for p in sorted(glob.glob(pat)):
    d = json.load(io.open(p, encoding="utf-8"))
    s = d.get("summary") or {}
    store = d.get("store") or os.path.basename(os.path.dirname(p))
    results.append({
        "store": store,
        # 与 inner 同口径：门店 ok 来自子进程 rc；这里用「有没有失败步骤」等价表达
        # （summary.failed 为空 ⇒ 那一步没出错 ⇒ rc=0）。
        "ok": not (s.get("failed") or []),
        "rc": 42 if (s.get("failed") or []) else 0,
        "summary": s,
        "source": os.path.relpath(p, PROJ),
    })

for r in results:
    s = r["summary"]
    print("%-6s ok=%-5s hard=%s soft=%s" % (
        r["store"], r["ok"], s.get("hard_failed"), s.get("soft_failed")))
print("verdict_code =", F.verdict_code(results), "(3 = 带告警成功)")
