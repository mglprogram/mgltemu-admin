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


def test_track_without_manual_is_confirmable():
    """★ 2026-11-20 口径修正：**有运单号就是可确认**。

    旧判据还要求「去手动确认」字样，实测 KLS 待发货 24 单只挑出 2 单，而平台按钮写着 11
    ——「去手动确认」只在没运单号的行里出现，有号的行平台直接认。
    """
    rows = [{"po": "PO-1", "track": "1Z2K1W60YW48319976", "manual": False}]
    g = T.pick_confirmable(rows)
    assert g["n_confirmable"] == 1
    assert [r["po"] for r in g["confirmable"]] == ["PO-1"]
    assert len(g["track_only"]) == 1
    assert g["manual"] == []


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
    assert sorted(got) == ["A", "B", "B", "C"]     # B 同时属于 confirmable 与 track_only


# ------------------------------------------------ 零勾选直接点批量（2026-11-20 用户口径）
# 用户连纠三次：「不用一个个点击」→「可以提前拉到500」→「你不需要一个个勾选」。
# 最终口径：**一个都不勾**，直接点平台那颗「批量确认发货 N」。
#   · N 是平台自己算的可确认数，不勾点它就是对的；
#   · 勾了反而把 N 拉低（实测全勾 20 行 → 11→7；只勾「卖家发货」档 → 11→3）；
#   · 真机实测零勾选一跑：待发货 25 → 14（正好 11），按钮变 0 并置灰。

def test_zero_selection_batch_path_exists():
    assert callable(getattr(T, "click_batch_confirm", None))


def test_batch_path_does_not_select_rows_first():
    """默认路径不许再逐行勾选（`--only` 才走勾选）。"""
    import io
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temu_confirm_ship.py")
    src = io.open(p, encoding="utf-8").read()
    i = src.find("def run(")          # 实现已收敛进 run()，main() 只是壳
    body = src[i:i + 9000]
    j = body.find("_select_rows(pg, targets, _log)")
    assert j > 0, "勾选逻辑被整个删掉了（--only 还要用）"
    head = body[:j]
    assert "if targets:" in head, "默认路径仍在逐行勾选 —— 用户明确说不用勾"
    # 默认路径必须是直接调批量
    k = body.find("click_batch_confirm(pg, _log)")
    assert k > 0, "默认路径没有调用零勾选批量"


def test_page_size_retries_dropdown_open():
    """每页下拉要点两次才展开；没设成 500 就只看得到 20 行，第 2 页的单会漏。

    真机：`li.PGT_sizeChanger_123` 文本是「每页\\n条」（不含当前值），
    第一次点只聚焦、第二次才展开 ⇒ 必须重试点开，不能点一次就放弃。
    """
    import io
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temu_confirm_ship.py")
    src = io.open(p, encoding="utf-8").read()
    i = src.find("def _set_page_size_500")
    body = src[i:i + 2600]
    assert "for attempt in" in body, "没重试展开下拉 ⇒ 永远报「下拉里没有 500 选项」"
    assert "dispatch_mouse" in body


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
