# -*- coding: utf-8 -*-
"""test_weight_hint.py — 「先填基准值，平台报差异过大（N 磅）就改成 N」的回归钉。

★ 2026-11-23 用户令：这正是点击版 `weight_fix()` 的口径（代码注释里写着
  「按提醒的数字磅数填上去」）。接口版沿用同一条：报错文本里抠出 N，用 N 覆盖基准。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
if _POWER not in sys.path:
    sys.path.insert(0, _POWER)

import temu_label_dryrun as D  # noqa: E402


def test_fullwidth_parens():
    """平台原文用的全角括号「（1.76 磅）」。"""
    assert D.pick_weight_hint("填写包裹重量与包裹内SKU总重量（1.76 磅）差异过大，请修改") == 1.76


def test_halfwidth_parens():
    assert D.pick_weight_hint("差异过大(3.48 lb)") is None, "英文 lb 不认（平台用磅）"
    assert D.pick_weight_hint("（3.48 磅）") == 3.48


def test_integer_weight():
    assert D.pick_weight_hint("（2 磅）") == 2.0


def test_no_hint_returns_none():
    assert D.pick_weight_hint("预约时间范围有误") is None
    assert D.pick_weight_hint("") is None
    assert D.pick_weight_hint(None) is None


def test_picks_first_number_only():
    s = "包裹重量（1.76 磅）与（0.5 磅）不一致"
    assert D.pick_weight_hint(s) == 1.76
