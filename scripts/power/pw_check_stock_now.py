# -*- coding: utf-8 -*-
"""查 `D144606YUNO` 现在的真实库存 —— 判断最后一单还能不能修。

只读：库存页 keywords 查 Fnsku，打印每个仓的代发库存。
"""
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_fix_with_wh as W  # noqa: E402
import pw_session as SS  # noqa: E402

for kw in ("D144606YUNO", "T3038-1B-L"):
    print("\n=== keywords=%s ===" % kw)
    try:
        pw = SS.PW()
        pw.login()
        rows = W.stock_candidates(pw, kw)
        if not rows:
            print("  查不到")
        for r in rows:
            flag = "  ← 有货" if r["stock_n"] > 0 else ""
            print("  %-14s %-18s %-10s 代发库存=%-5s%s"
                  % (r["fnsku"], r["seller"][:18], r["wh"], r["stock"], flag))
    except Exception as ex:  # noqa: BLE001
        print("  查询失败:", str(ex)[:80])
