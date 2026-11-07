# -*- coding: utf-8 -*-
"""test_pw_deliver_ship.py — 帕沃尔交付页「待确认 → 确认订单 → 获取单号」的判据测试（纯函数）。

2026-11-06 真机事故的钉痕：pb=all 上按 PO 匹配「查到 124 行」就批量提交，
结果那 124 行是**别人的单**（id 与 hwc 的 124 单完全对不上）⇒ 替别的店确认了 124 单。
所以这里只测一件事：**门禁必须宁可不做，也不能做错**。
"""
import inspect
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
sys.path.insert(0, _POWER)

import pw_deliver_ship as D  # noqa: E402


def _raw(id_, tds):
    return {"id": str(id_), "tds": tds}


# ---------------------------------------------------------------- 行解析：按列头文字，不按固定下标


def test_rows_pb1_reference_no_column():
    """pb=1 待确认页：有「参考单号」、出库单号为空、状态「未确认」。"""
    raw = [_raw(1, ["1", "", "PO-211-123", "", "H049921899", "BK-1-L", "1", "", "UPS",
                     "kansas仓", "BHY", "", "未确认", "2026-11-06 18:32:38"])]
    r = D.rows_from_raw(raw)[0]
    assert r["po"] == "PO-211-123"
    assert r["out_no"] == ""
    assert r["status"] == "未确认"


def test_rows_pb2_has_out_no_and_no_po():
    """pb=2 待打包页：有「出库单号」、参考单号为空 ⇒ 不许把出库单号当成 PO。"""
    raw = [_raw(7, ["1", "GFUS01082229782278", "", "", "H049921899", "BK-1-L", "1", "",
                    "UPS", "kansas仓", "BHY", "", "待打包", "2026-11-06"])]
    r = D.rows_from_raw(raw)[0]
    assert r["po"] == "", "出库单号不是参考单号"
    assert r["out_no"] == "GFUS01082229782278"
    assert r["status"] == "待打包"


def test_rows_waybill_detected():
    raw = [_raw(8, ["1", "", "PO-211-1", "", "F1", "S", "1", "", "UPS", "加州3仓",
                    "BHY", "", "待打包", "1Z999AA10123456784"])]
    assert D.rows_from_raw(raw)[0]["wb"] == "1Z999AA10123456784"


def test_rows_skip_header_and_empty():
    assert D.rows_from_raw([_raw("", ["序号", "出库单号"])])[0]["po"] == ""


# ---------------------------------------------------------------- 门禁：judge_batch


def test_gate_ok_when_exactly_batch():
    rows = [{"po": "PO-1"}, {"po": "PO-2"}]
    g = D.judge_batch(rows, ["PO-1", "PO-2"], "1")
    assert g["ok"] and g["checked"] == 2


def test_gate_rejects_missing_orders():
    """本批里有单没查到 ⇒ 一条都不许提交（宁可不做）。"""
    g = D.judge_batch([{"po": "PO-1"}], ["PO-1", "PO-2"], "1")
    assert not g["ok"] and g["checked"] == 0
    assert "查不到" in g["why"]


def test_gate_rejects_extra_orders():
    """★ 事故复现：查到本批之外的单（别人的单混进来）⇒ 必须拒绝。"""
    g = D.judge_batch([{"po": "PO-1"}, {"po": "PO-OTHER"}], ["PO-1"], "1")
    assert not g["ok"] and g["checked"] == 0
    assert "本批之外" in g["why"]


def test_gate_shop_mode_requires_some_rows():
    assert D.judge_batch([], ["PO-1"], "1")["ok"] is False
    assert D.judge_batch([{"po": "PO-1"}], [], "all")["ok"] is True


# ---------------------------------------------------------------- 实现约束（防回退）


def test_confirm_gate_is_inside_stage_and_blocks_submit():
    """门禁通过后才允许勾选/提交；没通过要**在提交前**返回。"""
    src = inspect.getsource(D.stage_confirm)
    i_gate = src.index("judge_batch(")
    i_submit = src.index('click_by_text(pg, "提交"')
    assert i_gate < i_submit, "门禁必须在提交之前"
    assert "return {" in src[i_gate:i_submit], "没通过门禁必须直接返回，不许继续勾选提交"


def test_confirm_reads_back_status_not_button_click():
    """成功判据必须回读状态（离开「未确认」），不能只看结果页提示。"""
    src = inspect.getsource(D.stage_confirm)
    assert "未确认" in src
    assert "OK_AFTER_CONFIRM" in src


def test_apply_filter_rejects_when_po_not_written():
    """单号没写进去时会返回全部店铺的单 ⇒ 必须抛错，不许继续查询。"""
    src = inspect.getsource(D.apply_filter)
    assert "deliver_no" in src, "要按参考单号筛就得切 search_types=deliver_no"
    assert "拒绝按全量查询" in src


def test_pipeline_confirm_passes_batch_pos():
    """整链的 `pw_receive` 必须把本批单号传进去（--by po），不能按整店提交。"""
    import run_temu_fulfill as F  # noqa: PLC0415
    src = inspect.getsource(F.step_pw_receive)
    assert '"--by", "po"' in src
    assert "orders-file" in src
