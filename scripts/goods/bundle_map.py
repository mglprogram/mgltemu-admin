# -*- coding: utf-8 -*-
"""组合套装 / 可替代货号映射库（用户 2026-11-23 口述，逐条落地）。

用途：某些 TEMU 货号在领星 / 帕沃尔里没有同名 SKU（算出来是 0），
      需要用「组合/替代」关系把可用量换算过来，否则会被误判成无货。

结构（_logs/套装映射.json）：
    {"rules": [
        {"key": "102105-9SetA", "kind": "compose",
         "parts": [{"key": "102105-3BGR", "mult": 3}], "note": "..."},
        {"key": "103910-1B", "kind": "size_codes",
         "note": "数字码 1B/2B 这类：1=S 2=M 3=L 4=XL 5=XXL", "sizes": ["S","M","L","XL","XXL"]},
        ...
    ]}

`sizes` 规则说明：像 `103910-1B` / `103912-4B` 这种尾部是数字码的货号，
数字依次对应 S/M/L/XL/XXL（用户原话），换算时按同一位置取量。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "_logs"

#: 用户 2026-11-23 给的这一批规则**只属于 BHY 店**（原话：「这个只是BHY店铺的数据」）。
#: 其余店（BEL / KLS）暂不套用，等各自的关系数据。
STORE_RULES = {"BHY": None}   # None = 下面这份 RULES 全部

RULES = [
    {"key": "102105-9SetA", "kind": "compose",
     "parts": [{"key": "102105-3BGR", "mult": 3}],
     "note": "102105-9SetA = 102105-3BGR × 3"},
    {"key": "103901-10SetB", "kind": "compose",
     "parts": [{"key": "103901-5SetC", "mult": 2}],
     "note": "103901-10SetB = 103901-5SetC × 2"},
    {"key": "103910-1B", "kind": "size_codes",
     "note": "103910-1B：数字码依次对应 S/M/L/XL/XXL"},
    {"key": "103910-2B", "kind": "compose",
     "parts": [{"key": "103910-1B", "mult": 2}],
     "note": "103910-2B = 103910-1B × 2"},
    {"key": "103910-2B1", "kind": "compose",
     "parts": [{"key": "103910-1B", "mult": 2}],
     "note": "103910-2B1 = 103910-1B × 2"},
    {"key": "103912-2B", "kind": "compose",
     "parts": [{"key": "103912-1B", "mult": 2}],
     "note": "103912-2B = 103912-1B × 2"},
    {"key": "103912-4B", "kind": "size_codes",
     "note": "103912-4B：数字码依次对应 S/M/L/XL/XXL"},
    {"key": "1040-5SetA1", "kind": "alias",
     "alias": ["R1040", "N1040", "1040-5SetA"],
     "note": "1040-5SetA1 ⇄ R1040 / N1040 / 1040-5SetA"},
    {"key": "104507-3B", "kind": "alias",
     "alias": ["104504-3B"], "note": "104507-3B ⇄ 104504-3B"},
    {"key": "104507-3BHG", "kind": "alias",
     "alias": ["104504-3BHG"], "note": "104507-3BHG ⇄ 104504-3BHG"},
    # 2026-11-23 用户修正：BHY 的 202304-6SetA 只由 202304-3BHW×2 组成（去掉 3BHP）
    {"key": "202304-6SetA", "kind": "compose",
     "parts": [{"key": "202304-3BHW", "mult": 2}],
     "note": "202304-6SetA = 202304-3BHW × 2"},
    {"key": "203804-4SetA", "kind": "alias",
     "alias": ["203804R-4SetA"], "note": "203804-4SetA ⇄ 203804R-4SetA"},
    {"key": "204203-4SetA1", "kind": "alias",
     "alias": ["204203-4SetA", "204203-4SetC"],
     "note": "204203-4SetA1 ⇄ 204203-4SetA / 4SetC"},
    {"key": "207202-10SetB", "kind": "compose",
     "parts": [{"key": "207202-5SetB", "mult": 1}, {"key": "207202-5SetA", "mult": 1}],
     "note": "207202-10SetB = 207202-5SetB + 207202-5SetA"},
    {"key": "H103902-4B", "kind": "compose",
     "parts": [{"key": "H103902-2B", "mult": 2}],
     "note": "H103902-4B = H103902-2B × 2"},
    {"key": "MaternitySets-4SetB", "kind": "compose",
     "parts": [{"key": "207220-4SetA", "mult": 1}, {"key": "203802-4SetA", "mult": 1}],
     "note": "MaternitySets-4SetB = 207220-4SetA×1 + 203802-4SetA×1"},
    {"key": "N204003-4B", "kind": "compose",
     "parts": [{"key": "N204003-2B", "mult": 2}],
     "note": "N204003-4B = N204003-2B × 2"},
]


def main():
    """按店输出：每店一个映射文件（这批规则只给 BHY）。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for store, rules in STORE_RULES.items():
        rs = RULES if rules is None else rules
        p = OUT_DIR / ("套装映射_%s.json" % store)
        p.write_text(json.dumps({"generatedAt": "2026-11-23", "store": store, "rules": rs},
                                ensure_ascii=False, indent=1), encoding="utf-8")
        print("[%s] 规则 %d 条 → %s" % (store, len(rs), p))
        for r in rs:
            print("  %-22s %-10s %s" % (r["key"], r["kind"], r["note"]))
    # 兼容旧路径：保持一份全量副本，供排查用（store_db 不再读它）
    (OUT_DIR / "套装映射.json").write_text(
        json.dumps({"generatedAt": "2026-11-23", "note": "全量副本，仅供参考；实际按店取 套装映射_<店>.json",
                    "rules": RULES}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
