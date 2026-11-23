# -*- coding: utf-8 -*-
"""把三个店的「待确认清零」清单合成一份 markdown（人工确认用）。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "_logs"


def main():
    out = ["# 待确认清零清单（恒信与加州3 两边都为 0）", "",
           "口径（2026-11-23）：", "",
           "- 恒信仓 = max(0, ⌈领星可用×80%⌉)",
           "- 加州3仓 = max(0, ⌈帕沃尔加州3可用×80%⌉)",
           "- Kansas = 0（直接清零，无需确认）",
           "- **恒信与加州3 都算出 0** 的（货号-尺码）先不执行，等确认", ""]
    total = 0
    for st in ("BHY", "BEL", "KLS"):
        p = LOGS / ("%s_zero_confirm.json" % st.lower())
        if not p.exists():
            continue
        items = json.loads(p.read_text(encoding="utf-8"))
        total += len(items)
        out.append("## %s（%d 条）" % (st, len(items)))
        out.append("")
        out.append("| 货号 | 尺码 | SPU | 领星可用 | 帕沃尔可用 |")
        out.append("|---|---|---|---|---|")
        for x in items:
            out.append("| %s | %s | %s | %s | %s |" % (
                x["code"], x["size"], x["spu"], x["lx"], x["pw"]))
        out.append("")
    out.insert(6, "合计 **%d** 条。" % total)
    out.insert(7, "")
    (LOGS / "zero_confirm_all.md").write_text("\n".join(out), encoding="utf-8")
    print("合计", total, "条 →", LOGS / "zero_confirm_all.md")


if __name__ == "__main__":
    main()
