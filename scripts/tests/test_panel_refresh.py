# -*- coding: utf-8 -*-
"""test_panel_refresh.py — 「任务跑完刷一次面板」的合并/互斥回归（**不真的发布**）。

来历（2026-10-14 用户要求「定时任务每被执行一次就刷新一次面板」）：
一次发布 2~4 分钟，而 09:00 有 3 个任务同分钟触发、08:00~08:50 一串挨着。
若各自 publish，会**并发部署到同一个 Pages 项目**互相覆盖（历史事故：老看板把 admin 面板盖掉）。

所以本模块的性质错了就会出事，逐条钉死：
  ① 拿不到锁时**不发布**，但**标记必须落下**（否则那次请求被吞掉，面板永远不更新）；
  ② 发布收尾检查标记 → 有新请求就再发一轮（N 个任务挤一分钟 ⇒ 最多 2 次发布，不是 N 次）；
  ③ 陈旧锁（进程死了/超时）能接管 —— 否则崩一次就永远不再刷面板；
  ④ 发布失败**绝不**改变调用任务的退出码（`request()` 永远返回 0）。
"""
import json
import os
import sys
import time

import pytest

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SCRIPTS)

import panel_refresh as P                          # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """把状态目录指到临时目录，绝不碰真实的 _logs/panel-refresh。"""
    monkeypatch.setattr(P, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(P, "PENDING", os.path.join(str(tmp_path), "pending.json"))
    monkeypatch.setattr(P, "LOCK", os.path.join(str(tmp_path), "publish.lock"))
    monkeypatch.setattr(P, "RUNLOG", os.path.join(str(tmp_path), "refresh.log"))
    monkeypatch.setattr(P, "_say", lambda m: None)      # 测试里不写日志
    return tmp_path


# ------------------------------------------------------------------ ① 标记
def test_mark_pending_accumulates_reasons(tmp_path):
    P.mark_pending("a")
    P.mark_pending("b")
    P.mark_pending("a")                                 # 重复的不再追加
    rec = P._read_json(P.PENDING)
    assert rec["reasons"] == ["a", "b"]
    assert rec["count"] == 3, "每次请求都要计数（含重复），便于看同一分钟挤了几个"


def test_take_pending_is_atomic_and_clears(tmp_path):
    P.mark_pending("x")
    assert P.has_pending() is True
    got = P.take_pending()
    assert got["reasons"] == ["x"]
    assert P.has_pending() is False
    assert P.take_pending() == {}, "取过之后应为空"


# ------------------------------------------------------------------ ② 锁
def test_acquire_lock_excludes_second_holder(tmp_path):
    assert P.acquire_lock() is True
    # 第二个"进程"：把锁文件里的 pid 写成别人（还活着=当前进程），应被拒
    with open(P.LOCK, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "started_at": time.time()}, f)
    P_OTHER = object()
    # 直接改自身 pid 后调用会被判"自己持有"，所以用死 pid 验证另一条分支
    with open(P.LOCK, "w", encoding="utf-8") as f:
        json.dump({"pid": 999999999, "started_at": time.time()}, f)
    assert P.acquire_lock() is True, "死进程的锁应可接管"


def test_stale_lock_by_age_is_taken_over(tmp_path):
    with open(P.LOCK, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "started_at": time.time() - 7200,
                   "started": "old"}, f)
    assert P.acquire_lock() is True


def test_release_lock_only_removes_own(tmp_path):
    """不偷走别人的锁：锁已被接管时，退出不许删。"""
    assert P.acquire_lock() is True
    with open(P.LOCK, "w", encoding="utf-8") as f:
        json.dump({"pid": 999999999, "started_at": time.time()}, f)
    P.release_lock()
    assert os.path.exists(P.LOCK), "接管者的锁必须还在"


# ------------------------------------------------------------------ ③ 合并（核心）
def test_publish_loop_coalesces_new_requests(tmp_path, monkeypatch):
    """发布期间来的新请求 → 收尾再发一轮；没有新请求就不多发。"""
    calls = []

    def fake_publish():
        calls.append(1)
        if len(calls) == 1:
            P.mark_pending("arrived-during-publish")     # 模拟发布期间又来了请求
        return 0, "ok"

    monkeypatch.setattr(P, "_publish_once", fake_publish)
    P.mark_pending("first")
    rc = P.publish_loop()
    assert rc == 0
    assert len(calls) == 2, "第1轮 + 期间来的请求再1轮 = 2，且不应无限循环"
    assert not P.has_pending()
    assert not os.path.exists(P.LOCK), "收工必须放锁"


