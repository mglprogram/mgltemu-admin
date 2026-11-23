# -*- coding: utf-8 -*-
"""三店恒信/加州3/Kansas 同步结果汇总：台账 → 清单。

输入：_logs/api_hx_ca3.jsonl
输出：_logs/hx_ca3_result.md + .csv
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "_logs"


def main():
    rows = [json.loads(l) for l in (LOGS / "api_hx_ca3.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    ok, bad, nothing = [], [], []
    for r in rows:
        e = r.get("event")
        if e == "write":
            (ok if r.get("ok") else bad).append(r)
        elif e == "nothing":
            nothing.append(r)
    md = ["# 三店恒信 / 加州3 / Kansas 同步结果", "",
          "口径：恒信 = max(0, ⌈领星可用×80%⌉)；加州3仓 = max(0, ⌈帕沃尔可用×80%⌉)；"
          "Kansas = 0；恒信与加州3 两边都 0 的行不写（另出清单）。", "",
          "| 项 | 数量 |", "|---|---|",
          "| 写入成功（货号） | %d |" % len(ok),
          "| 写入失败 | %d |" % len(bad),
          "| 已达标跳过 | %d |" % len(nothing), ""]
    if bad:
        md += ["## 失败", "", "| 货号 | 时间 |", "|---|---|"]
        for r in bad:
            md.append("| %s | %s |" % (r.get("code"), r.get("ts")))
        md.append("")
    md += ["## 写入明细（按店）", ""]
    for st, pre in (("BHY", "BHY-"), ("BEL", "BEL-"), ("KLS", "BK")):
        sub = [r for r in ok if str(r.get("code", "")).startswith(pre)]
        if not sub:
            continue
        md += ["### %s（%d 个货号）" % (st, len(sub)), "", "| 货号 | 改动条数 |", "|---|---|"]
        for r in sub:
            md.append("| %s | %s |" % (r.get("code"), r.get("n")))
        md.append("")
    (LOGS / "hx_ca3_result.md").write_text("\n".join(md), encoding="utf-8")

    with open(LOGS / "hx_ca3_result.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["货号", "结果", "改动条数", "时间"])
        for r in ok:
            w.writerow([r.get("code"), "成功", r.get("n"), r.get("ts")])
        for r in bad:
            w.writerow([r.get("code"), "失败", "", r.get("ts")])
        for r in nothing:
            w.writerow([r.get("code"), "已达标", 0, r.get("ts")])
    print("成功 %d / 失败 %d / 已达标 %d" % (len(ok), len(bad), len(nothing)))
    print("→", LOGS / "hx_ca3_result.md")


if __name__ == "__main__":
    main()
