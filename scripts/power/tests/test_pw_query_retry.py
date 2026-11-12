# -*- coding: utf-8 -*-
"""test_pw_query_retry.py — 帕沃尔整批查询的重试 / 页面锁 / 延迟复核（纯逻辑，不开浏览器）。

钉的是 2026-11-12 真机那一轮：
  · 帕沃尔是**单实例单页**（`PORT=9333`），每日任务并行跑多个店 ⇒ 我们粘贴的单号被
    别的店冲掉，`listCallback` 回来的是整店默认列表（`record_count=61`，本批才 2 单）。
    旧逻辑一次不中就 `[FATAL]` 整批不下单 ⇒ BHY 61/61 + BEL 22/22 + BAN 2/2 全灭。
  · 下单回执「下单成功」但列表更新慢（BS `GSU1AU28U001T97`），当场回读还是「未下单」，
    十分钟后才是「已下单」——只看一眼就判失败会把成功的单塞进失败表。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import pw_hwc_flow as F  # noqa: E402


def _rows(*pos):
    return [{"po": p, "id": str(i), "status": "已下单", "err": "", "wh": "", "qd": ""}
            for i, p in enumerate(pos)]


# ---------------------------------------------------------------- 查询重试


def test_query_retries_when_list_is_not_our_batch(monkeypatch):
    """第 1 次拿到整店默认列表（不含本批）→ 重贴单号再查，第 2 次拿到本批就成功。"""
    calls = []

    def fake_once(pg, pos, shop_id="", wait=25.0, fast=False):
        calls.append(list(pos))
        if len(calls) == 1:
            raise F.QueryStale("查询没刷新出结果｜rows:63 cb:1 cnt:61")
        return _rows("PO-1", "PO-2")

    monkeypatch.setattr(F, "_do_query_batch_once", fake_once)
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    monkeypatch.setattr(F, "arm", lambda pg: None)
    out = F._do_query_batch(None, ["PO-1", "PO-2"], attempts=3)
    assert [r["po"] for r in out] == ["PO-1", "PO-2"]
    assert len(calls) == 2, calls


def test_query_raises_fatal_only_after_all_attempts(monkeypatch):
    """每次都只有别人的单 ⇒ 重试用尽才 FATAL，且消息里带页面实况。"""
    n = []

    def fake_once(pg, pos, shop_id="", wait=25.0, fast=False):
        n.append(1)
        raise F.QueryStale("[FATAL] 查询没刷新出结果｜页面实况 {\"rows\":63,\"cb\":1,\"cnt\":\"61\"}")

    monkeypatch.setattr(F, "_do_query_batch_once", fake_once)
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    monkeypatch.setattr(F, "arm", lambda pg: None)
    try:
        F._do_query_batch(None, ["PO-1"], attempts=3)
    except SystemExit as ex:
        assert "页面实况" in str(ex) and "63" in str(ex), str(ex)
    else:
        raise AssertionError("重试用尽必须 FATAL，不许当成成功了")
    assert len(n) == 3, n


def test_query_attempts_env_override(monkeypatch):
    """`PW_QUERY_ATTEMPTS` 可调（真机排查时想少试几次）。"""
    seen = []

    def fake_once(pg, pos, shop_id="", wait=25.0, fast=False):
        seen.append(1)
        raise F.QueryStale("[FATAL] 没拿到本批")

    monkeypatch.setattr(F, "_do_query_batch_once", fake_once)
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    monkeypatch.setattr(F, "arm", lambda pg: None)
    monkeypatch.setenv("PW_QUERY_ATTEMPTS", "2")
    try:
        F._do_query_batch(None, ["PO-1"])
    except SystemExit:
        pass
    assert len(seen) == 2, seen


def test_stale_exception_is_not_systemexit():
    """重试靠 `QueryStale` 区分「可以再试」与「真的崩了」，别混成 SystemExit。"""
    assert not issubclass(F.QueryStale, SystemExit)


# ---------------------------------------------------------------- 页面锁


def test_pw_lock_key_is_shared_by_all_stores():
    """所有店拿同一把 `PW` 锁（帕沃尔只有一个页面，键必须与店无关）。"""
    import run_pw_fulfill as R  # noqa: PLC0415  （同目录）

    src = open(R.__file__, encoding="utf-8").read()
    assert 'os.environ.get("PW_LOCK_KEY", "PW")' in src, \
        "帕沃尔锁键必须是固定的 PW（不同店不同键＝没锁）"
    assert "_pw_lock" in src and "_run_steps" in src


def test_late_recheck_constant_exists():
    """延迟复核的秒数必须是个可读常量（真机调过：60s 不够，列表滞后约十分钟）。"""
    assert isinstance(F.LATE_RECHECK_S, int) and F.LATE_RECHECK_S >= 60
