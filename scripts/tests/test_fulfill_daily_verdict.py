# -*- coding: utf-8 -*-
"""钉住每日发货整链的「软失败不该判红」口径（用户 2026-11-12）。

事实来源：2026-11-12 04:00 那一轮，BHY/BEL/BS 三店 rc=0，BAN 只是 **软失败**
（`label_shein` 买不了面单，1 单 SHEIN 平台条件未满足），`hard_failed` 为空、
门店自己判 `ok: true`，但汇总按「非零即 bad」把整链打了 exit 1 ⇒ 用户收到一条
「运行失败」告警，而实际上主体发货和报表都完成了。

新口径：只有软失败 ⇒ rc=3（带告警成功，面板琥珀色、不推失败告警）。
硬失败（导出就挂 / 抛异常 / 没有 summary）仍然 rc=1 / 42。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_PROJ, "scripts", "tasks"))

import run_fulfill_daily_inner as F  # noqa: E402


def _ok(store="BHY"):
    return {"store": store, "ok": True, "rc": 0,
            "summary": {"failed": [], "soft_failed": [], "hard_failed": []}}


def _soft(store="BAN"):
    return {"store": store, "ok": False, "rc": 42,
            "summary": {"failed": ["label_shein"], "soft_failed": ["label_shein"],
                        "hard_failed": [], "ok": True}}


def _hard(store="BEL", why="export_orders"):
    return {"store": store, "ok": False, "rc": 42,
            "summary": {"failed": [why], "soft_failed": [], "hard_failed": [why]}}


def test_all_green_is_zero():
    assert F.verdict_code([_ok(), _ok(), _ok()]) == 0


def test_soft_only_is_amber_three():
    """★ 2026-11-12 现场：三店绿 + BAN 只有软失败 ⇒ 必须是 3，不是 1。"""
    res = [_ok("BHY"), _ok("BEL"), _ok("BS"), _soft("BAN")]
    assert F.verdict_code(res) == 3


def test_hard_failure_still_red_one():
    res = [_ok("BHY"), _hard("BEL"), _soft("BAN")]
    assert F.verdict_code(res) == 1


def test_store_without_summary_counts_as_hard():
    """导出就挂 / 抛异常 ⇒ 没有 summary ⇒ 这店压根没跑起来，不能算软。"""
    res = [_ok(), {"store": "BEL", "ok": False, "rc": 1, "stage": "exception"}]
    assert F.verdict_code(res) == 1


def test_everything_hard_is_42():
    assert F.verdict_code([_hard("A"), _hard("B")]) == 42


def test_soft_does_not_mask_a_total_hard_failure():
    """所有店都硬失败时，即便其中一个被标成软，也必须是 42。"""
    res = [_hard("A"), _hard("B"), _soft("C")]
    assert F.verdict_code(res) == 42


def test_is_soft_only_definition():
    assert F.is_soft_only(_soft()) is True
    assert F.is_soft_only(_hard()) is False
    assert F.is_soft_only(_ok()) is False