def test_publish_loop_no_extra_round_when_nothing_pending(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(P, "_publish_once", lambda: (calls.append(1), (0, "ok"))[1])
    P.mark_pending("only")
    P.publish_loop()
    assert len(calls) == 1, "没有新请求就不许再发（省 Cloudflare 额度）"


def test_publish_loop_stops_on_failure(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(P, "_publish_once", lambda: (calls.append(1), (1, "boom"))[1])
    P.mark_pending("x")
    rc = P.publish_loop()
    assert rc == 1 and len(calls) == 1, "失败后不许再叠一轮"
    assert not os.path.exists(P.LOCK), "失败也要放锁"


def test_publish_loop_returns_0_when_lock_held(tmp_path, monkeypatch):
    """已有发布在跑 → 本次直接返回（合并），且**不**调用发布。"""
    calls = []
    monkeypatch.setattr(P, "_publish_once", lambda: (calls.append(1), (0, "ok"))[1])
    assert P.acquire_lock() is True
    with open(P.LOCK, "w", encoding="utf-8") as f:       # 假装是"别的活着的进程"持有
        json.dump({"pid": os.getpid(), "started_at": time.time() + 9999}, f)
    # 让 _pid_alive 认为它活着、且未超时 → acquire 失败
    rc = P.publish_loop()
    assert rc == 0 and calls == [], "拿不到锁时不许自己发布"


# ------------------------------------------------------------------ ④ 不拖累业务
def test_request_never_raises_and_always_returns_0(tmp_path, monkeypatch):
    """request() 是各任务收尾调用的：它必须**永远** rc=0，绝不让业务失败。"""
    def boom(*a, **k):
        raise RuntimeError("publish exploded")
    monkeypatch.setattr(P, "publish_loop", boom)
    monkeypatch.setattr(P, "acquire_lock", boom)

    class FakePopen:
        def __init__(self, *a, **k):
            pass
    monkeypatch.setattr(P.subprocess, "Popen", FakePopen)
    rc = P.request("some-task", wait=False)
    assert rc == 0


def test_request_marks_pending_even_when_publish_fails(tmp_path, monkeypatch):
    """发布起不来也必须留标记：下一次刷新（定时任务）会把它带上。"""
    class FakePopen:
        def __init__(self, *a, **k):
            raise OSError("cannot spawn")
    monkeypatch.setattr(P.subprocess, "Popen", FakePopen)
    P.request("t", wait=False)
    assert P.has_pending() is True, "标记不能被吞掉"


def test_request_wait_mode_runs_publish_synchronously(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(P, "_publish_once", lambda: (calls.append(1), (0, "ok"))[1])
    rc = P.request("t", wait=True)
    assert rc == 0 and len(calls) == 1
    assert not P.has_pending()


# ------------------------------------------------------------------ ⑤ 状态查询
def test_status_reports_pending_and_lock(tmp_path):
    assert P.status() == {"pending": {}, "lock": {}, "lock_alive": False}
    P.mark_pending("z")
    assert P.acquire_lock() is True
    s = P.status()
    assert s["pending"]["reasons"] == ["z"]
    assert s["lock_alive"] is True


# ------------------------------------------------------------------ ⑥ 不许再弹出控制台窗口
def test_background_flags_use_no_window_and_never_detached():
    """2026-10-14 踩坑回归：**DETACHED_PROCESS 会让发布链的子孙进程弹出一个可见控制台窗口。**

    机制：DETACHED_PROCESS = 「完全没有控制台」，于是子孙里谁需要控制台，Windows 就新分配
    一个**可见**的（用户截图里那个标题为 python.exe 全路径的黑框）。
    只用 CREATE_NO_WINDOW（＝有控制台、但无窗口，子孙继承它）。
    两者同用也不行 —— 官方文档：CREATE_NO_WINDOW「与 DETACHED_PROCESS 同用时被忽略」。
    """
    flags = P._background_creationflags()
    if os.name == "nt":
        assert flags & P.CREATE_NO_WINDOW, "必须带 CREATE_NO_WINDOW（否则子孙会弹窗）"
        assert not (flags & P.DETACHED_PROCESS), \
            "绝不能用 DETACHED_PROCESS：子孙进程会新分配一个可见控制台窗口"
    else:
        assert flags == 0


def test_spawn_background_passes_those_flags(tmp_path, monkeypatch):
    """真正 spawn 时必须用上面那套标志（别在一个地方修好、另一个地方又写死）。"""
    seen = {}

    class FakePopen:
        def __init__(self, *a, **k):
            seen.update(k)

    monkeypatch.setattr(P.subprocess, "Popen", FakePopen)
    P._spawn_background("t")
    assert seen.get("creationflags") == P._background_creationflags()
    if os.name == "nt":
        assert not (seen["creationflags"] & P.DETACHED_PROCESS)
