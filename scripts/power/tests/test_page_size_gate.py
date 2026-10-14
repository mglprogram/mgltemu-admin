# -*- coding: utf-8 -*-
"""test_page_size_gate.py — 「每页条数」闸门的离线回归（纯函数，不开浏览器）。

来历（2026-09-17 用户定，原话「我要你保证首先能够跑的通」+ 现场查证）：
    订单列表默认每页 20 行。列表「每页」调不成功时，`全选` 只勾当前页 ⇒ **只买 20 单**，
    而账面看起来像是跑完了。实测状态串有四种：`already:N` / `set:N` / `no-sc:*` / `no-option:*`。
    现在 >20 单而没调成功 → **拒绝硬跑**（抛错），<=20 单则放行（默认页装得下）。

运行：python -m pytest scripts/power/tests/test_page_size_gate.py -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "lib"))

from temu_buylabel_driver import page_size_status_ok, page_size_suspect, pick_page_size_option  # noqa: E402

OPTS = [{"text": t} for t in ("5", "10", "20", "50", "100", "500")]


def test_set_must_cover_whole_batch():
    assert page_size_status_ok("set:50", 27)[0] is True
    assert page_size_status_ok("already:20", 20)[0] is True
    assert page_size_status_ok("already:20", 27)[0] is False, "20 装不下 27 单，必须拦"
    assert page_size_status_ok("set:20", 21)[0] is False


def test_unknown_status_only_safe_within_default_page():
    for st in ("no-sc:no-sc", "no-option:选项=", "no-viewport:1200,900", ""):
        assert page_size_status_ok(st, 7)[0] is True, "%s 本批 7 单（≤默认 20）应放行" % st
        assert page_size_status_ok(st, 27)[0] is False, "%s 本批 27 单必须拦" % st


def test_page_option_cap_beyond_options_is_blocked():
    """>500 单：页面最大选项就是 500 → set:500 < need → 拦下并要求拆表（不许只买 500）。"""
    assert pick_page_size_option(OPTS, 600, cap=500) == "500"
    ok, why = page_size_status_ok("set:500", 600)
    assert ok is False and "拆表" in why


def test_pick_always_takes_the_max_option():
    """2026-09-17 用户定：「直接拉到500，你不需要判断别的」→ 一律取最大（≤cap），不看 need。"""
    assert pick_page_size_option(OPTS, 27) == "500"
    assert pick_page_size_option(OPTS, 20) == "500"
    assert pick_page_size_option(OPTS, 3) == "500", "哪怕本批只有 3 单也拉满"
    assert pick_page_size_option(OPTS, 999) == "500", "超过 500 也只能选到 500（闸门会拦）"
    assert pick_page_size_option([{"text": "5"}, {"text": "10"}], 7) == "10", "没有 500 就取可选最大值"
    assert pick_page_size_option([], 5) is None


def test_pipeline_records_the_page_size_rule():
    """口径必须落在 pipeline 定义里（用户 2026-09-17：「这个要记录在目前的 pipeline 里面」）。"""
    import json
    import os
    p = os.path.join(os.path.dirname(_HERE), "data", "temu_buylabel_pipeline.json")
    cfg = json.load(open(p, encoding="utf-8"))
    rule = cfg.get("_页大小口径") or ""
    assert "500" in rule and "拉满" in rule, "pipeline 未记录「每页拉满 500」口径"
    fill = next(s for s in cfg["steps"] if s["name"] == "fill")
    assert "拉满 500" in (fill.get("_做什么") or ""), "fill 步骤也要点名这条口径"


def test_default_page_is_configurable():
    """页面默认值来自口径 `batch.page_default`（实测 20），不是写死的魔法数。"""
    assert page_size_status_ok("no-sc:no-sc", 20, default=20)[0] is True
    assert page_size_status_ok("no-sc:no-sc", 21, default=20)[0] is False
    assert page_size_status_ok("no-sc:no-sc", 60, default=100)[0] is True


def test_suspect_needs_row_evidence_not_just_bad_status():
    """关键：`no-sc` 不能一律当页大小问题——0 行是登录态/页面未就绪，5 行是真缺单。

    2026-09-17 现场：BEL 登录态失效 → 列表 0 行 → no-sc，驱动分诊成 auth_required（正确）。
    只有「行数正好一页（=20）而本批更多」才是页大小被截断。
    """
    assert page_size_suspect(20, 27, "no-sc:no-sc") is True, "正好一页+本批更多 → 疑似截断"
    assert page_size_suspect(20, 27, "already:20") is True, "确认当前每页=20 → 截断无疑"
    assert page_size_suspect(0, 27, "no-sc:no-sc") is False, "0 行 = 登录态/未就绪，不是页大小"
    assert page_size_suspect(5, 27, "no-sc:no-sc") is False, "没填满默认页 → 真缺单"
    assert page_size_suspect(27, 27, "set:50") is False, "够数就不该报"
    assert page_size_suspect(20, 20, "already:20") is False, "本批就是 20 单，没问题"
