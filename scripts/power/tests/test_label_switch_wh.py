# -*- coding: utf-8 -*-
"""test_label_switch_wh.py — 「无可用物流商就换 Kansas，取第一个物流」的回归钉。

★ 2026-11-23 用户令：「这种情况你就要换仓，**直接默认换成 Kansas 仓**，
  **选第一个物流**就可以了」。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
if _POWER not in sys.path:
    sys.path.insert(0, _POWER)

import label_switch_wh as S  # noqa: E402


def _ch(name, fee, validate=True):
    return {"shipping_company_name": name, "validate": validate,
            "online_estimated_vo": {"charge_amount_si": fee}}


def test_switch_to_kansas_when_available():
    """买面单下拉里有 kansas（真机值是小写）⇒ 换它，返回**下拉真值**。"""
    assert S.next_warehouse(["加州三仓"], {"加州三仓", "kansas", "恒信加州3"}) == "kansas"
    # 下拉若写成展示名 Kansas，也认
    assert S.next_warehouse(["加州三仓"], {"加州三仓", "Kansas"}) == "Kansas"


def test_no_switch_if_kansas_already_tried():
    assert S.next_warehouse(["加州三仓", "Kansas"], {"kansas"}) is None
    assert S.next_warehouse(["加州三仓", "kansas"], {"kansas"}) is None


def test_no_switch_if_warehouse_not_in_dropdown():
    """下拉里没有 kansas（如 BAN 主体只有两条地址）⇒ 不换，别猜。"""
    assert S.next_warehouse(["加州三仓"], {"加州三仓", "恒信加州3仓"}) is None


def test_pick_first_after_switch():
    """换仓后取第一条可用（不看价格）。"""
    rows = [_ch("USPS", 900), _ch("GOFO", 300), _ch("SwiftX", 500)]
    got = S.pick_channel(rows, after_switch=True)
    assert got["shipping_company_name"] == "USPS", "用户口径：选第一个物流"


def test_pick_cheapest_without_switch():
    rows = [_ch("USPS", 900), _ch("GOFO", 300)]
    assert S.pick_channel(rows)["shipping_company_name"] == "GOFO"


def test_pick_first_skips_invalid_and_zero():
    rows = [_ch("BFE", 0), _ch("Pilot", 100, validate=False), _ch("SwiftX", 500)]
    got = S.pick_channel(rows, after_switch=True)
    assert got["shipping_company_name"] == "SwiftX"


def test_pick_none_when_all_invalid():
    assert S.pick_channel([_ch("X", 0), _ch("Y", 100, validate=False)]) is None
