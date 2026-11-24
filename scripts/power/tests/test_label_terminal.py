# -*- coding: utf-8 -*-
"""test_label_terminal.py — 终态错误分类的回归钉。

★ 2026-11-24 用户口径：
  · 「该订单为延长时效预售订单，…之后才可发货」⇒ **预售单，不能买面单**，命中即终态
  · 「发货订单重复」⇒ 已买过面单，命中即终态
  两者都**不许再换仓/换渠道/重试**（否则真实原因会被后续错误盖掉，白跑一轮）。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
if _POWER not in sys.path:
    sys.path.insert(0, _POWER)

import label_fail_kind as K  # noqa: E402


def test_presale_text_is_terminal():
    msg = ("该订单为延长时效预售订单，在途库存补货日期（2026-12-04 23:59:59 UTC+8）"
           "之后才可发货")
    assert K.classify_error(msg) == K.PRESALE
    assert K.is_terminal(msg) is True


def test_duplicate_is_terminal():
    for m in ("发货订单重复", "该单已下单", "duplicate order"):
        assert K.classify_error(m) == K.DUPLICATE
        assert K.is_terminal(m) is True


def test_normal_error_is_not_terminal():
    for m in ("系统异常，请稍后重试", "预约时间范围有误",
              "填写包裹重量与包裹内SKU总重量（2.20 磅）差异过大，请修改"):
        assert K.classify_error(m) == ""
        assert K.is_terminal(m) is False


def test_empty_safe():
    assert K.classify_error("") == ""
    assert K.is_terminal(None) is False
