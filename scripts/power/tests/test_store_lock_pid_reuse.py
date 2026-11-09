# -*- coding: utf-8 -*-
"""test_store_lock_pid_reuse.py — 同店窗口锁：PID 被复用后不能永远"看起来在跑"。

来历（2026-11-09 实测 BEL.lock）
-------------------------------
锁文件里记着 `pid=7308`，那个进程早就退了；Windows 把这个 PID 分给了
`WmiPrvSE.exe`（系统服务），于是 `_pid_alive(7308)` 返回 **True** ——
一把死锁看起来"还在跑"，只能等 60 分钟判超时才被接管。
这期间 BEL 的所有窗口作业都会被 `[HARD STOP] 同店窗口占用` 挡掉。

修法：锁记录里多存一份**进程启动时间**（`pid_start`）。
PID 相同但启动时间对不上 ⇒ 说明号被复用，原进程已死 ⇒ 判陈旧、可接管。

⚠ 拿不到启动时间时（老锁没这字段 / 无权限查询）**不据此判死**，
   仍走原来的"进程在不在 + 超时"两条判据 —— 宁可多等，也不能让两条作业撞同一扇窗。
"""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)          # scripts/power
for _p in (os.path.join(_POWER, "lib"), _POWER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_temu_buylabel as R  # noqa: E402


def _now():
    return time.time()


# ---------------------------------------------------------------- ① 原有判据不许退化
def test_dead_pid_is_stale():
    assert R._lock_is_stale({"pid": 999999, "started_at": _now()}, _now(), 3600) is True


def test_live_self_without_stamp_is_not_stale():
    """老格式的锁（没 pid_start）在超时前必须继续有效。"""
    rec = {"pid": os.getpid(), "started_at": _now()}
    assert R._lock_is_stale(rec, _now(), 3600) is False


def test_timeout_still_applies():
    rec = {"pid": os.getpid(), "started_at": _now() - 7200}
    assert R._lock_is_stale(rec, _now(), 3600) is True


# ---------------------------------------------------------------- ② PID 复用要能识破
def test_pid_reuse_is_detected_when_stamp_readable():
    """自己这个进程的启动时间是查得到的 ⇒ 用一个假戳就该判陈旧。"""
    me = os.getpid()
    real = R._pid_start_stamp(me)
    if not real:
        import pytest
        pytest.skip("本机取不到进程启动时间，跳过")
    rec = {"pid": me, "started_at": _now(), "pid_start": "00:00:00"}
    assert R._lock_is_stale(rec, _now(), 3600) is True, (
        "PID 相同但启动时间对不上，应判陈旧（这正是 PID 被复用的情形）")


def test_matching_stamp_is_not_stale():
    me = os.getpid()
    real = R._pid_start_stamp(me)
    if not real:
        import pytest
        pytest.skip("本机取不到进程启动时间，跳过")
    rec = {"pid": me, "started_at": _now(), "pid_start": real}
    assert R._lock_is_stale(rec, _now(), 3600) is False


def test_unreadable_stamp_falls_back_to_conservative(monkeypatch):
    """查不到启动时间时**不许**因此判死 —— 保守优先，走超时判据。"""
    monkeypatch.setattr(R, "_pid_start_stamp", lambda pid: "")
    rec = {"pid": os.getpid(), "started_at": _now(), "pid_start": "20:00:00"}
    assert R._lock_is_stale(rec, _now(), 3600) is False, (
        "查不到启动时间就判陈旧 = 会让两条作业撞同一扇窗")


# ---------------------------------------------------------------- ③ 新锁要写这个字段
def test_lock_record_includes_pid_start():
    src = open(os.path.join(_POWER, "run_temu_buylabel.py"), encoding="utf-8").read()
    i = src.find("me = {")
    assert i > 0, "找不到锁记录的构造处"
    seg = src[i:i + 500]
    assert "pid_start" in seg, "新锁必须写 pid_start，否则 PID 复用识别不了"
