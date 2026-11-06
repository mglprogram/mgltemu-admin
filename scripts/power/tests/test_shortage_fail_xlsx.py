# -*- coding: utf-8 -*-
"""test_shortage_fail_xlsx.py — 缺货 / 失败订单两 sheet 的归类规则（纯函数）。

钉的都是真机踩过的坑：
  · 「缺货」里混进其实已出单有运单号的单 ⇒ 会让人工白去补货；
  · 「失败订单」里混进「恒信那份」和「缺货单」的「未下单」 ⇒ 几百行噪音；
  · 失败原因要能区分「库存不足」「状态异常」「网络异常」「没提交」。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import gen_shortage_fail_xlsx as G  # noqa: E402


# ---------------------------------------------------------------- classify_miss

def test_classify_splits_already_shipped_out_of_miss():
    """真机案：BHY 308 单 MISS 里 6 单其实已出单有运单号（BS-202504-5SetE-XL）。"""
    rows = [{"ord": "PO-1", "sku": "X-1A-L", "qty": 1},
            {"ord": "PO-2", "sku": "X-1A-L", "qty": 1}]
    idx = {"PO-2": {"id": "9", "wb": "GFUS01082220889858", "pb": "2"}}
    real, already = G.classify_miss(rows, idx)
    assert [r["ord"] for r in real] == ["PO-1"]
    assert [r["ord"] for r in already] == ["PO-2"]
    assert already[0]["waybill"] == "GFUS01082220889858"


def test_classify_without_index_keeps_all_as_shortage():
    rows = [{"ord": "PO-1", "sku": "X-1A-L", "qty": 1}]
    real, already = G.classify_miss(rows, {})
    assert len(real) == 1 and not already


def test_classify_blank_waybill_is_not_shipped():
    rows = [{"ord": "PO-1", "sku": "X-1A-L", "qty": 1}]
    real, already = G.classify_miss(rows, {"PO-1": {"wb": "   "}})
    assert len(real) == 1 and not already


# ---------------------------------------------------------------- miss_reason

def test_miss_reason_extracts_variant_hint():
    why = ("两边都无此 SKU（恒信 0 / 帕沃尔 0）——**变体不存在**："
           "10SETA（同款同码库存里只有 5SETA/5SETB），疑似上链接命名有误")
    txt, hint = G.miss_reason({"why": why})
    assert "变体在库存里不存在" in txt
    assert hint.startswith("10SETA")


def test_miss_reason_distinguishes_exhausted():
    txt, hint = G.miss_reason({"why": "可用已被前面的单分完（本批共需 5，恒信 0 + 帕沃尔 1）"})
    assert "分完" in txt and not hint


def test_miss_reason_plain_absent():
    txt, hint = G.miss_reason({"why": "两边都无此 SKU"})
    assert "查不到" in txt and not hint


# ---------------------------------------------------------------- fail_reason

def test_fail_reason_stock_shortage():
    step, why = G.fail_reason(
        "下单失败", "下单成功0条,失败1条,PO-x下单失败-sku:D14450QZ库存不足发货")
    assert step == "帕沃尔下单"
    assert "D14450QZ" in why and "库存不足" in why


def test_fail_reason_warehouse_empty():
    _, why = G.fail_reason(
        "下单失败", "下单成功0条,失败1条,PO-x下单失败-sku:D14450K9仓库没有库存")
    assert "该仓无此库存" in why


def test_fail_reason_status_abnormal():
    _, why = G.fail_reason("下单失败", "PO-x下单失败-SKU:状态异常无法下单")
    assert "状态异常" in why


def test_fail_reason_network():
    _, why = G.fail_reason("下单失败", "PO-x下单失败,网络异常，请稍后重试")
    assert "网络异常" in why


def test_fail_reason_not_submitted():
    step, why = G.fail_reason("未下单", "")
    assert "未提交" in step
    assert "需重下" in why


# ---------------------------------------------------------------- build

def _split(pw=(), hx=(), miss=()):
    return {"groups": {"PW": [{"ord": p, "sku": "S", "qty": 1} for p in pw],
                       "HX": [{"ord": p, "sku": "S", "qty": 1} for p in hx],
                       "MISS": [{"ord": p, "sku": "S", "qty": 1, "why": "两边都无此 SKU"}
                                for p in miss]}}


def test_build_has_exactly_two_sheets():
    out = G.build("BHY", _split(), [], {})
    assert list(out.keys()) == ["缺货", "失败订单"]


def test_build_skips_henxin_not_ordered():
    """恒信那份在帕沃尔页面必然显示「未下单」（它走领星 OMS）——不算失败。"""
    out = G.build("BHY", _split(hx=["PO-HX"]),
                  [{"po": "PO-HX", "status": "未下单", "sku": "S"}], {})
    assert out["失败订单"] == []


def test_build_skips_miss_not_ordered():
    """缺货单本来就没派仓库，「未下单」是预期，不是失败。"""
    out = G.build("BHY", _split(miss=["PO-M"]),
                  [{"po": "PO-M", "status": "未下单", "sku": "S"}], {})
    assert out["失败订单"] == []


def test_build_keeps_pw_not_ordered_as_failure():
    """派过帕沃尔却「未下单」⇒ 真失败（下了但没成）。"""
    out = G.build("BHY", _split(pw=["PO-P"]),
                  [{"po": "PO-P", "status": "未下单", "sku": "S", "wh": "加州3仓"}], {})
    assert len(out["失败订单"]) == 1
    assert out["失败订单"][0]["拆仓归属"] == "帕沃尔"


def test_build_keeps_any_order_that_explicitly_failed():
    """「下单失败」无论是哪一份都要报（恒信那份也可能在帕沃尔尝试过）。"""
    out = G.build("BHY", _split(miss=["PO-M"]),
                  [{"po": "PO-M", "status": "下单失败", "sku": "S",
                    "err": "PO-M下单失败-sku:D1库存不足发货"}], {})
    assert len(out["失败订单"]) == 1


def test_build_shortage_puts_real_before_already_shipped():
    """真缺货要排在前面（要人工补货）；已下单的压到最后。"""
    split = _split(miss=["PO-A", "PO-B"])
    idx = {"PO-B": {"wb": "GFUS1"}}
    out = G.build("BHY", split, [], idx)
    assert out["缺货"][0]["参考单号"] == "PO-A"
    assert out["缺货"][-1]["参考单号"] == "PO-B"
    assert out["缺货"][0]["状态"].startswith("真缺货")
    assert out["缺货"][-1]["状态"].startswith("已实际下单")


def test_build_ignores_blank_po():
    out = G.build("BHY", _split(), [{"po": "", "status": "下单失败"}], {})
    assert out["失败订单"] == []
