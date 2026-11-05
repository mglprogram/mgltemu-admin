#!/usr/bin/env python3
"""`presale_window.judge_window` 的口径测试（2026-11-05 用户新要求）。

用户原话：「有记录每个产品对应尺码的7天日均，你要根据7天日均和代发库存，
鼠标悬浮在代发途中可以看到入库单号，拿这个单号去货件跟踪查询，
计算能不能开一个小于等于7天的预售」。

四条判定必须分清楚（错一个就会让平台超卖 / 白开预售）：
  现货够 / 到不了 / 量不够 / 可开。
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

    def test_stock_covers_seven_days(self):
        # 日均 10，库存 80 → 够 8 天，不用开
        r = W.judge_window(10, 80, [], TODAY)
        self.assertEqual(r["verdict"], "现货够")
        self.assertEqual(r["need7"], 0)

    def test_no_arrival_in_window(self):
        # 日均 10 库存 10（够 1 天），只有 11-20 才到 → 7 天内到不了
        r = W.judge_window(10, 10, [batch("IB1", 500, date(2026, 11, 20))], TODAY)
        self.assertEqual(r["verdict"], "到不了")
        self.assertEqual(r["inWindow"], [])

    def test_arrival_but_not_enough(self):
        # 缺口 = 70-10 = 60；7 天内只到 30 → 仍断档
        r = W.judge_window(10, 10, [batch("IB1", 30, date(2026, 11, 8))], TODAY)
        self.assertEqual(r["verdict"], "量不够")
        self.assertEqual(r["need7"], 60)
        self.assertEqual(r["covers"], 30)

    def test_arrival_enough(self):
        # 缺口 60；7 天内到 60 → 可开
        r = W.judge_window(10, 10, [batch("IB1", 60, date(2026, 11, 9))], TODAY)
        self.assertEqual(r["verdict"], "可开")
        self.assertEqual(r["daysToArrival"], 4)
        self.assertEqual(r["arrivalQty"], 60)

    def test_multiple_batches_sum_inside_window(self):
        # 只有窗口内的两单算，窗口外那单不算
        r = W.judge_window(
            10, 0,
            [batch("IB1", 20, date(2026, 11, 6)),
             batch("IB2", 25, date(2026, 11, 12)),
             batch("IB3", 200, date(2026, 11, 20))],
            TODAY)
        self.assertEqual(r["verdict"], "量不够")     # 缺口 70，窗口内只 45
        self.assertEqual(r["covers"], 45)
        self.assertEqual(len(r["inWindow"]), 2)
        self.assertEqual(r["daysToArrival"], 1)

    def test_zero_stock_exact_window_edge(self):
        """第 7 天到货算窗口内（≤7 天，不含第 8 天）。"""
        r = W.judge_window(10, 0, [batch("IB1", 70, date(2026, 11, 12))], TODAY)
        self.assertEqual(r["verdict"], "可开")
        r2 = W.judge_window(10, 0, [batch("IB1", 70, date(2026, 11, 13))], TODAY)
        self.assertEqual(r2["verdict"], "到不了")

    def test_batches_without_eta_are_ignored(self):
        r = W.judge_window(10, 0, [batch("IB1", 999, None)], TODAY)
        self.assertEqual(r["verdict"], "到不了")
        self.assertEqual(r["covers"], 0)


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
