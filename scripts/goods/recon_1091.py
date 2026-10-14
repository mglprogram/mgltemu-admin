# -*- coding: utf-8 -*-
"""对齐 1091-1K：看板（实时/缓存）逐仓逐尺码 vs 面板 goods.json 的判定值。

用法：python scripts/goods/recon_1091.py
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
print("看板缓存 TTL:", (cfg.get("看板") or {}).get("缓存秒"))

for force in (False, True):
    snap = T.fetch_snapshot(cfg, force=force, log=lambda m: None)
    rows = snap.get("rows") or []
    print("\n=== force=%s  built_at=%s  行数=%d 缓存=%s"
          % (force, snap.get("built_at"), len(rows), snap.get("_from_cache")))

    # 1091-1K 的逐仓原始行（全部尺码）
    hit = [r for r in rows if str(r.get("小款号") or "").startswith("1091-1K")]
    print("  1091-1K 原始行数:", len(hit))
    for r in hit:
        print("   %-16s 货源=%-6s 仓=%-24s 代发库存=%-6s 途中=%-6s 日均=%-8s 店铺=%s"
              % (r.get("小款号"), r.get("货源"), r.get("仓库名称"),
                 r.get("代发库存"), r.get("代发途中"), r.get("近7天日均"), r.get("店铺")))

    agg = T.aggregate_by_sku(rows)
    for key in ("1091-1K-S", "1091-1K-M", "1091-1K-L", "1091-1K-XL", "1091-1K-XXL"):
        a = agg.get(key)
        if not a:
            print("  agg[%s] = 无" % key)
            continue
        print("  agg[%-11s] 代发库存=%-5s 途中=%-5s 日均=%-8s 按仓=%s"
              % (key, a.get("代发库存"), a.get("代发途中"), a.get("近7天日均"),
                 json.dumps(a.get("按仓"), ensure_ascii=False)))

# 面板侧
goods = json.loads((ROOT / "temu-admin/public/data/goods.json").read_text(encoding="utf-8"))
print("\n=== 面板 goods.json（restockGeneratedAt=%s）" % goods.get("restockGeneratedAt"))
print("  看板快照时间:", (goods["lowStock"]["restock"]["sources"]["看板"] or {}).get("builtAt"))
rows = [r for r in goods["lowStock"]["rows"] if r["code"] == "BHY-1091-1K"]
for r in rows:
    print("  %-14s %-5s TEMU=%s 仓内=%-6s 源=%-12s 结论=%-12s 建议加=%s 明细=%s"
          % (r["code"], r["size"], r["stock"], r.get("whStockRaw"), r.get("stockSource"),
             r.get("verdict"), r.get("suggestAdd"),
             json.dumps(r.get("whByWarehouse"), ensure_ascii=False)))
    print("      boardKey=%s factor=%s 日均=%s(%s) 需求=%s 加后=%s 在途=%s"
          % (r.get("boardKey"), r.get("factor"), r.get("dailyUse"), r.get("dailySource"),
             r.get("needUnits"), r.get("afterStock"), r.get("transitRaw")))

# 用同一份看板 + 飞书，现场重算一遍 judge_row，看与落盘值是否一致
print("\n=== 现场重算 judge_row（同一份 agg）===")
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
import enrich_restock as E  # noqa: E402

cfg_e = E.load_cfg()
cfg_t = T.load_cfg()
snap = T.fetch_snapshot(cfg_t, force=False, log=lambda m: None)
agg2 = T.aggregate_by_sku(snap.get("rows") or [])
fs = E.load_feishu(cfg_e, offline=True)
for r in rows:
    d = E.judge_row({k: r[k] for k in ("skuCode", "code", "stock", "size", "store")},
                    agg2, fs, cfg_e, cfg_t)
    print("  %-14s %-3s 仓内=%-6s 源=%-12s 日均=%-9s 需求=%-8s 建议加=%-5s 结论=%s"
          % (r["code"], r["size"], d.get("whStockRaw"), d.get("stockSource"),
             d.get("dailyUse"), d.get("needUnits"), d.get("suggestAdd"), d.get("verdict")))
    print("      手算: need=日均*30=%s  →  need-仓内-TEMU = %s"
          % (round((d.get("dailyUse") or 0) * 30, 2),
             round((d.get("dailyUse") or 0) * 30 - (d.get("whStockRaw") or 0)
                   - (r["stock"] or 0), 2)))

