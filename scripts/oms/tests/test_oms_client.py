# -*- coding: utf-8 -*-
"""scripts/oms 的离线单测（不碰网络）。

跑：python -m pytest scripts/oms/tests -q
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))

from oms_client import (DETAIL_BATCH_MAX, PAGE_SIZE_MAX,                  # noqa: E402
                        OmsError, authcode, resolve_account)


def test_authcode_matches_documented_shape():
    """authcode = HMAC-SHA256(appKey + compact_json(data, 键排序) + reqTime, appSecret)。

    这里用固定输入把**字节级**的拼接钉死：任何一处改动（分隔符、排序、是否紧凑）
    都会让签名变掉 → 线上直接 401/签名失败。
    """
    a = authcode("KEY", "SECRET", {"b": 2, "a": 1}, "1700000000")
    # 手工按同一规则算一遍（独立实现，防止"改了代码又改了测试"）
    import hashlib
    import hmac
    ds = '{"a":1,"b":2}'          # 键排序 + 无空格
    msg = ("KEY" + ds + "1700000000").encode()
    want = hmac.new(b"SECRET", msg, hashlib.sha256).hexdigest()
    assert a == want

    # 空 data 用 "{}"，不是空串
    ds2 = "{}"
    want2 = hmac.new(b"SECRET", ("KEY" + ds2 + "1").encode(), hashlib.sha256).hexdigest()
    assert authcode("KEY", "SECRET", {}, "1") == want2

    # None 用空串
    want3 = hmac.new(b"SECRET", ("KEY" + "" + "1").encode(), hashlib.sha256).hexdigest()
    assert authcode("KEY", "SECRET", None, "1") == want3


def test_authcode_is_order_insensitive():
    """data 的键序不影响签名（内部按 sort_keys 归一）。"""
    assert authcode("K", "S", {"b": 1, "a": 2}, "9") == authcode("K", "S", {"a": 2, "b": 1}, "9")


def test_limits_are_the_measured_ones():
    """实测上限：pageSize 50、detail 批量 50 —— 改大就会被服务端拒（records 空 / code 10002）。"""
    assert PAGE_SIZE_MAX == 50
    assert DETAIL_BATCH_MAX == 50


def test_resolve_account_prefers_env(monkeypatch):
    monkeypatch.setenv("OMS_APP_KEY", "env-key")
    monkeypatch.setenv("OMS_APP_SECRET", "env-secret")
    assert resolve_account() == ("env-key", "env-secret")


def test_resolve_account_errors_when_missing(monkeypatch):
    monkeypatch.delenv("OMS_APP_KEY", raising=False)
    monkeypatch.delenv("OMS_APP_SECRET", raising=False)
    import oms_client
    old = oms_client.ACCOUNT_FILE
    oms_client.ACCOUNT_FILE = os.path.join(HERE, "_nonexistent_account.json")
    try:
        try:
            resolve_account()
            raise AssertionError("应当抛 OmsError")
        except OmsError as e:
            assert "appKey" in str(e)
    finally:
        oms_client.ACCOUNT_FILE = old


def test_slim_detail_keeps_what_the_panel_needs():
    """slim_detail：州/市/邮编 + 产品 + 面单链接必须留下，体积要压下来。"""
    from run_oms_orders import slim_detail
    raw = {
        "outboundOrderNo": "OBS1", "storeName": "BHY", "statusName": "仓库处理中",
        "platformOrderNo": "PO-211-1", "referOrderNo": "SO1",
        "provinceCode": "TX", "provinceName": "Texas", "cityName": "DALLAS",
        "postCode": "75217-2625", "orderCreateTime": "2026-10-15 12:00:00",
        "logisticsTrackNo": "T1", "logisticsTrackNos": [],
        "productList": [
            {"sku": "D14450X", "productName": "BS2-202504-5SetA-L", "quantity": 2, "skuId": "x"},
            {"sku": "D14450Y", "productName": "BS2-202504-5SetB-L", "quantity": 1, "skuId": "y"},
        ],
        "expressList": [{"packMark": "OBS1_P1", "trackNo": "T1", "weight": 0.7,
                         "pkgSkuNumInfo": "D14450X*2", "fileUrl": "https://x/label.pdf"}],
        "receiver": "******", "addressOne": "******",   # 不该进产物
    }
    s = slim_detail(raw)
    assert s["storeName"] == "BHY" and s["provinceName"] == "Texas"
    assert s["postCode"] == "75217-2625"
    assert [p["name"] for p in s["products"]] == ["BS2-202504-5SetA-L", "BS2-202504-5SetB-L"]
    assert s["products"][0]["qty"] == 2
    assert s["packs"][0]["labelUrl"] == "https://x/label.pdf"
    assert s["logisticsTrackNos"] == ["T1"]        # 列表空时回落到单值
    assert "receiver" not in s and "addressOne" not in s   # 脱敏字段不落盘
    assert len(json.dumps(s, ensure_ascii=False)) < 1200   # 原始 4KB → 压缩到 1KB 内
