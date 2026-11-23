# -*- coding: utf-8 -*-
"""「无可用物流商」时**默认换 Kansas 仓**，取第一个可用渠道。

★ 2026-11-23 用户令：
  「这种情况你就要换仓，**直接默认换成 Kansas 仓**，**选第一个物流**就可以了」
  —— 针对页面报「没有可用物流商及服务」的单（例 PO-211-01788556278390314，LEXINGTON KY 40505，
     加州三仓无线路）。

落地口径：
  ① 原仓（加州三仓/恒信加州3）查渠道 → 一条可用都没有 ⇒ **换 Kansas 仓重查一次**；
  ② Kansas 有渠道 ⇒ **取列表里的第一条可用渠道**（不按价格排序，用户口径「选第一个物流」）；
  ③ Kansas 也没有 ⇒ 进失败表「没有可用物流商及服务」，交人工（不改地址、不猜）。
"""
from __future__ import annotations

#: 换仓顺序：原仓失败后才用（用户 2026-11-23：默认 Kansas）
FALLBACK_WAREHOUSES = [("kansas", "Kansas")]

#: 换仓后选渠道的策略：`first` = 取第一条可用（用户口径），不是最便宜
PICK_AFTER_SWITCH = "first"


def next_warehouse(tried: list, available) -> str | None:
    """按换仓顺序挑下一个还没试过、且该店确实有这个仓的名字；没有则 None。

    `available` = 该店**买面单下拉**里的仓名集合（BHY=加州三仓/kansas/恒信加州3，
    BEL=加州3仓/kansas/恒信加州3仓）。名字必须精确命中，不许猜。

    ★ 真机值是小写 `kansas`（BHY/BEL 下拉都这样），所以这里返回**下拉真值**，
      而不是展示名 `Kansas`；展示名只在日志里用。
    """
    tried_set = {str(x) for x in (tried or [])}
    for key, ui_name in FALLBACK_WAREHOUSES:
        if ui_name in tried_set or key in tried_set:
            continue
        # 先按展示名精确命中，再按小写 key 命中（真机下拉值就是 kansas）
        for cand in (ui_name, key):
            if cand in (available or {}):
                return cand
    return None


def pick_channel(rows: list, after_switch: bool = False) -> dict | None:
    """选渠道。`after_switch=True` ⇒ 取**第一条**可用（用户口径）；否则按价格升序。"""
    ok = [r for r in (rows or [])
          if r.get("validate", True) and int((r.get("online_estimated_vo") or {})
                                             .get("charge_amount_si") or 0) > 0]
    if not ok:
        return None
    if after_switch:
        return ok[0]
    ok.sort(key=lambda r: int((r.get("online_estimated_vo") or {}).get("charge_amount_si") or 0))
    return ok[0]
