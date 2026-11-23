# -*- coding: utf-8 -*-
"""重量来源：`data/sku_weight.json`（MSKU → 单件磅重），查不到就回落到基准值。

口径（2026-11-23）：
  · 表里有的 SKU ⇒ 包裹重量 = Σ(件数 × 单件重)
  · 表里没有的     ⇒ 回落 `weight_per_unit_lb × 总件数`（老口径），并**明确记日志**
    （不猜、不静默：日志里列出缺哪些 SKU，方便一次性补录）
  · 平台红字给的值（`weight_hint_lb`）优先级最高 —— 那是平台自己算的，最准
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = os.path.join(_HERE, "data", "sku_weight.json")
_CACHE: dict | None = None


def load(force: bool = False) -> dict:
    """`{MSKU(大写): 单件磅重float}`；文件不存在/解析失败 → {}。"""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    out = {}
    try:
        raw = json.load(open(TABLE, encoding="utf-8"))
        for k, v in (raw.get("weights") or raw or {}).items():
            if str(k).startswith("_"):
                continue
            try:
                out[str(k).strip().upper()] = float(v)
            except (TypeError, ValueError):
                continue
    except Exception:  # noqa: BLE001
        out = {}
    _CACHE = out
    return out


def save(weights: dict) -> str:
    """写回表（合并）。返回路径。"""
    cur = dict(load(force=True))
    for k, v in (weights or {}).items():
        try:
            cur[str(k).strip().upper()] = round(float(v), 3)
        except (TypeError, ValueError):
            continue
    os.makedirs(os.path.dirname(TABLE), exist_ok=True)
    with open(TABLE, "w", encoding="utf-8") as f:
        json.dump({"_说明": "MSKU → 单件磅重（lb）。买面单重量 = Σ(件数 × 单件重)；"
                            "缺的 SKU 会回落基准值并记日志。2026-11-23 建立。",
                   "_口径": "键 = 平台 SKU 编码（含店名前缀，如 BHY-204207-4SetA-XL）",
                   "weights": cur}, f, ensure_ascii=False, indent=1)
    global _CACHE
    _CACHE = cur
    return TABLE


def package_weight(items: list, base_lb: float = 1.5,
                   hint_lb: float | None = None) -> tuple:
    """算包裹重量 → `(磅, 依据, 缺失SKU列表)`。

    `items` = `[{"sku": "BHY-xxx", "qty": 1}, ...]`
    优先级：平台红字 `hint_lb` > 表内实重求和 > 基准×总件数（带缺失提示）
    """
    tbl = load()
    total_qty = 0
    known = 0.0
    missing = []
    for it in items or []:
        sku = str(it.get("sku") or "").strip().upper()
        qty = int(it.get("qty") or 1)
        total_qty += qty
        w = tbl.get(sku)
        if w is None:
            missing.append(sku)
        else:
            known += w * qty
    if hint_lb:
        return round(float(hint_lb), 2), "平台红字", missing
    if known > 0 and not missing:
        return round(known, 2), "表内实重", []
    if known > 0 and missing:
        # 部分缺：缺的按基准补，仍比全基准准
        return round(known + base_lb * sum(
            int(i.get("qty") or 1) for i in items
            if str(i.get("sku") or "").strip().upper() in missing), 2), "部分实重+基准", missing
    return round(base_lb * max(1, total_qty), 2), "基准×件数", missing
