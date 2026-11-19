# -*- coding: utf-8 -*-
"""头程「到港 → 送仓上架」口径单测。

真值例子来自用户原话（2026-11-19 定稿）：
    11.15（周日）→ 11.21（周六）
    11.16（周一）→ 11.24（下周二）
    11.17（周二）→ 11.27（下周五）
    「周三周四周五周六到 → 下周六」
    「跟渠道没关系 —— 送仓时间只跟周几到港有关，周日到港三个渠道都可能在下周六」
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goods"))

import headmile_eta as HE  # noqa: E402


def test_user_examples():
    assert HE.shelf_eta(date(2026, 11, 15), "美森")["eta"] == date(2026, 11, 21)
    assert HE.shelf_eta(date(2026, 11, 16), "以星")["eta"] == date(2026, 11, 24)
    assert HE.shelf_eta(date(2026, 11, 17), "盐田")["eta"] == date(2026, 11, 27)


def test_channel_does_not_matter():
    """同一到港日，三个渠道给同一个上架日（用户 2026-11-19 明确）。"""
    for ch in ("美森", "以星", "盐田", "", "未知"):
        assert HE.shelf_eta(date(2026, 11, 15), ch)["eta"] == date(2026, 11, 21)
        assert HE.shelf_eta(date(2026, 11, 18), ch)["eta"] == date(2026, 11, 28)


def test_weekday_table():
    """周一→下周二 · 周二→下周五 · 周三~周日→下周六。"""
    expected = {
        date(2026, 11, 16): date(2026, 11, 24),   # 周一
        date(2026, 11, 17): date(2026, 11, 27),   # 周二
        date(2026, 11, 18): date(2026, 11, 28),   # 周三
        date(2026, 11, 19): date(2026, 11, 28),   # 周四
        date(2026, 11, 20): date(2026, 11, 28),   # 周五
        date(2026, 11, 21): date(2026, 11, 28),   # 周六
        date(2026, 11, 22): date(2026, 11, 28),   # 周日
    }
    for arrive, eta in expected.items():
        assert HE.shelf_eta(arrive, "美森")["eta"] == eta


def test_strictly_after_arrival_week():
    """本周到港的赶不上本周的送仓 → 一律排下周（按周换班）。"""
    assert HE.shelf_eta(date(2026, 11, 20), "")["eta"] == date(2026, 11, 28)  # 周五到 → 下周六
    assert HE.shelf_eta(date(2026, 11, 16), "")["eta"] == date(2026, 11, 24)  # 周一到 → 下周二


def test_no_arrival_returns_none():
    assert HE.shelf_eta(None, "美森")["eta"] is None


def test_inspection_adds_two_weeks():
    """美国海关查验未放行：正常上架日 + 14 天。"""
    traces = [{"time": "2026-11-03 10:00:00", "info": "【国外查验】美国海关开箱查验"}]
    r = HE.shelf_eta(date(2026, 10, 27), "以星", traces)
    assert r["inspected"] and r["releaseAt"] is None
    # 10-27 周二到 → 下周五 11-06；未放行再后移两周 = 11-20
    assert r["eta"] == date(2026, 11, 20)


def test_released_moves_after_release_day():
    """已放行：正常上架日 / 后移两周 里取不早于放行日的那一个。"""
    traces = [
        {"time": "2026-11-18 10:00:00", "info": "【海关】查验放行"},
        {"time": "2026-11-04 10:00:00", "info": "【查验】海关查验中"},
    ]
    r = HE.shelf_eta(date(2026, 11, 2), "以星", traces)
    assert r["releaseAt"] == date(2026, 11, 18)
    # 11-02 周一到 → 下周二 11-03；后移两周 = 11-17，早于放行 11-18 → 顺延到 11-24
    assert r["eta"] == date(2026, 11, 24)


def test_baoguan_release_is_not_inspection_release():
    """「报关放行」是出货前动作，不能当查验放行（否则查验单被误判为已放行）。"""
    traces = [
        {"time": "2026-11-13 09:00:00", "info": "【到港】已于11/13抵达USLAX"},
        {"time": "2026-11-08 10:00:00", "info": "【国外查验】美国海关开箱查验"},
        {"time": "2026-10-21 10:00:00", "info": "【报关】已于10/21报关放行"},
    ]
    r = HE.shelf_eta(date(2026, 11, 13), "以星", traces)
    assert r["inspected"] and r["releaseAt"] is None
    # 11-13 周五到 → 下周六 11-21；未放行再后移两周 = 12-05
    assert r["eta"] == date(2026, 12, 5)


def test_export_inspection_is_not_customs_inspection():
    """出口/国内查验发生在开船前，船期已含延误 —— 不能再加两周（2026-11-19 口径）。"""
    traces = [
        {"time": "2026-11-11 12:00:00", "info": "【出口查验】出口查验已放行，船名航次变更为：MACKENZIE 21E"},
        {"time": "2026-11-10 09:00:00", "info": "【出口查验】出口查验待放行"},
    ]
    r = HE.shelf_eta(date(2026, 12, 1), "以星", traces)
    assert not r["inspected"]
    # 12-01 周二到港 → 下周五 = 12-11（只按正常班次，不加两周）
    assert r["eta"] == date(2026, 12, 11)


def test_release_after_two_week_window_takes_release():
    """放行晚于「后移两周」那天时，按放行日再顺延。"""
    released = [
        {"time": "2026-11-16 23:12:37", "info": "查验已放行，待提柜回仓"},
        {"time": "2026-10-28 15:54:24", "info": "【国外查验】美时10/27国外通知开箱例行查验："},
    ]
    r = HE.shelf_eta(date(2026, 10, 27), "以星", released)
    assert r["releaseAt"] == date(2026, 11, 16)
    # 10-27 周二到 → 下周五 11-06；后移两周 11-20（晚于放行 11-16）→ 11-20
    assert r["eta"] == date(2026, 11, 20)


def test_eta_gate_requires_arrival_and_release():
    today = date(2026, 11, 18)
    no_arrive = {"arriveDate": "", "channel": "以星", "predictedShelf": "2026-11-20",
                 "logistics": {"traces": []}}
    ok, why = HE.eta_usable(no_arrive, today)
    assert not ok and "没有到港" in why

    inspected = {"arriveDate": "2026-11-10", "channel": "以星",
                 "logistics": {"traces": [
                     {"time": "2026-11-11 10:00:00", "info": "【查验】海关查验中"}]}}
    ok, why = HE.eta_usable(inspected, today)
    assert not ok and "查验未放行" in why

    stale = {"arriveDate": "2026-10-20", "channel": "以星",
             "logistics": {"traces": [{"time": "2026-10-20 10:00:00", "info": "【到港】"}]}}
    ok, why = HE.eta_usable(stale, today)
    assert not ok and "仍未上架" in why

    good = {"arriveDate": "2026-11-16", "channel": "以星", "arriveSource": "hengxe",
            "logistics": {"traces": [{"time": "2026-11-16 10:00:00", "info": "【到港】"}]}}
    ok, why = HE.eta_usable(good, today)
    assert ok and why.startswith("已到港")


def test_eta_from_row():
    row = {"arriveDate": "2026-11-15", "channel": "美森", "shelfTime": "",
           "logistics": {"traces": []}}
    assert HE.eta_from_row(row)["eta"] == date(2026, 11, 21)
    row["shelfTime"] = "2026-11-20 12:00:00"
    assert HE.eta_from_row(row)["eta"] == date(2026, 11, 20)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
