# -*- coding: utf-8 -*-
"""列出 BEL / BHY 在售款中，**只有新泽西 + Kansas 有现货、加州仓为 0** 的货号。

口径（用户 2026-11-17）：
  · 「在售款」= 面板 goods.json 里两店 items 的货号（去掉店前缀后比对）
  · 逐仓现货取海外仓看板的快照（`仓库名称` × `代发库存`），按「小款号」聚合
  · 只看 **帕沃尔** 仓库（kansas / 加州3 / 新泽西2），恒信仓不参与判断
  · 命中条件：加州3 合计 = 0 且 (kansas + 新泽西2) 合计 > 0

用法：
    python scripts/goods/which_warehouse.py                # 拉最新看板
    python scripts/goods/which_warehouse.py --cache        # 用本地快照
    python scripts/goods/which_warehouse.py --out x.xlsx
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\AI-workplace\TEMU")
GOODS = ROOT / "temu-admin" / "public" / "data" / "goods.json"
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

WH_CA = "加州3"
WH_KS = "kansas"
WH_NJ = "新泽西"


def load_goods() -> dict:
    return json.loads(GOODS.read_text(encoding="utf-8"))


def bare(code: str, store: str) -> str:
    c = (code or "").strip()
    if c.upper().startswith(store.upper() + "-"):
        c = c[len(store) + 1:]
    return re.sub(r"^(ERROR|TEST|X)-", "", c, flags=re.I)


def load_snapshot(use_cache: bool) -> dict:
    """返回 {小款号(去尺码): {加州: n, kansas: n, 新泽西: n, 其它: n}}。"""
    if use_cache:
        p = ROOT / "scripts" / "power" / "out" / "probe_snap_cache.json"
        snap = json.loads(p.read_text(encoding="utf-8"))
        print("用本地快照 built_at=%s" % snap.get("built_at"))
    else:
        import temu_stock_topup as T
        cfg = {"看板": {"url": "https://warehouse-probe.app.workbuddy.host/",
                        "缓存秒": 0}}
        snap = T.fetch_snapshot(cfg, force=True)
        print("拉最新看板 built_at=%s rows=%d"
              % (snap.get("built_at"), len(snap.get("rows") or [])))

    agg: dict[str, dict] = defaultdict(lambda: {"加州": 0, "kansas": 0, "新泽西": 0,
                                                "其它": 0, "all": 0})
    for r in snap.get("rows") or []:
        if (r.get("货源") or "") != "帕沃尔":
            continue
        key = str(r.get("小款号") or "").strip()
        if not key:
            continue
        # 小款号带尺码后缀（102503-5SetB-L）→ 去掉尺码，只认纯尺码词
        # ⚠ 不要用 rsplit("-",1)：会把 `102104-5SetA` 这类款号也切掉。
        style = re.sub(r"-(XS|S|M|L|XL|XXL|XXXL|3XL|4XL|5XL|[0-9]XL)$", "", key)
        wh = str(r.get("仓库名称") or "")
        qty = r.get("代发库存") or 0
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            qty = 0
        a = agg[style]
        a["all"] += qty
        if WH_CA in wh:
            a["加州"] += qty
        elif WH_KS in wh:
            a["kansas"] += qty
        elif WH_NJ in wh:
            a["新泽西"] += qty
        else:
            a["其它"] += qty
    return dict(agg), snap.get("built_at")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", action="store_true", help="用本地快照，不重新拉")
    ap.add_argument("--out", default="", help="可选：导出 xlsx")
    ap.add_argument("--include-zero-nj", action="store_true",
                    help="也列出「只有 kansas、新泽西也为 0」的款")
    a = ap.parse_args()

    goods = load_goods()
    agg, built = load_snapshot(a.cache)

    result = {}
    for store, s in (goods.get("stores") or {}).items():
        hits, miss = [], 0
        for it in s.get("items") or []:
            code = bare(it.get("code") or "", store)
            w = agg.get(code)
            if not w:
                miss += 1
                continue
            ca, ks, nj = w["加州"], w["kansas"], w["新泽西"]
            cond = (ca == 0 and (ks + nj) > 0)
            if not a.include_zero_nj:
                cond = cond and nj > 0
            if cond:
                hits.append({
                    "store": store, "code": it.get("code"), "spuId": it.get("spuId"),
                    "title": (it.get("title") or "")[:60],
                    "kansas": ks, "新泽西": nj, "合计": ks + nj,
                })
        hits.sort(key=lambda x: -x["合计"])
        result[store] = hits
        print("\n=== %s：命中 %d 个款（看板里对不上 %d 个）===" % (store, len(hits), miss))
        for h in hits:
            print("  %-24s kansas=%-6s 新泽西=%-6s 合计=%s"
                  % (h["code"], h["kansas"], h["新泽西"], h["合计"]))

    if a.out:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "只有新泽西+Kansas"
        ws.append(["店铺", "货号", "SPU ID", "Kansas", "新泽西", "合计", "商品标题"])
        for store, hits in result.items():
            for h in hits:
                ws.append([store, h["code"], h["spuId"], h["kansas"], h["新泽西"],
                           h["合计"], h["title"]])
        wb.save(a.out)
        print("\n已导出:", a.out)
    print("\n看板快照:", built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
