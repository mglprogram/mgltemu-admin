# -*- coding: utf-8 -*-
"""回归钉：`temu_label_prep` 从行字典抽接口要的 goods 结构（纯函数，不连网）。

★ 2026-11-23：买面单接口要 `order_sn/parent_order_sn/goods_id/sku_id/quantity`，
  而导出表的列名五花八门（参考单号/子订单号/商品ID/SKU ID…）⇒ 这里钉死别名映射，
  且**缺字段就返回 None，不猜、不填假值**。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
if _POWER not in sys.path:
    sys.path.insert(0, _POWER)

import temu_label_prep as P  # noqa: E402


def test_maps_chinese_headers():
    row = {"参考单号": "PO-211-1", "子订单号": "211-1", "商品ID": 606,
           "SKU ID": "910", "数量": "2"}
    g = P.to_goods_row(row)
    assert g == {"order_sn": "211-1", "parent_order_sn": "PO-211-1",
                 "goods_id": 606, "sku_id": "910", "quantity": 2}


def test_maps_english_headers():
    row = {"parentOrderSn": "PO-211-2", "orderSn": "211-2", "goodsId": 7,
           "skuId": 8, "qty": 1}
    g = P.to_goods_row(row)
    assert g and g["quantity"] == 1 and g["sku_id"] == "8"


def test_missing_field_returns_none():
    """缺 goods_id / sku_id 一律 None（不许猜，猜错会买成别的商品的面单）。"""
    assert P.to_goods_row({"参考单号": "PO-1", "子订单号": "1"}) is None
    assert P.to_goods_row({"参考单号": "PO-1", "goods_id": 1}) is None


def test_build_goods_groups_by_parent_and_filters():
    rows = [
        {"参考单号": "PO-A", "子订单号": "A1", "goods_id": 1, "sku_id": "s1", "数量": 1},
        {"参考单号": "PO-A", "子订单号": "A2", "goods_id": 2, "sku_id": "s2", "数量": 2},
        {"参考单号": "PO-B", "子订单号": "B1", "goods_id": 3, "sku_id": "s3", "数量": 1},
        {"参考单号": "PO-C"},                       # 缺字段 → 进 bad
    ]
    groups, bad = P.build_goods(rows, pos=["PO-A", "PO-C"])
    assert sorted(groups) == ["PO-A"]
    assert len(groups["PO-A"]) == 2
    assert sum(x["quantity"] for x in groups["PO-A"]) == 3
    assert len(bad) == 1


def test_quantity_bad_value_defaults_to_one():
    row = {"参考单号": "PO", "子订单号": "1", "goods_id": 1, "sku_id": "s",
           "数量": "abc"}
    assert P.to_goods_row(row)["quantity"] == 1


def test_describe_readable():
    groups = {"PO-A": [{"quantity": 2}, {"quantity": 1}]}
    assert "PO-A" in P.describe(groups) and "3 件" in P.describe(groups)
