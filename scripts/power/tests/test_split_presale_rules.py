# -*- coding: utf-8 -*-
"""test_split_presale_rules.py — D 组（预售 sheet）收录口径的回归钉。

★ 2026-11-19 用户令（口径换代，**以此为准**）：
「发货前去 https://mgltemu.us.ci/#/product/stock-track 查看哪些款号-货号已开预售，
 然后把这些订单在拆仓时，就直接收录在预售 sheet 和 15天预售 sheet」。

⇒ 预售判据从「代码里手写名单」改成 **读面板**（`presale-watch.json` 的 TEMU「预售中」行），
手写名单（`PRESALE_SIZE_KEYS`）降级为补录/兜底。本文件两层都钉：
  ① 面板命中（含别的店，如 KinglamanSport）；
  ② 面板读不到 → 退回手写名单（2026-11-18 那套：204207-4SetA XL/XXL、207213-4SetA L/XL/XXL…）。
"""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_SCRIPTS = os.path.dirname(_POWER)
for p in (_POWER, os.path.join(_POWER, "lib"), _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import bel_split_by_stock as S  # noqa: E402


@pytest.fixture(autouse=True)
def _no_panel(monkeypatch):
    """默认把面板缓存置成「{}」= 面板读不到 ⇒ 每个用例只验手写名单这条腿，不碰网络/本地文件。"""
    monkeypatch.setattr(S, "PANEL_CACHE", {}, raising=True)
    monkeypatch.setattr(S, "PANEL_META", {"source": "测试", "generatedAt": ""}, raising=True)


def _panel(d):
    S.PANEL_CACHE = d


# ---------------------------------------------------------------- ② 手写名单（兜底腿）
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


def test_brand_prefixes_do_not_break_matching():
    """库存侧/别店可能写成 BS2-/BH-/BK- 前缀，去前缀后仍要命中（用 XXL 尺码验）。"""
    for sku in ("BS2-204207-4SetA-XXL", "BH-204207-4SetA-XXL", "204207-4SetA-XXL"):
        assert S.is_presale(sku, "BEL"), sku


# ---------------------------------------------------------------- ① 面板（主口径腿）
def test_panel_hit_is_presale_even_if_not_in_manual_list():
    """面板说预售中 ⇒ 就是预售（手写名单里没有的款也照样进 D 组）。"""
    _panel({"BHY": {"202504-5SETA": {"S", "M"}}})
    assert S.is_presale("BHY-202504-5SetA-S", "BHY")
    assert S.is_presale("BHY-202504-5SetA-M", "BHY")
    # 面板里没有的尺码不进 D
    assert not S.is_presale("BHY-202504-5SetA-L", "BHY")


def test_panel_enables_other_temu_stores():
    """面板里 KinglamanSport 也有预售中 ⇒ 那家店自动纳入预售口径（不再写死在 BHY/BEL）。"""
    _panel({"KinglamanSport": {"203804-4SETA": {"S"}}})
    assert S.is_presale("BK-203804-4SetA-S", "KinglamanSport")
    assert not S.is_presale("BK-203804-4SetA-L", "KinglamanSport")
    assert "KinglamanSport" in S.presale_stores()


def test_presale_stores_union_of_manual_and_panel():
    _panel({"KinglamanSport": {"203804-4SETA": {"S"}}})
    st = S.presale_stores()
    for s in ("BHY", "BEL", "KinglamanSport"):
        assert s in st, st


def test_presale_only_for_temu_half_managed_stores():
    """SHEIN 店（BS/BD/BAN/BE）即便面板有同名记录也不进 D 组（面板只收 TEMU 行）。"""
    _panel({"BHY": {"204207-4SETA": {"XXL"}}})
    for store in ("BS", "BD", "BAN", "BE", "XIANSUO", ""):
        assert not S.is_presale("BHY-204207-4SetA-XXL", store), store


def test_panel_normalize_only_temu_presale_rows():
    """`_norm_panel`：只收 platform=TEMU 且 status=预售中；款号去店名/品牌前缀、尺码大写。"""
    raw = {"items": [
        {"platform": "TEMU", "store": "BEL", "code": "BEL-202504-5SetA", "size": "s",
         "status": "预售中"},
        {"platform": "SHEIN", "store": "BS", "code": "BS-102105-3BHL", "size": "XL",
         "status": "已清零"},
        {"platform": "TEMU", "store": "BHY", "code": "BHY-204207-4SetA", "size": "XXL",
         "status": "已过预售"},
    ]}
    got = S._norm_panel(raw)
    assert got == {"BEL": {"202504-5SETA": {"S"}}}
    assert S._norm_panel({}) == {}
    assert S._norm_panel({"items": None}) == {}


def test_desc_mentions_both_sources():
    _panel({"BEL": {"202504-5SETA": {"L"}}})
    S.PANEL_META = {"source": "本地产物", "generatedAt": "2026-11-19 11:11:43"}
    d = S.presale_desc()
    for token in ("面板", "202504-5SETA", "204207-4SETA", "YOGA-4SETA", "207213-4SETA",
                  "2026-11-19"):
        assert token in d, (token, d)


def test_desc_warns_when_panel_missing():
    """面板读不到时必须明说（否则表上会像「今天没人开预售」一样）。"""
    assert "面板读不到" in S.presale_desc()
