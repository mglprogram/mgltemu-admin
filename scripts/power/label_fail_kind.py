# -*- coding: utf-8 -*-
"""买面单「无可用物流商」口径：单条失败不拖整批，统一汇总交人工。

★ 2026-11-23 用户贴图（批量在线下单弹窗）：
    「63 个包裹填充成功，以下 11 个失败 ／ 失败原因：**没有可用物流商及服务**」
  ⇒ 这是「该仓+该收件地一个可用渠道都没有」，与「有渠道但被校验拒」不同：
     · 不能重试同仓同渠道（必然复现）
     · 不许它把整批卡住；要单独进「失败订单」交人工（换仓 / 换地址 / 人工选渠道）

纯函数，方便单测；调用方把结果拼进失败表。
"""
from __future__ import annotations

#: 失败分类（写进失败表「失败原因」列）
NO_CHANNEL = "没有可用物流商及服务"
ALL_FILTERED = "有渠道但全部被过滤/无报价"
CHECK_FAIL = "校验未通过"


def classify(chan_rows: list, allowed_ok: bool) -> str:
    """给定「候选渠道」列表 → 失败原因字符串（空串＝有可用渠道）。

    `chan_rows` = provider 接口返回的 `online_channel_vo_list`
    `allowed_ok` = 过滤白名单+validate+报价>0 之后是否还剩至少一条
    """
    if allowed_ok:
        return ""
    if not chan_rows:
        return NO_CHANNEL
    return ALL_FILTERED


def is_retryable(reason: str) -> bool:
    """这个失败能不能靠「换渠道重试」解决。

    `NO_CHANNEL`（一个都没有）→ **不可重试**：同仓必然复现，只能换仓/换地址/人工。
    `ALL_FILTERED` → 可换仓试；同仓不行。
    """
    r = str(reason or "")
    if r == NO_CHANNEL:
        return False
    return True


def batch_summary(rows: list) -> dict:
    """整批结果汇总 → `{ok, fail, reasons: {原因: 单数}, ids}`。"""
    ok, fail, reasons, ids = 0, 0, {}, []
    for r in rows or []:
        if r.get("ok"):
            ok += 1
        else:
            fail += 1
            why = str(r.get("why") or r.get("reason") or "未分类")
            reasons[why] = reasons.get(why, 0) + 1
            ids.append(str(r.get("po") or ""))
    return {"ok": ok, "fail": fail, "reasons": reasons, "fail_ids": ids}
