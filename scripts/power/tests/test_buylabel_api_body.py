# -*- coding: utf-8 -*-
"""回归钉：`build_send_item()` 拼出的 batch_send body 必须与真机抓到的一致。

样本：`_logs/temu-buylabel-api/send_full_20261123_180613.json`（2026-11-23 BHY 真单 SwiftX）。
没有样本时 skip（样本是运行产物，不入库）。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
import temu_buylabel_api as A  # noqa: E402

CAP = r"D:\AI-workplace\TEMU\_logs\temu-buylabel-api\send_full_20261123_180613.json"


def _load():
    if not os.path.exists(CAP):
        pytest.skip("没有真机抓包样本（运行产物，不入库）")
    d = json.load(open(CAP, encoding="utf-8"))
    send = [r for r in d["requests"] if r["url"].endswith("batch_send")][0]
    return json.loads(send["postData"])["send_request_list"][0]


def test_builder_matches_real_body():
    """★ 2026-11-23 真机：48 个字段逐一比对，必须 0 处不一致、0 处多余。"""
    orig = _load()
    goods = orig["order_send_info_list"]
    dims = {k: orig[k] for k in ("length", "width", "height",
                                 "dimension_unit", "weight", "weight_unit")}
    built = A.build_send_item(
        orig, goods, dims,
        {"warehouse_id": orig["warehouse_id"], "warehouse_name": orig["warehouse_name"]},
        (orig["reserve_time_period"]["begin_time_ts"],
         orig["reserve_time_period"]["end_time_ts"]))
    diff = [k for k in orig
            if json.dumps(orig[k], sort_keys=True) != json.dumps(built.get(k), sort_keys=True)]
    extra = [k for k in built if k not in orig]
    assert not diff, "字段不一致：%s" % diff
    assert not extra, "多出字段：%s" % extra
    assert len(built) == len(orig)


def test_carrier_name_falls_back_to_shipping_company_name():
    """provider 接口给的是 `shipping_company_name`，拼 body 必须转成 `ship_company_name`。"""
    ch = {"shipping_company_name": "SwiftX", "service_code": "SWIf",
          "channel_id": "1", "route_id": "2", "channel_id_version": 11,
          "ship_company_id": 999289591,
          "online_estimated_vo": {"charge_amount_si": 318000, "day_unit": 2,
                                  "delivery_day_min": 1, "delivery_day_max": 5}}
    goods = [{"order_sn": "211-1", "parent_order_sn": "PO-211-1",
              "goods_id": 1, "sku_id": "2", "quantity": 1}]
    it = A.build_send_item(ch, goods, {}, {"warehouse_id": "WH", "warehouse_name": "加州三仓"},
                           (1, 2))
    assert it["ship_company_name"] == "SwiftX"
    assert it["expect_shipping_fee"] == 318000
    assert it["expect_delivery_lower_limit"] == 1
    assert it["expect_delivery_upper_limit"] == 5


def test_send_body_envelope():
    body = A.build_send_body([{"x": 1}])
    assert body["send_type"] == 0
    assert body["send_request_list"] == [{"x": 1}]
    assert body["push_to_cw"] is False
    assert body["replaced_package_sn_list"] == []
