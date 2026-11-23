# -*- coding: utf-8 -*-
"""test_label_fail_kind.py — 买面单失败分类的回归钉。

★ 2026-11-23 用户贴图：「63 个包裹填充成功，以下 11 个失败 / 失败原因：**没有可用物流商及服务**」。
  这个失败**不能重试同仓同渠道**（必然复现），必须单独进失败表交人工（换仓/换地址/人工选渠道）。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
if _POWER not in sys.path:
    sys.path.insert(0, _POWER)

import label_fail_kind as K  # noqa: E402


def test_no_channel_when_empty():
    assert K.classify([], False) == K.NO_CHANNEL
    assert K.is_retryable(K.NO_CHANNEL) is False, "一个渠道都没有 ⇒ 换渠道重试必然复现"


def test_all_filtered_when_rows_exist():
    rows = [{"validate": False}, {"validate": False}]
    assert K.classify(rows, False) == K.ALL_FILTERED
    assert K.is_retryable(K.ALL_FILTERED) is True


def test_ok_when_any_allowed():
    assert K.classify([{"validate": True}], True) == ""


def test_batch_summary_counts_reasons():
    rows = [
        {"po": "A", "ok": True},
        {"po": "B", "ok": False, "why": K.NO_CHANNEL},
        {"po": "C", "ok": False, "why": K.NO_CHANNEL},
        {"po": "D", "ok": False, "why": K.CHECK_FAIL},
    ]
    s = K.batch_summary(rows)
    assert s["ok"] == 1 and s["fail"] == 3
    assert s["reasons"][K.NO_CHANNEL] == 2
    assert s["fail_ids"] == ["B", "C", "D"]
