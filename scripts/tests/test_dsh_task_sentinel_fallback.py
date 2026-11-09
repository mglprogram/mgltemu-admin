# -*- coding: utf-8 -*-
"""钉住 dsh_task 的「哨兵回落」判据。

背景（2026-11-08 实测事故）：TEMU-Fulfill-Daily 的会话工作区是
`D:\\AI-workplace\\发货`，哨兵根被搬进那个工作区；但 agent 在沙箱里自己调
`dsh_run.py` 时没继承 `DSH_SENTINEL_ROOT`，于是 attempts.jsonl 被写回了
`TEMU\\_logs\\dsh-sentinel`。主目录读到空 ⇒ rc=71「等于没执行」，
**业务真跑的 rc=42 被丢掉了**。

回落能救人，但那个文件**不会被 reset_sentinel 清**，所以必须只认本轮写的记录 ——
否则昨天那次失败的 rc 会被当今天的结果报出去。这条就是钉这个边界。
"""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_PROJ, "scripts"))

import dsh_task as T  # noqa: E402


def _iso(ts: float) -> str:
    return T._dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def test_attempt_from_this_run_is_adopted():
    now = time.time()
    assert T._attempt_after({"finished": _iso(now - 3), "rc": 42}, now - 60) is True


def test_stale_attempt_from_a_previous_run_is_ignored():
    """上一轮（昨天）留下的 rc=0 绝不能当成今天的结果。"""
    now = time.time()
    assert T._attempt_after({"finished": _iso(now - 86400), "rc": 0}, now - 60) is False


def test_unparseable_timestamp_is_not_trusted():
    assert T._attempt_after({"rc": 0}, 0.0) is False
    assert T._attempt_after({"finished": "not-a-time", "rc": 0}, 0.0) is False
