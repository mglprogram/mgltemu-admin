# -*- coding: utf-8 -*-
"""test_quiet_detached.py — 钉死 `lib/quiet.detached()` 不再返回 `DETACHED_PROCESS`。

来历（2026-10-14 真实报障「为什么又弹出 cmd」）：
    `DETACHED_PROCESS` 的语义是「进程**完全没有控制台**」，**不是**「没有窗口」。
    父进程没有控制台时，**子孙**进程（node / Playwright driver / 另一个 python）会各自
    新建一个**可见**控制台窗口。本库 2026-09-22 记过这个现象但"没复现"（探针没带孙进程），
    2026-10-14 用带孙进程的探针稳定复现。

`detached()` 现无调用方，但它是"想要脱离父进程"的人最可能去用的入口 ——
所以在这里钉死它给的必须是 `NO_WINDOW`（有控制台、无窗口，后代继承 ⇒ 全链不弹窗）。
"""
import os
import sys

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS)

from lib import quiet as Q                          # noqa: E402


def test_detached_never_uses_detached_process():
    flags = Q.detached()
    if os.name == "nt":
        assert not (flags & Q.DETACHED_PROCESS), \
            "detached() 不能用 DETACHED_PROCESS：子孙进程会新建一个可见控制台窗口"
        assert flags & Q.NO_WINDOW, "detached() 必须带 NO_WINDOW"
    else:
        assert flags == 0


def test_no_window_and_detached_are_documented_as_exclusive():
    """NO_WINDOW 与 DETACHED_PROCESS 同用时 NO_WINDOW 会被忽略 ⇒ detached() 不该两个都给。"""
    flags = Q.detached()
    assert not (flags & Q.NO_WINDOW and flags & Q.DETACHED_PROCESS), \
        "两者同时给没有意义（NO_WINDOW 会被忽略）"


def test_hidden_flags_are_not_detached():
    """hidden_* 走的是"隐藏控制台"路线，同样不该带 DETACHED_PROCESS。"""
    flags = Q.hidden_flags()
    if os.name == "nt":
        assert not (flags & Q.DETACHED_PROCESS)
