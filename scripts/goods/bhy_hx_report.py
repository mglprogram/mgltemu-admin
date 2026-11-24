# -*- coding: utf-8 -*-
"""把 _logs/api_hx_stock.jsonl 整理成「BHY 恒信仓同步清单」。

输出：_logs/bhy_hx_list.md + .csv
    · 写入过的货号：目标 vs 改后回读（含是否一致）
    · 已达标跳过的货号
    · 失败货号 + 原因
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "_logs"
OUT_MD = LOGS / "bhy_hx_list.md"
OUT_CSV = LOGS / "bhy_hx_list.csv"


def main():
    rows = [json.loads(l) for l in (LOGS / "api_hx_stock.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    writes = {}
    nothings = set()
    fails = {}
    for r in rows:
        e = r.get("event")
        c = r.get("code")
        if e == "write":
            writes.setdefault(c, {})["ok"] = bool(r.get("ok"))
            writes[c]["ts"] = r.get("ts")
        elif e == "verify":
            writes.setdefault(c, {})["got"] = r.get("got") or {}
            writes[c]["want"] = r.get("want") or {}
            writes[c]["match"] = bool(r.get("match"))
        elif e == "nothing":
            nothings.add(c)
        elif e in ("skc-empty", "skc-fail"):
            fails[c] = r.get("status") or e

    md = ["# BHY 恒信仓同步清单（2026-11-23）", "",
          "口径：恒信加州3 = max(0, ⌈领星可用×80%⌉)；加州三仓 / Kansas = 0", "",
          "## 已写入并回读（%d 个货号）" % len(writes), "",
          "| 货号 | 恒信目标（尺码） | 回读 | 一致 |", "|---|---|---|---|"]
    for c in sorted(writes):
        w = writes[c]
        want = " / ".join("%s=%s" % (k, v) for k, v in sorted((w.get("want") or {}).items()))
        got = " / ".join("%s=%s" % (k, v) for k, v in sorted((w.get("got") or {}).items()))
        md.append("| %s | %s | %s | %s |" % (c, want or "-", got or "-",
                                            "✅" if w.get("match") else "❌"))
    md += ["", "## 已达标、跳过（%d 个货号）" % len(nothings), "", "```",
           " ".join(sorted(nothings)) if nothings else "-", "```", ""]
    md += ["## 失败（%d 个货号）" % len(fails), "", "```",
           "\n".join("%s\t%s" % (k, v) for k, v in sorted(fails.items())) if fails else "-", "```", ""]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["货号", "结果", "恒信目标", "改后回读", "一致"])
        for c in sorted(writes):
            v = writes[c]
            w.writerow([c, "已写入",
                        json.dumps(v.get("want") or {}, ensure_ascii=False),
                        json.dumps(v.get("got") or {}, ensure_ascii=False),
                        "是" if v.get("match") else "否"])
        for c in sorted(nothings):
            w.writerow([c, "已达标", "", "", ""])
        for c, why in sorted(fails.items()):
            w.writerow([c, "失败:%s" % why, "", "", ""])

    print("写入 %d / 已达标 %d / 失败 %d" % (len(writes), len(nothings), len(fails)))
    print("→", OUT_MD)
    print("→", OUT_CSV)


if __name__ == "__main__":
    main()
