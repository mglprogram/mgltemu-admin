# -*- coding: utf-8 -*-
"""头程「到港 → 送仓上架」新口径（用户 2026-11-18）单测。

三个真值例子来自用户原话：
    11.15（周天）→ 最晚 21（周六）   美森
    11.16（周一）→ 最晚 24（下周二） 以星
    11.17（周二）→ 最晚 27（下周五） 盐田
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


def test_strictly_after_arrival_week():
    """本周到港的赶不上本周的送仓 → 一律排下周（按周换班）。"""
    assert HE.shelf_eta(date(2026, 11, 14), "美森")["eta"] == date(2026, 11, 21)  # 周六到 → 下周六
    assert HE.shelf_eta(date(2026, 11, 17), "以星")["eta"] == date(2026, 11, 24)  # 周二到 → 下周二


def test_unknown_channel_falls_back():
    r = HE.shelf_eta(date(2026, 11, 15), "")
    assert r["eta"] == date(2026, 11, 25) and "渠道未知" in r["basis"]


def test_inspection_adds_two_weeks():
    traces = [
        {"time": "2026-11-03 10:00:00", "info": "【国外查验】美国海关开箱查验"},
        {"time": "2026-10-27 09:00:00", "info": "【到港】已到港"},
    ]
    r = HE.shelf_eta(date(2026, 10, 27), "以星", traces)
    assert r["inspected"] and r["releaseAt"] is None
    # 查验日 11-03 + 14 天 = 11-17（周二）→ 下一周的周二 = 11-24
    assert r["eta"] == date(2026, 11, 24)


def test_released_starts_from_release_day():
    traces = [
        {"time": "2026-11-18 10:00:00", "info": "【海关】查验放行"},
        {"time": "2026-11-04 10:00:00", "info": "【查验】海关查验中"},
    ]
    r = HE.shelf_eta(date(2026, 11, 2), "以星", traces)
    assert r["releaseAt"] == date(2026, 11, 18)
    assert r["eta"] == date(2026, 11, 24)   # 11-18 周三（当周）→ 下周周二


def test_no_arrival_returns_none():
    assert HE.shelf_eta(None, "美森")["eta"] is None


def test_eta_from_row():
    row = {"arriveDate": "2026-11-15", "channel": "美森", "shelfTime": "",
           "logistics": {"traces": []}}
    assert HE.eta_from_row(row)["eta"] == date(2026, 11, 21)
    row["shelfTime"] = "2026-11-20 12:00:00"
    assert HE.eta_from_row(row)["eta"] == date(2026, 11, 20)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
