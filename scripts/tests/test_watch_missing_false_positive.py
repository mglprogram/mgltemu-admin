# -*- coding: utf-8 -*-
"""巡检「任务不存在」必须是**双通道确认**的结论（2026-11-02 假告警修复）。

事故：2026-11-02 14:57 收到飞书 + 手机告警
    ❓ **待发货已买面单订单手动确认 + 双账号核对**：任务不存在（可能被删/改名）
而任务一直在（14:45 刚触发、当时正在跑），15:27 下一轮巡检就正常了。

根因：`task_info()` 只走一条通道 `Get-ScheduledTask -TaskName X`。它在**被查的那一瞬间**
可能返回 null（任务刚跑完、注册表正在刷新、或有别的进程正写它的 XML）⇒ 上层直接判
「任务不存在」并推送。`state.json` 里残留的 4 个 `*|missing` 键说明这类假告警
**之前已经发生过 3 次**（PWShipping / Overdue-Confirm / WorkBuddy-Proxy-Sentinel）。

修法：`missing` 是高代价结论 ⇒ ① 重试 3 次；② 用 `schtasks /Query` 交叉确认；
两条通道都说没有才认。不一致时返回 `unknown`，本轮跳过、**不写 seen**（下一轮重判）。

运行: python -m pytest scripts/tests/test_watch_missing_false_positive.py -q
"""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))       # scripts/

import watch_tasks as w  # noqa: E402


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """别真睡 1.5s×3。"""
    monkeypatch.setattr(w.time, "sleep", lambda s: None)


# ---------------------------------------------------------------- 核心回归
def test_ps_missing_but_schtasks_found_is_NOT_missing(monkeypatch):
    """PowerShell 说没有、schtasks 说在 ⇒ 不能报「任务不存在」（就是当天那条假告警）。"""
    monkeypatch.setattr(w, "_ps", lambda cmd: "MISSING")
    monkeypatch.setattr(w, "_schtasks_exists", lambda name: True)
    info = w.task_info("TEMU-Pending-Confirm-Daily")
    assert not info.get("missing"), "两通道结论不一致时绝不能判 missing"
    assert info.get("unknown") is True, "应回 unknown 让主循环跳过（下一轮重判）"


def test_both_channels_say_missing_then_missing(monkeypatch):
    """两个通道都说没有 ⇒ 才允许报「任务不存在」。"""
    monkeypatch.setattr(w, "_ps", lambda cmd: "MISSING")
    monkeypatch.setattr(w, "_schtasks_exists", lambda name: False)
    assert w.task_info("TEMU-Nope") .get("missing") is True


def test_empty_output_retries_before_concluding(monkeypatch):
    """PowerShell 偶发返回空串：必须重试，不能一次空就下结论。"""
    calls = {"n": 0}

    def flaky(cmd):
        calls["n"] += 1
        if calls["n"] < 3:
            return ""
        return '{"State":"Ready","Last":"2026-11-02T14:45:01","Result":1,"Next":""}'

    monkeypatch.setattr(w, "_ps", flaky)
    monkeypatch.setattr(w, "_schtasks_exists", lambda name: True)
    info = w.task_info("X")
    assert info.get("State") == "Ready"
    assert calls["n"] == 3, "应重试到第 3 次才拿到结果"


def test_unparsable_json_falls_back_to_schtasks(monkeypatch):
    """PowerShell 回了垃圾（模块报错文案混进 stdout）⇒ 用 schtasks 兜底，别判 missing。"""
    monkeypatch.setattr(w, "_ps", lambda cmd: "Get-ScheduledTask : Access denied")
    monkeypatch.setattr(w, "_schtasks_exists", lambda name: True)
    assert w.task_info("X").get("unknown") is True


# ---------------------------------------------------------------- Int32 溢出（真根因）
def test_result_is_read_as_int64_not_int32():
    """**真根因**：`Result=[int]$i.LastTaskResult` 装不下 0xC000013A（3221225786）。

    Int32.MaxValue = 2147483647 < 3221225786 ⇒ `[int]` 强转抛
    `Cannot convert value "3221225786" to type "System.Int32"`，
    **整个 $o 对象因此没有输出** ⇒ `_ps()` 拿到报错文本 ⇒ 上层判「任务不存在」。

    实测链条（2026-11-02）：用 `taskkill /F /T` 杀过一轮任务后，该任务的
    LastTaskResult = 3221225786；下一次巡检读它爆 Int32 → 假告警「任务不存在」。
    `state.json` 里残留的 4 个 `*|missing` 键全是被杀过的任务，原因一致。

    ⇒ 必须是 `[long]`。而且这类码**恰恰最该被报出来**（超时被杀/强杀），
      旧实现把真故障伪装成了"配置错误"。
    """
    src = open(os.path.join(os.path.dirname(_HERE), "watch_tasks.py"),
               encoding="utf-8").read()
    assert "[long]$i.LastTaskResult" in src, "必须用 Int64 读 LastTaskResult"
    assert "[int]$i.LastTaskResult" not in src, "Int32 会因 0xC000013A 溢出"


def test_terminated_code_survives_the_python_side(monkeypatch):
    """3221225786 走过 task_info 后必须原样保留，不能被当 missing / 截断。"""
    monkeypatch.setattr(w, "_ps", lambda cmd: '{"State":"Ready","Last":"2026-11-02T17:00:00",'
                                              '"Result":3221225786,"Next":""}')
    info = w.task_info("TEMU_Bottom_DailyStockSync")
    assert not info.get("missing")
    assert int(info["Result"]) == 3221225786, "0xC000013A 必须原样传下来"


# ---------------------------------------------------------------- schtasks 通道
def test_schtasks_exists_returns_true_when_query_succeeds(monkeypatch):
    class R:
        returncode = 0
        stdout = "TaskName: TEMU-X"
        stderr = ""

    monkeypatch.setattr(w.subprocess, "run", lambda *a, **kw: R())
    assert w._schtasks_exists("TEMU-X") is True


def test_schtasks_not_found_is_detected(monkeypatch):
    class R:
        returncode = 1
        stdout = ""
        stderr = "ERROR: The system cannot find the file specified."

    monkeypatch.setattr(w.subprocess, "run", lambda *a, **kw: R())
    assert w._schtasks_exists("TEMU-Nope") is False


def test_schtasks_other_errors_are_not_treated_as_missing(monkeypatch):
    """权限/本地化文案等其它错误**不能**当"不存在"——宁可不报，也不乱报。"""
    class R:
        returncode = 1
        stdout = ""
        stderr = "ERROR: Access is denied."

    monkeypatch.setattr(w.subprocess, "run", lambda *a, **kw: R())
    assert w._schtasks_exists("TEMU-X") is True


def test_schtasks_command_failure_keeps_benefit_of_doubt(monkeypatch):
    def boom(*a, **kw):
        raise OSError("schtasks.exe 不见了")

    monkeypatch.setattr(w.subprocess, "run", boom)
    assert w._schtasks_exists("TEMU-X") is True


# ---------------------------------------------------------------- 主循环契约
def test_main_loop_skips_unknown_without_recording_seen():
    """`unknown` 必须 continue（不报警、不写 seen），否则会把真故障永久静音。"""
    src = open(os.path.join(os.path.dirname(_HERE), "watch_tasks.py"),
               encoding="utf-8").read()
    seg = src.split('if info.get("unknown")', 1)[1].split('if info.get("missing")', 1)[0]
    assert "continue" in seg, "unknown 必须 continue"
    assert "seen[" not in seg, "unknown 不许写 seen（否则下一轮不再重判）"
