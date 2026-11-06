# -*- coding: utf-8 -*-
"""test_temu_confirm_ship.py — TEMU「待发货 → 批量确认发货」单入口的纯函数测试。

钉住的都是当天真机踩过的判据：
  · 「可确认」= 有运单号 **且** 行内有「去手动确认」（只有号不够，勾错会让按钮变 0）
  · 成功判据**只认「待发货」计数按目标数下降**；读不到就是 unknown，不许当成功
  · 计数没变 ⇒ 四层里有一层没点（no_effect），不能报成功
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import temu_confirm_ship as T  # noqa: E402


# ---------------------------------------------------------------- pick_confirmable


def test_track_and_manual_is_confirmable():
    rows = [{"po": "PO-1", "track": "1Z2K1W60YW48319976", "manual": True}]
    g = T.pick_confirmable(rows)
    assert g["n_confirmable"] == 1
    assert g["confirmable"][0]["po"] == "PO-1"


def test_track_without_manual_is_not_confirmable():
    """只有运单号但平台不给「去手动确认」⇒ 勾了会把按钮变 0，不能选。"""
    rows = [{"po": "PO-1", "track": "1Z2K1W60YW48319976", "manual": False}]
    g = T.pick_confirmable(rows)
    assert g["n_confirmable"] == 0
    assert len(g["track_only"]) == 1


def test_no_track_is_not_confirmable():
    rows = [{"po": "PO-1", "track": "", "manual": False}]
    g = T.pick_confirmable(rows)
    assert g["n_confirmable"] == 0
    assert len(g["no_track"]) == 1


def test_empty_rows():
    g = T.pick_confirmable([])
    assert g["n_confirmable"] == 0
    assert g["confirmable"] == []


def test_groups_are_disjoint_and_cover_all():
    rows = [
        {"po": "A", "track": "1Z2K1W60YW48319976", "manual": True},
        {"po": "B", "track": "GFUS01082186338180", "manual": False},
        {"po": "C", "track": "", "manual": False},
    ]
    g = T.pick_confirmable(rows)
    got = ([r["po"] for r in g["confirmable"]] + [r["po"] for r in g["track_only"]]
           + [r["po"] for r in g["no_track"]])
    assert sorted(got) == ["A", "B", "C"]


# ---------------------------------------------------------------- parse_tab_count


def test_parse_tab_count():
    assert T.parse_tab_count("待发货(121)") == 121
    assert T.parse_tab_count("待发货(0)") == 0


def test_parse_tab_count_unreadable_is_minus_one():
    """读不到必须给 -1（而不是 0）——0 会被误当成"已经全发完"。"""
    assert T.parse_tab_count("") == -1
    assert T.parse_tab_count("待发货") == -1


# ---------------------------------------------------------------- judge_confirm


def test_judge_ok_when_dropped_by_target():
    j = T.judge_confirm(before=121, after=89, target=32)
    assert j["ok"] is True
    assert j["verdict"] == "ok"


def test_judge_no_effect_when_count_unchanged():
    """计数没变 ⇒ 四层里有一层没点，绝不能报成功。"""
    j = T.judge_confirm(before=121, after=121, target=32)
    assert j["ok"] is False
    assert j["verdict"] == "no_effect"
    assert "四层" in j["why"]


def test_judge_partial():
    j = T.judge_confirm(before=121, after=100, target=32)
    assert j["ok"] is False
    assert j["verdict"] == "partial"
    assert j["dropped"] == 21


def test_judge_unknown_when_count_unreadable():
    j = T.judge_confirm(before=-1, after=89, target=32)
    assert j["ok"] is False
    assert j["verdict"] == "unknown"


def test_judge_unknown_when_after_unreadable():
    j = T.judge_confirm(before=121, after=-1, target=32)
    assert j["ok"] is False
    assert j["verdict"] == "unknown"


def test_judge_more_dropped_than_target_is_still_ok():
    """别的会话同时确认了更多单 ⇒ 掉得更多也算成功（只要不少于目标）。"""
    j = T.judge_confirm(before=121, after=80, target=32)
    assert j["ok"] is True
