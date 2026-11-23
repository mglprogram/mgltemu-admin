# -*- coding: utf-8 -*-
"""test_sku_weight.py — 买面单「包裹重量」取值口径的回归钉。

★ 2026-11-23 用户口径：平台会校验「包裹重量 vs 包裹内 SKU 总重量」，
  差太多直接不让下单（BHY 那张单真实值 1.76 lb，而基准 1.5×2=3.00 或 1.1×2=2.20 都不对）。
  优先级：**平台红字 > 表内实重求和 > 基准×件数**；缺 SKU 要**明确报出来**，不许静默。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
if _POWER not in sys.path:
    sys.path.insert(0, _POWER)

import sku_weight as W  # noqa: E402


def _isolate(monkeypatch, table):
    monkeypatch.setattr(W, "_CACHE", {k.upper(): v for k, v in table.items()},
                        raising=True)


def test_all_known_uses_sum(monkeypatch):
    _isolate(monkeypatch, {"BHY-A-XL": 0.8, "BHY-B-M": 0.96})
    w, src, miss = W.package_weight([{"sku": "BHY-A-XL", "qty": 1},
                                     {"sku": "BHY-B-M", "qty": 1}], base_lb=1.5)
    assert (w, src, miss) == (1.76, "表内实重", [])


def test_qty_multiplies(monkeypatch):
    _isolate(monkeypatch, {"X": 0.5})
    w, src, _ = W.package_weight([{"sku": "X", "qty": 3}], base_lb=1.5)
    assert w == 1.5 and src == "表内实重"


def test_missing_falls_back_and_reports(monkeypatch):
    """一个都不认识 ⇒ 基准×件数，且把缺的 SKU 全部报出来。"""
    _isolate(monkeypatch, {})
    w, src, miss = W.package_weight([{"sku": "BHY-UNKNOWN-L", "qty": 2}], base_lb=1.1)
    assert w == 2.2 and src == "基准×件数"
    assert miss == ["BHY-UNKNOWN-L"]


def test_partial_known_mixes(monkeypatch):
    """一部分认识 ⇒ 认识的按实重、其余按基准补，标「部分实重+基准」。"""
    _isolate(monkeypatch, {"KNOWN": 1.0})
    w, src, miss = W.package_weight([{"sku": "KNOWN", "qty": 1},
                                     {"sku": "NOPE", "qty": 1}], base_lb=0.5)
    assert w == 1.5 and src == "部分实重+基准" and miss == ["NOPE"]


def test_hint_wins(monkeypatch):
    """平台红字给的磅数最准，优先级最高。"""
    _isolate(monkeypatch, {"A": 9.9})
    w, src, _ = W.package_weight([{"sku": "A", "qty": 1}], base_lb=1.5, hint_lb=1.76)
    assert (w, src) == (1.76, "平台红字")


def test_case_insensitive(monkeypatch):
    _isolate(monkeypatch, {"bhy-abc-xl": 0.7})
    w, _, _ = W.package_weight([{"sku": "BHY-ABC-XL", "qty": 1}], base_lb=1.5)
    assert w == 0.7
