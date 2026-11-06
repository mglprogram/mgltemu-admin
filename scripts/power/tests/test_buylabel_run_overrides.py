# -*- coding: utf-8 -*-
"""test_buylabel_run_overrides.py — 按次口径覆盖（--warehouse / --carriers）的纯函数测试。

背景（2026-11-05 用户口径）：BEL 同批订单按库存拆成两份，两份的买面单参数**不同**：
  · 帕沃尔那份 → 发货仓「加州3仓」，物流商不限定（默认全部）
  · 恒信那份   → 发货仓「恒信加州3仓」，物流商范围要多选燕文
店级口径 `stores.BEL` 装不下「同店两套」，必须支持按次覆盖；否则只能改 json 跑完再改回来。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(os.path.dirname(_HERE), "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import temu_buylabel_driver as D  # noqa: E402


BASE = {
    "warehouse_default": "加州三仓",
    "logistics": {"strategy": "配送成本优先", "carriers": [], "max_quote": ""},
}


def test_no_override_keeps_cfg():
    out = D.apply_run_overrides(BASE, warehouse="", carriers=None)
    assert out["warehouse_default"] == "加州三仓"
    assert out["logistics"]["carriers"] == []


def test_warehouse_override():
    out = D.apply_run_overrides(BASE, warehouse="恒信加州3仓")
    assert out["warehouse_default"] == "恒信加州3仓"


def test_carriers_override_adds_yanwen():
    out = D.apply_run_overrides(BASE, warehouse="", carriers=["USPS", "YANWEN Express"])
    assert out["logistics"]["carriers"] == ["USPS", "YANWEN Express"]


def test_empty_carriers_list_means_platform_default():
    """显式给空列表＝用页面默认「全部物流商」，不能被当成「没给」而沿用旧值。"""
    cfg = dict(BASE, logistics={"strategy": "配送成本优先", "carriers": ["USPS"]})
    out = D.apply_run_overrides(cfg, warehouse="", carriers=[])
    assert out["logistics"]["carriers"] == []


def test_both_together_for_hx_batch():
    out = D.apply_run_overrides(BASE, warehouse="恒信加州3仓", carriers=["YANWEN Express"])
    assert out["warehouse_default"] == "恒信加州3仓"
    assert out["logistics"]["carriers"] == ["YANWEN Express"]


def test_does_not_mutate_input():
    cfg = {"warehouse_default": "加州三仓",
           "logistics": {"strategy": "配送成本优先", "carriers": ["USPS"]}}
    D.apply_run_overrides(cfg, warehouse="kansas仓", carriers=["GOFO"])
    assert cfg["warehouse_default"] == "加州三仓"
    assert cfg["logistics"]["carriers"] == ["USPS"]


def test_carrier_names_are_stripped_and_blank_dropped():
    out = D.apply_run_overrides(BASE, warehouse="", carriers=[" USPS ", "", "YANWEN Express "])
    assert out["logistics"]["carriers"] == ["USPS", "YANWEN Express"]


# ---------------------------------------------------------------- 与分店覆盖的顺序


def test_run_override_wins_over_store_override():
    """先按店覆盖、再按次覆盖 —— 本次参数必须压过店级默认。"""
    cfg = {"warehouse_default": "顶仓",
           "logistics": {"strategy": "x", "carriers": []},
           "stores": {"BEL": {"warehouse_default": "加州三仓"}}}
    cfg2, _ = D.apply_store_overrides(cfg, "BEL")
    assert cfg2["warehouse_default"] == "加州三仓"
    cfg3 = D.apply_run_overrides(cfg2, warehouse="恒信加州3仓")
    assert cfg3["warehouse_default"] == "恒信加州3仓"
