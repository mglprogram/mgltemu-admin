# -*- coding: utf-8 -*-
"""test_split_presale_rules.py — D 组（预售 sheet）收录口径的回归钉。

2026-11-18 用户令：「你只要把 **204207-4SetA-XXL** 的订单加入到预售 sheet 就行」。
这条口径是**按尺码**的：同款 204207-4SetA 的 XL / L 等尺码照常走 A/B/C，
只有 XXL 进 D 组 —— 一个款挂上预售就整款算预售，会把有现货的尺码也扣住不发。

历史口径（保持）：YOGA-4SetA **全尺码**进 D；店必须属于 BHY / BEL；别的店一律不认。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_SCRIPTS = os.path.dirname(_POWER)
for p in (_POWER, os.path.join(_POWER, "lib"), _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import bel_split_by_stock as S  # noqa: E402


def test_204207_xl_and_xxl_are_presale():
    """2026-11-18 最终口径：「已开预售：204207-4SetA-XL 和 XXL」。"""
    for size in ("XL", "XXL"):
        assert S.is_presale("BHY-204207-4SetA-%s" % size, "BHY"), size
        assert S.is_presale("BEL-204207-4SetA-%s" % size, "BEL"), size
    # 同款别的尺码不许进 D（L/M/S）
    for size in ("L", "M", "S"):
        assert not S.is_presale("BHY-204207-4SetA-%s" % size, "BHY"), size


def test_103902_2b_xl_is_presale():
    """2026-11-18 二次补充：103902-2B 的 XL 也进预售 sheet。"""
    assert S.is_presale("BHY-103902-2B-XL", "BHY")
    assert S.is_presale("BEL-103902-2B-XL", "BEL")
    for size in ("L", "M", "S", "XXL"):
        assert not S.is_presale("BHY-103902-2B-%s" % size, "BHY"), size
    # 别把其它变体（4B / 8B / 4SetA / 2B1.1）连坐
    for sku in ("BHY-103902-4B-XL", "BHY-103902-8B-XL", "BHY-103902-4SetA-XL",
                "BD-H103903-2B1.1-XL"):
        assert not S.is_presale(sku, "BHY"), sku


def test_yoga_only_xxl_is_presale():
    """2026-11-18 最终口径：「YOGA 现在只剩 YOGA-XXL 在开预售」。"""
    assert S.is_presale("BHY-YOGA-4SetA-XXL", "BHY")
    for size in ("S", "M", "L", "XL"):
        assert not S.is_presale("BHY-YOGA-4SetA-%s" % size, "BHY"), size


def test_207213_l_xl_xxl_are_presale():
    """2026-11-18 补充：「temu 店铺 207213 的 L / XL / XXL 三个尺码已开预售」。"""
    for size in ("L", "XL", "XXL"):
        assert S.is_presale("BHY-207213-4SetA-%s" % size, "BHY"), size
        assert S.is_presale("BEL-207213-4SetA-%s" % size, "BEL"), size
    for size in ("S", "M"):
        assert not S.is_presale("BHY-207213-4SetA-%s" % size, "BHY"), size
    # 库存侧品牌前缀（BH-/BS-/BS2-）同样命中 L
    for sku in ("BS2-207213-4SetA-L", "BH-207213-4SetA-L", "207213-4SetA-L"):
        assert S.is_presale(sku, "BEL"), sku


def test_presale_only_for_temu_half_managed_stores():
    """只有 BHY / BEL 认 D 组；SHEIN 店（BS/BD/BAN/BE）与陌生店一律 False。"""
    for store in ("BS", "BD", "BAN", "BE", "KinglamanSport", ""):
        assert not S.is_presale("BHY-204207-4SetA-XXL", store), store


def test_brand_prefixes_do_not_break_matching():
    """库存侧/别店可能写成 BS2-/BH-/BK- 前缀，去前缀后仍要命中（用 XXL 尺码验）。"""
    for sku in ("BS2-204207-4SetA-XXL", "BH-204207-4SetA-XXL", "204207-4SetA-XXL"):
        assert S.is_presale(sku, "BEL"), sku


def test_desc_mentions_all_rules():
    d = S.presale_desc()
    for token in ("YOGA-4SETA", "XXL", "204207-4SETA", "XL", "103902-2B",
                  "207213-4SETA", "L"):
        assert token in d, (token, d)
