# -*- coding: utf-8 -*-
"""test_fulfill_detached_launcher.py — 发货整链必须以**脱离进程**方式启动。

★★ 2026-11-27 实测事故（本文件守的就是它）：

    在交互式 shell 里跑 `run_temu_fulfill.py`，**四次都是**在同一位置断掉：

        [授权兜底] 先跑一次授权: run_temu_auth.py --store X
        ← 日志到此为止

    排查排除了三种假设：
      · 清障猛关页 → 已加前置清障，仍断；
      · `capture_output` 受限 → 嵌套调用实测 rc=0 正常；
      · 子进程超时（300s）→ 单独跑 us 只要 74s。

    同一条链改用 `Start-Process -WindowStyle Hidden`（子进程**没有附着控制台**）
    一次跑完：

        [授权兜底] us -> OK rc=0
        [授权兜底] cc -> OK rc=0（cookie 已落地）
        [视图] 复读确认 -> KinglamanSport local ✓
        [RESULT] ok:true failed:[] stopped_at:""

    ⇒ 生产上必须走 `scripts/tasks/run_temu_fulfill_one.cmd`（内部就是
      Start-Process -WindowStyle Hidden），不要在对话/终端里长跑。
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
# scripts/power/tests → scripts/power → scripts → PROJ
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_CMD = os.path.join(_PROJ, "scripts", "tasks", "run_temu_fulfill_one.cmd")


def _src() -> str:
    return open(_CMD, encoding="utf-8", errors="replace").read()


def test_launcher_exists():
    assert os.path.isfile(_CMD), "缺少脱离式启动器 %s" % _CMD


def test_launcher_uses_start_process_hidden():
    """必须用 Start-Process -WindowStyle Hidden —— 子进程无附着控制台才不会被回收。"""
    s = _src()
    assert "Start-Process" in s, "没用 Start-Process"
    assert "-WindowStyle Hidden" in s, "必须 Hidden（无附着控制台）"
    assert "-PassThru" in s, "要拿 PID，便于监控"


def test_launcher_redirects_logs():
    """stdout/stderr 必须落文件，否则后台跑完看不到结果。"""
    s = _src()
    assert "-RedirectStandardOutput" in s
    assert "-RedirectStandardError" in s
    assert "_logs\\fulfill-one" in s or "fulfill-one" in s, "日志目录没定义"


def test_launcher_clears_stale_proxy():
    """残留的 127.0.0.1:7897 代理会让子进程 WinError 10061（run_fulfill_daily 里记过）。"""
    s = _src()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        assert k in s, "没清 %s" % k


def test_launcher_pins_unattended():
    """无人值守：不许弹 Chrome 窗口。"""
    s = _src()
    assert "PW_UNATTENDED=1" in s
    assert "PW_HEADED=0" in s


def test_launcher_requires_store_arg():
    s = _src()
    assert "%~1" in s, "必须有 store 参数"
    assert "usage:" in s.lower(), "没给用法提示"


def test_launcher_passes_through_extra_args():
    """`--from/--to/--confirm` 等要原样透传；用临时 .ps1 避开 cmd 引号地狱。"""
    s = _src()
    assert "shift" in s, "没有参数转发循环"
    assert ".ps1" in s, "应写临时 ps1（实测直接用 -Command 拼引号会报 Unexpected token）"


def test_launcher_does_not_hardcode_confirm():
    """绝不许默认带 --confirm：那是真下单/花钱。"""
    s = _src()
    assert re.search(r"^\s*>>\s*\"%PS1%\"\s+echo.*--confirm", s, re.M) is None, \
        "启动器里硬编码了 --confirm（会真花钱）"
