# -*- coding: utf-8 -*-
"""侦察：看板里「加州3仓」的逐 SKU 可用量（加库存要只加这一个仓）。

用法：python scripts/goods/recon_kansas.py [款号关键字]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\AI-workplace\TEMU")
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))
import temu_stock_topup as T  # noqa: E402

cfg = T.load_cfg()
snap = T.fetch_snapshot(cfg, force=False, log=lambda m: None)
rows = snap.get("rows") or []
print("看板 built_at:", snap.get("built_at"), "行数:", len(rows))

# 仓库名清单
from collections import Counter
wh = Counter((r.get("仓库名称") or "") for r in rows)
print("\n仓库清单：")
for k, v in wh.most_common():
    print("   %-32s %d 行" % (k, v))

kw = sys.argv[1] if len(sys.argv) > 1 else ""
target = "加州"
print("\n含「%s」的仓库行样例：%s" % (target,
      [r.get("小款号") for r in rows if target in (r.get("仓库名称") or "")][:5]))

if kw:
    print("\n=== 款号含 %s 的逐仓明细 ===" % kw)
    for r in rows:
        if kw in (r.get("小款号") or ""):
            print("  %-18s 货源=%-6s 仓=%-26s 代发库存=%-6s 途中=%-6s 日均=%-8s"
                  % (r.get("小款号"), r.get("货源"), r.get("仓库名称"),
                     r.get("代发库存"), r.get("代发途中"), r.get("近7天日均")))

# 统计：加州仓占全部现货的比例
ca = sum(float(r.get("代发库存") or 0) for r in rows if target in (r.get("仓库名称") or ""))
allx = sum(float(r.get("代发库存") or 0) for r in rows)
print("\n加州仓现货合计 %s / 全部 %s（%.1f%%）" % (int(ca), int(allx), ca / allx * 100 if allx else 0))
