# -*- coding: utf-8 -*-
"""到港日期解析：预报被查验作废 + 连字符 MM-DD（用户 2026-11-19 口径）。

用户原话：
「碰到最新物流节点，出现国内外海关查验，这个预计到港就不准确了，要清除预计的
  到港日期，等最新的节点出现最新的离港和到港时间」
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "goods" / "fetch_shipment_track.py"
spec = importlib.util.spec_from_file_location("fetch_shipment_track", SRC)
FST = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FST)


def test_forecast_voided_by_later_inspection():
    """预报之后（更新位置）出现查验 ⇒ 不给到港日，标记 voided。"""
    traces = [
        {"time": "2026-10-20 15:05:09",
         "info": "您好 因海关出口例行查验，目前等待海关放行，我们这边会实时跟进好进度"},
        {"time": "2026-10-18 22:30:58",
         "info": "已装柜完成，船名航次：ZIM ALBATROSS/18/E，预计10/21开船，预计11/03到港"},
    ]
    arrive, kind = FST.arrive_of(traces)
    assert arrive is None and kind == ""
    assert FST.forecast_voided(traces) is True


def test_new_eta_after_inspection_wins():
    """查验之后货代又给了更新的 ETA ⇒ 那条更新、排在查验前面，仍然采信。"""
    traces = [
        {"time": "2026-11-16 11:55:14",
         "info": "您好 已放行，新船名航次：MACKENZIE/21/E，预计11-18号开船，预计12-01到港"},
        {"time": "2026-11-10 09:50:02",
         "info": "您好 因海关出口例行查验，目前等待海关放行"},
    ]
    arrive, kind = FST.arrive_of(traces)
    assert arrive == date(2026, 12, 1) and kind == "forecast"
    assert FST.forecast_voided(traces) is False


def test_dash_month_day_parsed():
    """`预计12-01到港` 这种连字符写法必须能解析（以前会退回节点录入时间）。"""
    traces = [{"time": "2026-11-18 00:31:00",
               "info": "11/18已开船，预计12/01到港，具体以实际到港为准。"}]
    arrive, kind = FST.arrive_of(traces)
    assert arrive == date(2026, 12, 1) and kind == "forecast"

    traces2 = [{"time": "2026-11-18 00:31:00",
                "info": "11/18已开船，预计12-01到港，具体以实际到港为准。"}]
    assert FST.arrive_of(traces2)[0] == date(2026, 12, 1)


def test_full_date_not_reparsed_as_dash():
    """`预计到港时间为美国时间2026-12-01` 不能被再拆成 12-01 之类的重复候选。"""
    traces = [{"time": "2026-11-18 00:31:00",
               "info": "预计到港时间为美国时间2026-12-01 00:00:00"}]
    assert FST.arrive_of(traces)[0] == date(2026, 12, 1)


def test_real_arrival_beats_voided_forecast():
    """真的到港节点永远优先，哪怕轨迹里也有查验。"""
    traces = [
        {"time": "2026-11-16 23:12:37", "info": "查验已放行，待提柜回仓"},
        {"time": "2026-10-28 15:54:24", "info": "【国外查验】美时10/27国外通知开箱例行查验："},
        {"time": "2026-10-28 13:59:58", "info": "美时10/27已到港，等待清关放行；"},
        {"time": "2026-10-15 11:18:29", "info": "10/14已开船，预计10/27到港"},
    ]
    arrive, kind = FST.arrive_of(traces)
    assert arrive == date(2026, 10, 27) and kind == "track"
    assert FST.forecast_voided(traces) is False


def test_plain_forecast_still_works():
    traces = [{"time": "2026-11-05 20:55:11",
               "info": "11/05已开船，预计11/16到港，具体以实际到港为准。"}]
    assert FST.arrive_of(traces) == (date(2026, 11, 16), "forecast")
    assert FST.forecast_voided(traces) is False


# ------------------------------------------------- 查验之后货代给了新 ETA：采信新的那条
def test_new_eta_in_vessel_node_after_inspection():
    """`【开船】… ATD：11/18 | ETA：12/01` 是查验之后给的新到港时间，必须采信。"""
    traces = [
        {"time": "2026-11-18 02:06:15", "info": "【开船】已于从CNYTN起运，ATD：11/18 | ETA：12/01"},
        {"time": "2026-11-11 12:20:00", "info": "【出口查验】出口查验已放行，船名航次变更为：MACKENZIE 21E"},
        {"time": "2026-11-10 18:11:29", "info": "【出口查验】出口查验待放行"},
        {"time": "2026-11-08 22:27:07", "info": "【装柜】船名航次：ZIM DIAMOND - 11/E，ETD：11/10 ｜ ETA：11/24"},
    ]
    assert FST.arrive_of(traces) == (date(2026, 12, 1), "forecast")
    assert FST.forecast_voided(traces) is False


def test_delay_node_with_new_eta_after_inspection():
    """`【抵港延迟】新ETA: 2026-11-19` 同样算最新的到港预报。"""
    traces = [
        {"time": "2026-11-17 10:00:00", "info": "【清关】货件已于11/15清关放行"},
        {"time": "2026-11-12 10:00:00", "info": "【抵港延迟】新ETA: 2026-11-19"},
        {"time": "2026-11-04 10:00:00", "info": "【开船】已于从CNNGB起运，ATD：11/04 | ETA：11/15"},
        {"time": "2026-11-04 09:00:00", "info": "【出口查验放行】原定于11月3日开船的柜子遇到出口查验"},
    ]
    assert FST.arrive_of(traces) == (date(2026, 11, 19), "forecast")
    assert FST.forecast_voided(traces) is False


def test_etd_is_not_treated_as_eta():
    """`ETD：11-18` 是离港日，不能当到港预报。"""
    traces = [{"time": "2026-10-30 10:00:00",
               "info": "【装柜】船名航次：CSCL INDIAN OCEAN - 080E，ETD：11/05"}]
    assert FST.arrive_of(traces) == (None, "")


# --------------------------------------- 国内查验后重新装柜：旧船期的「到港」要作废
def test_old_voyage_arrival_is_ignored_after_reload():
    """IB106261013RT：11-05 报的「11-03 到港」是旧船期，之后 11-08 又重新装柜、11-12 开船。

    用户原话：「这种国内海关查验的情况，预计的到港时间要重新计算，
    根据给出的 ATD：11/11 | ETA：11/22；而不是一直固定一个到港日期和预测上架的日期」。
    """
    traces = [
        {"time": "2026-11-12 11:22:05", "info": "【开船】已于从CNSHA起运，ATD：11/11 | ETA：11/22"},
        {"time": "2026-11-10 14:22:35", "info": "【报关】已于11/10报关放行"},
        {"time": "2026-11-08 04:19:39", "info": "【装柜】船名航次：KAIMANA HILA O78E，ETD：11/11 ｜ ETA：11/22"},
        {"time": "2026-11-05 10:40:17", "info": "【到港】已于11/03抵达USLSA，等待卸柜中"},
        {"time": "2026-10-21 16:55:23", "info": "【起运】ATD: 2026/10/21，ETA :2026/11/07"},
        {"time": "2026-10-20 17:08:21", "info": "【开船】已于从CNYTN起运，ATD：10/21 | ETA：11/03"},
    ]
    arrive, kind = FST.arrive_of(traces)
    assert arrive == date(2026, 11, 22) and kind == "forecast"
    assert FST.forecast_voided(traces) is False


def test_normal_arrival_still_wins_without_reload():
    """没重新装柜的票，真实到港照常优先。"""
    traces = [
        {"time": "2026-11-14 10:05:44", "info": "【到港】已于11/13抵达USLSA，等待卸柜中"},
        {"time": "2026-11-08 10:00:00", "info": "【装柜】ETD：11/05 ｜ ETA：11/13"},
    ]
    assert FST.arrive_of(traces) == (date(2026, 11, 13), "track")
    assert FST.forecast_voided(traces) is False


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
