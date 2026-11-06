# -*- coding: utf-8 -*-
"""oms_ship_verdict.py — 领星 OMS 发货**终态判据**（纯函数，可单测）。

用户口径（2026-11-06 原话，**不许改**）：
    「点击完确认并审核之后，你要去『处理中』查看这一单有没有转成处理中；
      否则你就要去『待获取平台面单』查看，如果在这个页面下，那就是下单失败了」

三个终态：
    IN_PROGRESS  —— 「处理中」里搜到这一单 ⇒ 发货已受理
    PLACE_FAILED —— 「处理中」没有，但「待获取平台面单」里有 ⇒ **下单失败**（面单没成）
    UNKNOWN      —— 两处都没有 / 页签读不清 ⇒ 如实报未知，**绝不当成功**

为什么单独抽成纯函数：这套判据以前被写成「接口返回 OK」「页签计数 +2」，
都是**间接信号**，会把失败报成成功（`导出计数变了 2` 这种根本没验证到这一单）。
判据只认「在目标页签里搜到这一单」。
"""
from __future__ import annotations

IN_PROGRESS = "in_progress"
PLACE_FAILED = "place_failed"
UNKNOWN = "unknown"


def judge(hit_doing: int, hit_pending: int, tab_doing_found: bool = True,
          tab_pending_found: bool = True) -> str:
    """判定终态（纯函数）。

    hit_doing / hit_pending: 各自页签里**搜到的行数**（0 = 没有）
    tab_doing_found / tab_pending_found: 页签本身是否找到（读不清就是 False）
    """
    if not tab_doing_found:
        return UNKNOWN
    if int(hit_doing or 0) > 0:
        return IN_PROGRESS
    if not tab_pending_found:
        return UNKNOWN
    if int(hit_pending or 0) > 0:
        return PLACE_FAILED
    return UNKNOWN


def exit_code(verdict: str) -> int:
    """终态 → 进程退出码：只有 IN_PROGRESS 算 0。"""
    return 0 if verdict == IN_PROGRESS else 42


def message(verdict: str, po: str) -> str:
    """终态 → 一句人能读的结论。"""
    if verdict == IN_PROGRESS:
        return "已在「处理中」：%s（发货已受理）" % po
    if verdict == PLACE_FAILED:
        return "落在「待获取平台面单」⇒ 下单失败：%s（面单没成，要回头看面单）" % po
    return "「处理中」与「待获取平台面单」都搜不到 %s ⇒ 状态未知，不许当成功" % po
