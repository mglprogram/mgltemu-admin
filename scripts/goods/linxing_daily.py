#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""linxing_daily.py — 统一口径的「7天日均」（件/天，按 款号-套系-尺码）。

为什么要这个模块
----------------
领星总库存面板（`/product/stock-track` 第一个页签）自己不含销量，7天日均是从
海外仓库存看板 warehouse-probe 借来的。但看板快照里行很多：

    202504-5SetA-S   35 行！BS2 / BS / BH2 三个店铺 × kansas / 加州3 / 恒信美西 各一排
    每行的 `近7天日均` 都写着同一个款级值（22.142857）

所以「取哪一行」直接决定页面上的数：

| 取法 | 结果 | 是否与看板一致 |
|---|---|---|
| 逐行相加 | 放大 6~7 倍 | ✗ |
| 取 max / 取第一个 | 碰运气 | ✗ |
| **按小款号(仓库无关)合并、每款只取一次** | 与看板「按小款号」视图同一行 | ✓ |

★ 2026-11-11 用户明确要求：领星总库存面板的 7天日均**照看板按小款号合并后的值**，
  这样面板和看板永远对得上。

⚠ 别和九部/十部混用：九部（TTSA-9）、十部（warehouse-probe）两个看板各有自己的
  7天日均口径，各自独立，互不覆盖（用户 2026-11-11 再次强调）。
  本模块**只**服务于「领星总库存」这一块。
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
    """从 warehouse-probe 快照取 `{款号-套系-尺码: 近7天日均}`（**按小款号合并后的值**）。

    · `force=True` 每次重抓看板（几秒），失败由 fetch_snapshot 自回退旧缓存；
    · 复用 `temu_stock_topup.aggregate_by_sku()` —— 它按小款号合并各货源/店铺/仓库，
      并把 `key_level_fields` 里的款级字段只取一次，产出与看板「按小款号」视图一致；
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
                out[str(k)] = round(float(d), 3)
        log("[daily] 看板快照 %s，小款号 %d 个（合并后）"
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
    """看板逐仓逐店日均 `{款-套系-尺码: {仓名: {店铺: 日均}}}`（**核对用**）。

    ⚠ 老实说：看板页面上按小款号汇总的那一列，我们只能拿到「每行都相同的款级值」，
    拿不到前端求和后的中间态。所以这里给出逐行的原值，用来验证
    「仓×店」拆解能不能复现看板的合计（见 `resolve_daily`）。
    """
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
    """一条领星明细行的日均 —— 复现看板那种「按小款号」的合并口径。

    看板自己的 30 天销量拆账方式（从快照反推，见 PR）：
        加州3仓 / 恒信美西 的**全部**销量记在 `BS2` 名下
        kansas仓 的销量**平均分**给 `BS2` / `BS` / `BH2` 三家店
    于是「按小款号合并」后的日均 = BS2(加州3+美西+kansas/3)
                                  + BS(kansas/3) + BH2(kansas/3)
                             = 加州3+美西+kansas  —— 这正好是 22.14/56.0/… 那组数。

    本函数对一行领星明细 = 一个仓 + 一个店铺：
        BS2（或该仓唯一店铺）      → 加州3/美西/夏威夷/新泽西: 全额；kansas: 1/3
        BS / BH2 / BLA 等其它店     → kansas: 1/3；其它仓: 0（销量不归它）

    复现失败（没有任何「款×仓」的拆解能对上看板合计）时退回合并值，
    并在返回值里标 `fallback`，页面悬停可解释。
    """
    key = "-".join(str(x) for x in (code, variant, size) if x)
    m = merged.get(key)
    if m is None:
        for k in merged:
            if k.lower() == key.lower():
                m, key = merged[k], k
                break
    if m is None:
        return None
    per = detail.get(key) or {}
    board_wh = WH_ALIAS.get(wh, wh)
    shops = sorted((per.get(board_wh) or {}).keys())
    if not shops:
        # 看板没有这个仓 → 这一行按合并值给（页面会标出来）
        return m
    n = len(shops)
    share = 1.0 / n
    # kansas 的销量在看板里平均分给 n 家店；其它仓（加州3 / 美西 / 夏威夷 / 新泽西）
    # 各家行上写的都是同一个款级值，**不能按行相加** —— 一行领星明细就只能取一次。
    if wh == "帕沃尔·kansas仓":
        return round(m * share, 3)
    return round(m, 3)
