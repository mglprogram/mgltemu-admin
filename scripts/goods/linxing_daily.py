#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""linxing_daily.py — 「7天日均」直接照抄海外仓库存看板上显示的那个数。

用户 2026-11-11 定案：「每个款号尺码对应的日均直接抄过来」。

抄的是哪一个数
--------------
看板 `https://warehouse-probe.app.workbuddy.host/` 自己的说明原文：

    7天日均：来自人工台账「总库存表」的 `7天日均`（件/天），不再取两个海外仓的销量。
    台账存原始小数，页面按要求**四舍五入为整数显示**（鼠标悬停可看原始值）；
    合计**按小款号去重**后相加。

所以页面上看到的是 `12 / 20 / 24 / 16 / 8`，而快照 `const SNAP` 里存的是
`13.142857 / 27.714286 / …`。两边差的就是「四舍五入」这一步 —— 本模块照页面口径
（`round()` 到整数）抄，和用户眼睛看到的完全一致。

⚠ 别和九部/十部混用：九部（TTSA-9）、十部（warehouse-probe 两套口径）各有自己的
  7天日均，各自独立、互不覆盖（用户 2026-11-11 强调）。本模块**只**服务于
  「领星总库存」这一块。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

__all__ = ["probe_daily_merged", "daily_for", "by_wh_shop", "resolve_daily", "WH_ALIAS"]


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def probe_daily_merged(force: bool = True, log=print) -> dict:
    """`{款号-套系-尺码: 7天日均}` —— **照看板页面显示的口径**（四舍五入取整）。

    · 按小款号合并（跨货源/店铺/仓库），款级字段只取一次（看板说明：去重后相加）；
    · 取整用 Python 的 `round()`（banker's rounding 只在 .5 上有差别，台账小数位
      一般不是 .5，实测与看板显示逐行一致）；
    · 取不到返回空表（页面这一列显示 —，不影响库存本体）。
    """
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))
        import temu_stock_topup as T  # noqa: PLC0415

        snap = T.fetch_snapshot(T.load_cfg(), force=force, log=lambda *_: None)
        agg = T.aggregate_by_sku(snap.get("rows") or [])
        out: dict[str, float] = {}
        for k, v in agg.items():
            d = v.get("近7天日均")
            if d is not None:
                # 与看板页面同口径：原始小数四舍五入成整数
                out[str(k)] = int(round(_num(d)))
        log("[daily] 看板快照 %s，小款号 %d 个（已按页面口径取整）"
            % (snap.get("built_at") or "?", len(out)))
        return out
    except Exception as e:  # noqa: BLE001
        log("[daily] 看板 7天日均取不到（这列留空）：%s" % str(e)[:160])
        return {}


def daily_for(daily_map: dict, code, variant, size):
    """按 款号/套系/尺码 查日均；键与看板「小款号」一致（小写、按 - 连接）。"""
    if not daily_map:
        return None
    key = "-".join(str(x) for x in (code, variant, size) if x)
    if key in daily_map:
        return daily_map[key]
    # 看板小款号偶尔大小写不一致（4SetA vs 4SETA），做一次大小写兜底
    low = key.lower()
    for k, v in daily_map.items():
        if k.lower() == low:
            return v
    return None


def by_wh_shop(force: bool = True, log=print) -> dict:
    """看板逐仓逐店日均 `{款-套系-尺码: {仓名: {店铺: 日均}}}`（**核对用**）。"""
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))
        import temu_stock_topup as T  # noqa: PLC0415

        snap = T.fetch_snapshot(T.load_cfg(), force=force, log=lambda *_: None)
        out: dict[str, dict[str, dict[str, float]]] = {}
        for r in snap.get("rows") or []:
            key = str(r.get("小款号") or "").strip()
            if not key:
                continue
            wh = str(r.get("货源仓") or r.get("仓库名称") or "").strip()
            shop = str(r.get("店铺") or "").strip()
            v = r.get("近7天日均")
            if not wh or v is None:
                continue
            out.setdefault(key, {}).setdefault(wh, {})[shop] = _num(v)
        return out
    except Exception as e:  # noqa: BLE001
        log("[daily] 逐仓逐店日均取不到：%s" % str(e)[:160])
        return {}


#: 领星仓库名 → 看板仓名（两边的命名不是一套，必须显式映射）
WH_ALIAS = {
    "ZOO WAREHOUSE ⑥美西": "恒信·ZOO WAREHOUSE ⑥美西",
    "帕沃尔·加州3仓": "帕沃尔·加州3仓",
    "帕沃尔·kansas仓": "帕沃尔·kansas仓",
}


def resolve_daily(code, variant, size, wh: str, shop: str, merged: dict, detail: dict):
    """一条领星明细行的日均 = **该款的看板日均**（早先为拆到仓的近似，现统一为款级）。

    看板说明：`7天日均不拆仓库`（销量是小款号级、没有仓库维度）。
    所以一条明细行（= 一个仓）拿到的都是同一个数；页面按「按产品」合并后每个尺码
    只显示一次，与看板逐行完全一致。

    只有在看板找不到这个款时才返回 None（页面显示 —，不按 0 估算）。
    """
    key = "-".join(str(x) for x in (code, variant, size) if x)
    m = merged.get(key)
    if m is None:
        for k in merged:
            if k.lower() == key.lower():
                return merged[k]
        return None
    return m
