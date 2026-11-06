# -*- coding: utf-8 -*-
"""test_bel_split_by_stock.py — BEL 拆分规则（帕沃尔优先 + 防超卖）的纯函数测试。

钉住的都是真机踩过的坑：
  · 匹配必须去品牌前缀（BH/BK/BS/BS2/BLA/BEL），否则 `BEL-` 永远匹配不上 BH-；
  · 帕沃尔只认发货仓（加州3仓/kansas仓），Temu自动分仓不算可用；
  · 同款多单要按总需求分配（204203-4SetC-L 只 2 件 / 本批 5 单 → 只能发 2 单）；
  · 缺货的不能混进 A/B（超卖风险）；
  · 落档原因要说清「分完了」vs「没这个 SKU」。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import bel_split_by_stock as B  # noqa: E402


# ---------------------------------------------------------------- norm_sku / build_index


def test_norm_strips_store_and_brand_prefixes():
    assert B.norm_sku("BEL-203304-4SetA-S") == "203304-4SETA-S"
    assert B.norm_sku("BH-102104A-3GHL-L") == "102104A-3GHL-L"
    assert B.norm_sku("BS2-204605-3BHP-M") == "204605-3BHP-M"
    assert B.norm_sku("BLA-102303-6SetA-M") == "102303-6SETA-M"


def test_norm_keeps_version_dot_and_trailing_letter():
    """`6SetA1.1` 的点、`102104A` 的尾字母都不能丢（库存 JSON 的拆字段会丢）。"""
    assert B.norm_sku("BEL-102303-6SetA1.1-XL") == "102303-6SETA1.1-XL"
    assert B.norm_sku("BEL-102104A-3GHL-L") == "102104A-3GHL-L"


def test_build_index_filters_by_warehouse():
    items = [
        {"sku": "BS2-204605-3BHP-M", "wh": "加州3仓", "stock": 166},
        {"sku": "BH-204605-3BHP-M", "wh": "kansas仓", "stock": 1},
        {"sku": "BH-204605-3BHP-M", "wh": "Temu自动分仓", "stock": 999},
    ]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH)
    got = idx["204605-3BHP-M"]
    assert len(got) == 2, "Temu自动分仓不该计入可用"
    assert sum(x["stock"] for x in got) == 167


def test_build_index_indexes_name_field_too():
    items = [{"sku": "BK-104102-1B-L", "name": "BH-104102-1B-L", "wh": "加州3仓", "stock": 33}]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH)
    assert "104102-1B-L" in idx


# ---------------------------------------------------------------- allocate


def _pw(rows):
    return {B.norm_sku(x["sku"]): [x] for x in rows}


def _lx(rows):
    return {B.norm_sku(x["sku"]): [x] for x in rows}


def test_prefers_pw_when_both_have_stock():
    """两边都有货 → 归帕沃尔（用户口径：优先看帕沃尔）。"""
    pw = _pw([{"sku": "BEL-203802-4SetB-M", "wh": "加州3仓", "stock": 30}])
    lx = _lx([{"sku": "BS2-203802-4SetB-M", "avail": 92}])
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-203802-4SetB-M", "qty": 1}], pw, lx)
    assert len(g["PW"]) == 1 and not g["HX"]


def test_falls_back_to_hx_when_pw_empty():
    pw = _pw([])
    lx = _lx([{"sku": "BH-203805-3BHL-M", "avail": 250}])
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-203805-3BHL-M", "qty": 1}], pw, lx)
    assert len(g["HX"]) == 1 and g["HX"][0]["wh"] == "ZOO WAREHOUSE ⑥美西"


def test_no_stock_goes_to_miss_not_pw():
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-104102-1B2-L", "qty": 1}], {}, {})
    assert len(g["MISS"]) == 1 and not g["PW"] and not g["HX"]
    assert "无此 SKU" in g["MISS"][0]["why"]


def test_oversell_guard_allocates_only_available_units():
    """真机案例：204203-4SetC-L 帕沃尔只有 2 件、本批 5 单 → 只能放 2 单。"""
    pw = _pw([{"sku": "BH-204203-4SetC-L", "wh": "kansas仓", "stock": 2}])
    orders = [{"ord": "PO-%d" % i, "sku": "BEL-204203-4SetC-L", "qty": 1} for i in range(5)]
    g = B.allocate(orders, pw, {})
    assert len(g["PW"]) == 2, g
    assert len(g["MISS"]) == 3, g


def test_miss_reason_distinguishes_exhausted_from_absent():
    """被前面单分完 ≠ 没这个 SKU —— 排查时必须能分清。"""
    pw = _pw([{"sku": "BH-204203-4SetC-L", "wh": "kansas仓", "stock": 1}])
    orders = [{"ord": "PO-1", "sku": "BEL-204203-4SetC-L", "qty": 1},
              {"ord": "PO-2", "sku": "BEL-204203-4SetC-L", "qty": 1}]
    g = B.allocate(orders, pw, {})
    assert len(g["PW"]) == 1
    assert "分完" in g["MISS"][0]["why"], g["MISS"][0]["why"]


def test_qty_greater_than_one_respects_units():
    pw = _pw([{"sku": "BH-104102-1B-M", "wh": "加州3仓", "stock": 1}])
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-104102-1B-M", "qty": 2}], pw, {})
    assert len(g["MISS"]) == 1, "需 2 件但只有 1 件 → 不许拆开发"


def test_every_order_lands_exactly_once():
    pw = _pw([{"sku": "BH-104102-1B-M", "wh": "加州3仓", "stock": 5}])
    lx = _lx([{"sku": "BH-203805-3BHL-S", "avail": 9}])
    orders = [{"ord": "PO-1", "sku": "BEL-104102-1B-M", "qty": 1},
              {"ord": "PO-2", "sku": "BEL-203805-3BHL-S", "qty": 1},
              {"ord": "PO-3", "sku": "BEL-999999-9X-XL", "qty": 1}]
    g = B.allocate(orders, pw, lx)
    got = [r["ord"] for side in ("PW", "HX", "MISS") for r in g[side]]
    assert sorted(got) == ["PO-1", "PO-2", "PO-3"]


# ---------------------------------------------------------------- 2026-11-05 真机回归


def test_same_row_sku_and_name_are_not_double_counted():
    """真机案：`sku` 与 `name` 是同一串，旧实现两字段都收 ⇒ 库存算成双倍。

    实测 `203304-4SetA-S` 帕沃尔只有 4 件（BS 1 + BH 3），却报 8 件。
    """
    items = [
        {"sku": "BS-203304-4SetA-S", "name": "BS-203304-4SetA-S", "wh": "kansas仓", "stock": 1},
        {"sku": "BH-203304-4SetA-S", "name": "BH-203304-4SetA-S", "wh": "kansas仓", "stock": 3},
    ]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH)
    rows = idx["203304-4SETA-S"]
    assert len(rows) == 2, rows
    assert sum(int(x["stock"]) for x in rows) == 4, "不许双计"


def test_warehouse_is_the_one_that_has_stock_not_hardcoded():
    """真机案：`203304-4SetA-S` 加州3仓 stock=0（在途 69 不算）、kansas 才有 4 件。

    旧实现把 `wh` 写死「加州3仓」⇒ 下单会因仓里没货失败。
    """
    items = [{"sku": "BS-203304-4SetA-S", "name": "BS-203304-4SetA-S",
              "wh": "加州3仓", "stock": 0, "transit": 69},
             {"sku": "BH-203304-4SetA-S", "name": "BH-203304-4SetA-S",
              "wh": "kansas仓", "stock": 4, "transit": 0}]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH)
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-203304-4SetA-S", "qty": 1}], idx, {})
    assert len(g["PW"]) == 1
    assert g["PW"][0]["wh"] == "kansas仓", g["PW"][0]


def test_warehouse_prefers_jiazhou3_when_both_have_stock():
    items = [{"sku": "BH-204605-3BHP-M", "name": "BH-204605-3BHP-M", "wh": "加州3仓", "stock": 166},
             {"sku": "BK-204605-3BHP-M", "name": "BK-204605-3BHP-M", "wh": "kansas仓", "stock": 1}]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH)
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-204605-3BHP-M", "qty": 1}], idx, {})
    assert g["PW"][0]["wh"] == "加州3仓"

