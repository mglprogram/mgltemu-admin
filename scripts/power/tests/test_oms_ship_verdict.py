# -*- coding: utf-8 -*-
"""test_oms_ship_verdict.py — OMS 发货终态判据（用户 2026-11-06 口径，不许改）。

原话：
    「点击完确认并审核之后，你要去『处理中』查看这一单有没有转成处理中；
      否则你就要去『待获取平台面单』查看，如果在这个页面下，那就是下单失败了」

故三个终态，且**接口返回值/页签计数都不算判据**：
    · 处理中 找到                    → in_progress（成功）
    · 处理中 没有 + 待获取平台面单 找到 → place_failed（下单失败）
    · 两处都没有                      → unknown（如实报，不许当成功）

本文件把这条判据固化成纯函数，避免以后又"看到接口 200 就当成了"。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import oms_ship_verdict as V  # noqa: E402


def test_in_progress_wins_even_if_pending_also_hit():
    """「处理中」有就算成功 —— 哪怕「待获取平台面单」也残留一行（同一单可能历史留痕）。"""
    assert V.judge(hit_doing=1, hit_pending=1) == V.IN_PROGRESS


def test_pending_only_means_place_failed():
    assert V.judge(hit_doing=0, hit_pending=1) == V.PLACE_FAILED


def test_neither_means_unknown_not_success():
    v = V.judge(hit_doing=0, hit_pending=0)
    assert v == V.UNKNOWN
    assert v != V.IN_PROGRESS


def test_tabs_not_found_is_unknown():
    """页签都找不到（读不清）→ 未知，不许当成功。"""
    assert V.judge(hit_doing=0, hit_pending=0, tab_doing_found=False) == V.UNKNOWN


def test_multiple_hits_still_ok():
    assert V.judge(hit_doing=3, hit_pending=0) == V.IN_PROGRESS


def test_ok_code_mapping():
    assert V.exit_code(V.IN_PROGRESS) == 0
    assert V.exit_code(V.PLACE_FAILED) == 42
    assert V.exit_code(V.UNKNOWN) == 42


def test_verdict_is_not_derived_from_counts():
    """判据只认"搜到这一单"，不接受"页签计数变多了"这种间接信号。"""
    src = open(V.__file__, encoding="utf-8").read()
    body = src.split("def judge(", 1)[1].split("\ndef ", 1)[0]
    for bad in ("delta", "b_count", "before", "after", "count_diff"):
        assert bad not in body, "判据里不该出现计数差：%s" % bad


# ---------------------------------------------------------------- 批量（2026-11-06 用户口径）


def test_batch_all_in_progress():
    """用户 2026-11-06：「不用一单一单，可批量确认发货」——全在「处理中」就算成功。"""
    pos = ["PO-1", "PO-2", "PO-3"]
    r = V.judge_batch(pos, found_doing=pos, found_pending=[])
    assert r["ok"] is True
    assert r["in_progress"] == pos
    assert not r["place_failed"] and not r["unknown"]


def test_batch_partial_place_failed():
    pos = ["PO-1", "PO-2", "PO-3"]
    r = V.judge_batch(pos, found_doing=["PO-1"], found_pending=["PO-2"])
    assert r["ok"] is False
    assert r["in_progress"] == ["PO-1"]
    assert r["place_failed"] == ["PO-2"]
    assert r["unknown"] == ["PO-3"], "两处都没有的要单独列 unknown，不能混进失败"


def test_batch_unknown_when_tab_missing():
    r = V.judge_batch(["PO-1", "PO-2"], found_doing=[], found_pending=[],
                      tab_doing_found=False)
    assert r["ok"] is False
    assert r["unknown"] == ["PO-1", "PO-2"]


def test_batch_does_not_invent_results_for_extra_orders():
    """页面上搜到的单号若不在本批里，不该影响本批判定。"""
    r = V.judge_batch(["PO-1"], found_doing=["PO-1", "PO-OTHER"], found_pending=[])
    assert r["ok"] is True
    assert r["in_progress"] == ["PO-1"]

