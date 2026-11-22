# -*- coding: utf-8 -*-
"""test_export_page_ready.py — 「页面就绪哨兵」的回归钉（纯离线）。

★ 2026-11-22 用户令：KLS 每天 04:00 导出失败，诊断结论是**页面还没渲染完就开始找
  全选框/导出按钮**（同一命令 11 点手跑正常、04:00 读到 `{}`）。
  修法＝`temu_export_by_select.wait_page_ready()`：轮询 DOM 直到
  「待发货(N) 页签 + 表格有行 + 导出按钮」齐了再往下走，不再靠固定 sleep 赌时序。

这里不连浏览器：用假的 page 对象喂不同「渲染进度」，验哨兵会不会等到、会不会超时、
以及超时后是否**如实返回未就绪**（不许假装成功）。
"""
import importlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
for p in (_POWER, os.path.join(_POWER, "lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

import temu_export_by_select as E  # noqa: E402


class _FakePage:
    """按序列返回探测结果的假页面；序列用尽后一直返回最后一个。"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def evaluate(self, js):
        i = min(self.calls, len(self.seq) - 1)
        self.calls += 1
        v = self.seq[i]
        if isinstance(v, Exception):
            raise v
        return v


def _state(tab="", rows=0, btn=False, err=False):
    import json
    return json.dumps({"tab": tab, "rows": rows, "btn": btn, "err": err,
                       "href": "https://x/orders.html"})


def test_waits_until_rendered(monkeypatch):
    """前两次只有空壳，第三次才齐 ⇒ 必须等到第三次并返回 ready=True。"""
    monkeypatch.setattr(E.time, "sleep", lambda s: None)
    pg = _FakePage([_state(), _state(rows=1), _state(tab="待发货(9)", rows=9, btn=True)])
    out = E.wait_page_ready(pg, log=lambda *a, **k: None, timeout=60)
    assert out["ready"] is True
    assert out["tab"] == "待发货(9)" and out["rows"] == 9
    assert pg.calls == 3, pg.calls


def test_tolerates_evaluate_errors_while_navigating(monkeypatch):
    """导航中的 evaluate 会抛异常 —— 哨兵必须吞掉并继续等，不能把导出带崩。"""
    monkeypatch.setattr(E.time, "sleep", lambda s: None)
    pg = _FakePage([RuntimeError("Execution context was destroyed"),
                    _state(tab="待发货(3)", rows=3, btn=True)])
    out = E.wait_page_ready(pg, log=lambda *a, **k: None, timeout=30)
    assert out["ready"] is True and out["rows"] == 3


def test_timeout_reports_not_ready(monkeypatch):
    """一直不齐 ⇒ 超时后必须如实 `ready=False`（调用方照旧判失败），不许假装成功。"""
    ticks = {"t": 0.0}
    monkeypatch.setattr(E.time, "sleep", lambda s: ticks.__setitem__("t", ticks["t"] + s))
    monkeypatch.setattr(E.time, "time", lambda: ticks["t"])
    pg = _FakePage([_state()])
    out = E.wait_page_ready(pg, log=lambda *a, **k: None, timeout=10)
    assert out["ready"] is False


def test_need_btn_false_accepts_tab_and_rows(monkeypatch):
    """切页签后只等「页签 + 行」，不要求导出按钮（按钮可能还没渲染出来）。"""
    monkeypatch.setattr(E.time, "sleep", lambda s: None)
    pg = _FakePage([_state(tab="待发货(5)", rows=5, btn=False)])
    out = E.wait_page_ready(pg, log=lambda *a, **k: None, timeout=5, need_btn=False)
    assert out["ready"] is True


def test_need_btn_true_rejects_missing_button(monkeypatch):
    """主流程必须要求导出按钮存在 —— 这正是 KLS 那次读不到的东西。"""
    ticks = {"t": 0.0}
    monkeypatch.setattr(E.time, "sleep", lambda s: ticks.__setitem__("t", ticks["t"] + s))
    monkeypatch.setattr(E.time, "time", lambda: ticks["t"])
    pg = _FakePage([_state(tab="待发货(5)", rows=5, btn=False)])
    out = E.wait_page_ready(pg, log=lambda *a, **k: None, timeout=6)
    assert out["ready"] is False


def test_ready_timeout_env_override(monkeypatch):
    """等待上限可用环境变量调（凌晨窗口慢时可以放大）。"""
    monkeypatch.setenv("TEMU_EXPORT_READY_TIMEOUT", "42")
    m = importlib.reload(E)
    try:
        assert m.READY_TIMEOUT == 42.0
    finally:
        monkeypatch.delenv("TEMU_EXPORT_READY_TIMEOUT", raising=False)
        importlib.reload(E)
