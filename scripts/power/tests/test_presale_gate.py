# -*- coding: utf-8 -*-
"""test_presale_gate.py — 预售单前置拦截的回归钉。

★ 2026-11-24 用户截图确认：订单列表页有蓝色「**预售**」标签，
  接口商品行对应字段 `isExtendedPreSaleOrder=true` / `preSaleEndTimeStr`。
  用户口径：预售单**不能买面单**，且必须在**查渠道之前**就拦掉，
  否则会误报「没有可用物流商及服务」（真机踩过：PO-211-01418583368312457）。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
if _POWER not in sys.path:
    sys.path.insert(0, _POWER)

import order_list_api as O  # noqa: E402


def _page_item(order_list_extra: dict) -> dict:
    item = {"parentOrderMap": {"parentOrderSn": "PO-1", "regionId1": 211,
                               "parentOrderStatus": 2},
            "orderList": [dict({"orderSn": "211-1", "goodsId": 1, "skuId": "2",
                                "quantity": 1}, **order_list_extra)]}
    return item


def test_presale_flag_detected():
    res = {"pageItems": [_page_item({"isExtendedPreSaleOrder": True,
                                     "preSaleEndTimeStr": "2026-12-04 23:59:59 UTC+8"})]}
    it = O.parse_items(res)[0]
    assert it["isExtendedPreSale"] is True
    assert "2026-12-04" in it["preSaleEndTimeStr"]


def test_ship_restricted_also_counts():
    res = {"pageItems": [_page_item({"isExtendedPreSaleShipRestricted": True})]}
    assert O.parse_items(res)[0]["isExtendedPreSale"] is True


def test_has_presale_goods_alone_does_not_count():
    """`hasPreSaleGoods=true` 单独**不算**（避免把普通单误拦），只看 isExtendedPreSale*。"""
    res = {"pageItems": [_page_item({"hasPreSaleGoods": True})]}
    assert O.parse_items(res)[0]["isExtendedPreSale"] is False


def test_normal_order_not_flagged():
    res = {"pageItems": [_page_item({})]}
    it = O.parse_items(res)[0]
    assert it["isExtendedPreSale"] is False
    assert it["preSaleEndTimeStr"] == ""
    assert len(it["goods"]) == 1
