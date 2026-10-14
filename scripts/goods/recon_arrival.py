# -*- coding: utf-8 -*-
"""核对「最近到货」是否来自飞书到货计划甘特（用户给的 base）。

用户 2026-10-12 截图那条：SKU-型号 1026-5SetA · 到货日 2026-11-01 · 数量 100 · 尺码明细 M:100
  → 期望面板里 `BEL-1026-5SetA` 的 M 尺码「最近到货」= 2026-11-01，且能列出该批次。

用法：python scripts/goods/recon_arrival.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\AI-workplace\TEMU")
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))
import enrich_restock as E  # noqa: E402

cfg = E.load_cfg()
fs = E.load_feishu(cfg, offline=False)

print("飞书 base:", (cfg.get("飞书") or {}).get("base"))
print("到货计划键数:", len(fs.get("到货计划") or {}), "| fetched_at:", fs.get("fetched_at"))
print()
print("=== 用户截图那条（型号 1026-5SetA）===")
for key in ("1026-5SetA", "1026-5SetA-M", "1026-5SetA-S", "1026-5SetA-XL"):
    batches = (fs.get("到货计划") or {}).get(key)
    if batches is None:
        print("  %-14s 无" % key)
        continue
    for b in batches:
        print("  %-14s 到货日=%-11s 件数=%-5s 尺码件数=%-6s 入仓=%-5s 批次=%-8s 渠道=%-6s 单号=%s"
              % (key, b.get("到货日"), b.get("件数"), b.get("尺码件数"),
                 b.get("是否入仓"), b.get("批次"), b.get("渠道"), b.get("入库单号")))
print()
print("=== 同型号在型号级（未拆尺码）的记录 ===")
for k, v in (fs.get("到货计划") or {}).items():
    if k.startswith("1026-5SetA"):
        continue
    if "1026-5SetA" in k:
        print(" ", k, v)

print()
print("=== 面板侧：BEL-1026-5SetA 各尺码的到货 ===")
goods = json.loads((ROOT / "temu-admin/public/data/goods.json").read_text(encoding="utf-8"))
rows = [r for r in goods["lowStock"]["rows"] if r["code"] == "BEL-1026-5SetA"]
if not rows:
    print("  （该款不在低库存清单里）")
for r in rows:
    print("  %-3s TEMU=%-3s 仓内=%-5s 最近到货=%-11s 在途=%-6s 货=%s 建议加=%s"
          % (r["size"], r["stock"], r.get("whStockRaw"), r.get("arrivalDate") or "—",
             r.get("transitRaw"), r.get("verdict"), r.get("suggestAdd")))
    for b in r.get("arrivalBatches") or []:
        print("        批次 %s · %s 件 · 到货 %s · %s · %s"
              % (b.get("批次"), b.get("件数"), b.get("到货日"), b.get("渠道"),
                 b.get("入库单号")))
