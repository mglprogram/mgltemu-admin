#!/usr/bin/env python3
"""`presale_window.judge_window` 的口径测试。

用户 2026-11-06 原话（本版口径）：
    「你要根据这个尺码的 7 天日均，拿 7 天日均来计算，这个尺码的总库存可以卖几天，
      如果卖的天数小于等于 7 天，去货件跟踪查是否有 7 天内能上架的尺码，
      如果有即提醒我开预售。」

三分支必须分清（错一个就会让平台超卖 / 白开预售）：
    >7 天 → 现货够；≤7 天且有窗口内到货 → 可开；≤7 天但窗口内没货 → 到不了。
另外：到架日已过去的单不算窗口内（逾期没入仓 = 什么时候能卖未知）。
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "goods"))

import presale_window as W  # noqa: E402

TODAY = date(2026, 11, 5)


def batch(no, qty, eta, src="预计到架"):
    return {"no": no, "qty": qty, "eta": eta, "etaSource": src}


class JudgeWindowTest(unittest.TestCase):
    def test_no_demand(self):
        r = W.judge_window(0, 100, [], TODAY)
        self.assertEqual(r["verdict"], "无销量")

    def test_stock_covers_more_than_seven_days(self):
        # 日均 10，库存 80 → 够 8 天 > 7 → 现货够
        r = W.judge_window(10, 80, [], TODAY)
        self.assertEqual(r["verdict"], "现货够")
        self.assertEqual(r["daysOfStock"], 8.0)

    def test_exactly_seven_days_goes_to_window_check(self):
        # 正好 7 天（≤7）→ 要去看货件跟踪，不能直接判「现货够」
        r = W.judge_window(10, 70, [batch("IB1", 5, date(2026, 11, 8))], TODAY)
        self.assertEqual(r["verdict"], "可开")
        self.assertEqual(r["daysOfStock"], 7.0)

    def test_short_stock_with_arrival_in_window_is_openable(self):
        # 日均 10 库存 10（1 天），11-08 到 30 件 → 可开（不再要求覆盖缺口）
        r = W.judge_window(10, 10, [batch("IB1", 30, date(2026, 11, 8))], TODAY)
        self.assertEqual(r["verdict"], "可开")
        self.assertEqual(r["daysToArrival"], 3)
        self.assertEqual(r["arrivalQty"], 30)
        self.assertEqual(r["need7"], 60)      # 缺口只作参考显示

    def test_short_stock_no_arrival_in_window(self):
        # 日均 10 库存 10，只有 11-20 才到（>7 天）→ 到不了
        r = W.judge_window(10, 10, [batch("IB1", 500, date(2026, 11, 20))], TODAY)
        self.assertEqual(r["verdict"], "到不了")
        self.assertEqual(r["inWindow"], [])

    def test_window_edge_day_seven_counts(self):
        """第 7 天到货算窗口内（≤7 天，不含第 8 天）。"""
        r = W.judge_window(10, 0, [batch("IB1", 70, date(2026, 11, 12))], TODAY)
        self.assertEqual(r["verdict"], "可开")
        r2 = W.judge_window(10, 0, [batch("IB1", 70, date(2026, 11, 13))], TODAY)
        self.assertEqual(r2["verdict"], "到不了")

    def test_batches_without_eta_are_ignored(self):
        r = W.judge_window(10, 0, [batch("IB1", 999, None)], TODAY)
        self.assertEqual(r["verdict"], "到不了")
        self.assertEqual(r["covers"], 0)

    def test_overdue_batch_does_not_count_as_arriving(self):
        # 到架日 11-03 已过但还没入仓 → 不算窗口内，不许拿它开预售
        r = W.judge_window(10, 0, [batch("IB1", 500, date(2026, 11, 3))], TODAY)
        self.assertEqual(r["verdict"], "到不了")
        self.assertEqual(r["covers"], 0)
        self.assertEqual(r["overdueQty"], 500)
        self.assertEqual(len(r["overdue"]), 1)

    def test_multiple_batches_sum_inside_window(self):
        r = W.judge_window(
            10, 0,
            [batch("IB1", 20, date(2026, 11, 6)),
             batch("IB2", 25, date(2026, 11, 12)),
             batch("IB3", 200, date(2026, 11, 20))],
            TODAY)
        self.assertEqual(r["verdict"], "可开")
        self.assertEqual(r["covers"], 45)
        self.assertEqual(len(r["inWindow"]), 2)
        self.assertEqual(r["daysToArrival"], 1)


class EtaOfTest(unittest.TestCase):
    def test_prefers_predicted_shelf(self):
        d, src = W.eta_of({"predictedShelf": "2026-11-21", "expectedDate": "2026-11-10"})
        self.assertEqual(d, date(2026, 11, 21))
        self.assertEqual(src, "预计到架")

    def test_falls_back_to_expected(self):
        d, src = W.eta_of({"expectedDate": "2026-12-02"})
        self.assertEqual(d, date(2026, 12, 2))
        self.assertEqual(src, "预计到货")

    def test_empty_is_none(self):
        self.assertEqual(W.eta_of({}), (None, ""))

    def test_parse_variants(self):
        self.assertEqual(W._d("2026-11-21 10:00:00"), date(2026, 11, 21))
        self.assertEqual(W._d("2026/11/21"), date(2026, 11, 21))
        self.assertIsNone(W._d(""))
        self.assertIsNone(W._d("不是日期"))


class SplitInboundTest(unittest.TestCase):
    def test_joins_track_by_inbound_no(self):
        orders = [
            {"no": "IB1", "qty": 100, "inbound": 100, "expected": "2026-11-10"},
            {"no": "IB2", "qty": 50, "inbound": 50, "expected": "2026-11-20"},
        ]
        track = {"IB1": {"predictedShelf": "2026-11-12"}}
        got = W.split_inbound(orders, track)
        self.assertEqual(got[0]["eta"], date(2026, 11, 12))
        self.assertEqual(got[0]["etaSource"], "预计到架")
        # 跟踪里没有 IB2 → 退回领星 expected
        self.assertEqual(got[1]["eta"], date(2026, 11, 20))
        self.assertEqual(got[1]["etaSource"], "领星预计到货")
        # 数量用 inbound（还没到的量），不是 qty
        self.assertEqual(got[0]["qty"], 100)

    def test_uses_inbound_remaining_qty(self):
        got = W.split_inbound([{"no": "IB1", "qty": 100, "inbound": 40}], {})
        self.assertEqual(got[0]["qty"], 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
