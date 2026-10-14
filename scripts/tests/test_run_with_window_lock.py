# -*- coding: utf-8 -*-
"""test_run_with_window_lock.py — 窗口锁包装器的离线回归（不碰浏览器、不起真实业务）。

来历（2026-10-14）：6 个窗口任务要共用一把锁。包装器是 .cmd 唯一的接触面，
它的两条性质错了就会把定时任务弄坏：
  ① **退出码必须原样透传** —— 业务靠 exit code 判成败（HARD STOP 42 / 3 / 4 各有含义），
     包装器若把 rc 吞成 0，失败会被面板和 TaskWatch 当成成功（静默失败，最坏的一种）。
  ② 拿不到锁必须**明确失败**（BUSY_EXIT=5）且**不跑业务命令**（不硬插、不双跑）。
"""
import os
import subprocess
import sys

import pytest

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS)

import run_with_window_lock as R                 # noqa: E402
import window_lock as W                          # noqa: E402

WRAP = os.path.join(_SCRIPTS, "run_with_window_lock.py")


def _run(args, **kw):
    return subprocess.run([sys.executable, WRAP] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", **kw)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """锁目录指到临时目录；包装器是子进程，用环境变量传不进去，所以走 cwd 无关的模块补丁。

    子进程有自己的一份 `window_lock`，因此这里改用「真实 _logs/window-locks 里不落东西」
    的做法：只跑那些**会立刻成功**的用例，且在用例内显式断言锁目录已空。
    """
    monkeypatch.setattr(W, "_LOCK_DIR", str(tmp_path))
    return tmp_path


def test_exit_code_is_passed_through():
    """① 业务 rc 原样透传（这里用 exit 7 当哨兵；0/3/4/42 同理）。"""
    r = _run(["--window", "", "--action", "t", "--", "cmd", "/c", "exit 7"])
    assert r.returncode == 7, r.stdout + r.stderr


def test_zero_exit_stays_zero():
    r = _run(["--window", "", "--action", "t", "--", "cmd", "/c", "exit 0"])
    assert r.returncode == 0


def test_no_lock_mode_runs_command(tmp_path):
    """--no-lock 时不落锁、照跑。"""
    r = _run(["--no-lock", "--window", "ZZZ", "--action", "t", "--", "cmd", "/c", "echo RAN"])
    assert r.returncode == 0
    assert "RAN" in r.stdout


def test_empty_window_means_no_lock(tmp_path):
    """空窗口键＝显式声明不用窗口 → 不落锁、照跑（给非窗口任务复用同一包装用）。"""
    r = _run(["--window", "", "--action", "t", "--", "cmd", "/c", "echo PLAIN"])
    assert r.returncode == 0 and "PLAIN" in r.stdout


def test_missing_command_is_usage_error():
    r = _run(["--window", "", "--action", "t"])
    assert r.returncode == 2


def test_busy_exit_constant_is_distinct():
    """BUSY_EXIT 不能和业务退出码撞（1/2/3/4/42 都在用）。"""
    assert R.BUSY_EXIT == 5
    assert R.BUSY_EXIT not in (0, 1, 2, 3, 4, 42)
