# -*- coding: utf-8 -*-
"""test_fulfill_daily_entry.py — 「定时任务 → DSH 会话 → 发货工作区」这条入口的钉子。

用户 2026-11-06 要求：
    「设一个每天 6:30 的发货任务，这个定时任务用 dsh 会话激活；
      激活的会话在**发货这个工作区**出现」

2026-11-07 真机验收抓到一个反复出现的坑：`action` 里**直调了业务 .cmd**，
于是任务跑起来了、退出码也对，但**压根没有 DSH 会话** —— 用户要的那个
「会话出现在发货工作区」根本没发生。肉眼看不出来，因为日志一片绿。

这里把四件事钉住：
  ① action 必须经 run_via_dsh.cmd（唯一会起会话的入口）
  ② dsh.workspace 必须指向发货工作区，且 dsh.inner 指 .cmd（不是 .py，否则绕过窗口锁）
  ③ 引号只能是一个 `"`（raw string 写法会在 JSON 里留字面 `\\"`，PowerShell 会打散命令行）
  ④ 每天 04:00 + 上限 ≤180 分钟（与其它任务的窗口不重叠）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_PROJ, "scripts", "tasks"))

import add_fulfill_daily_task as A  # noqa: E402

REGISTRY = os.path.join(os.path.expanduser("~"), ".agents", "skills",
                        "scheduled-tasks", "tasks.json")


# ---------------------------------------------------------------- 生成器

def test_action_goes_through_the_dsh_runner():
    """直调业务 .cmd 也能跑通，但**不会起会话** —— 那正是这次要修的 bug。"""
    e = A.entry({})
    args = e["action"]["arguments"]
    assert "run_via_dsh.cmd" in args, "action 没经过 run_via_dsh.cmd ⇒ 不会有 DSH 会话"
    assert A.TASK_NAME in args, "任务名没传给 run_via_dsh.cmd ⇒ 它读不到 dsh.inner"
    assert "run_fulfill_daily.cmd" not in args, "action 直接调了业务入口，绕过了 DSH"


def test_action_has_no_escaped_quotes():
    """raw string 写法会在 JSON 里留下字面 `\\"`，New-ScheduledTaskAction 会打散它。"""
    args = A.entry({})["action"]["arguments"]
    assert '\\"' not in args, 'action 里有反斜杠转义的引号：%r' % args
    assert args.count('"') % 2 == 0, "引号不成对：%r" % args


def test_action_matches_the_other_dsh_tasks():
    """与已跑通的 dsh 任务（如 TEMU-Presale-Notify）形状必须一致。"""
    args = A.entry({})["action"]["arguments"]
    assert args.startswith("//nologo "), args
    assert "run_hidden.vbs" in args, "丢了隐藏窗口那层 ⇒ 会弹黑框"
    assert 'cmd /c ""' in args, "cmd /c 的引号层级不对：%r" % args


def test_workspace_is_the_shipping_folder():
    e = A.entry({})
    assert e["dsh"]["workspace"] == r"D:\AI-workplace\发货"
    assert e["dsh"]["enabled"] is True
    assert e["dsh"]["doc"].endswith("TASK-DOC-fulfill-daily.md")


def test_inner_points_at_the_locking_cmd_not_the_py():
    """窗口锁在 run_fulfill_daily.cmd 里；指到 .py 会和其它任务抢 BHY+BEL 窗口。"""
    inner = A.entry({})["dsh"]["inner"].replace("/", "\\")
    assert inner.endswith("run_fulfill_daily.cmd"), inner
    assert not inner.endswith(".py"), "inner 指向裸 .py 会绕过窗口锁"


def test_schedule_and_timeout_stay_out_of_other_tasks_way():
    e = A.entry({})
    assert e["schedule"]["daily"] == ["04:00"]
    assert e["timeout_minutes"] <= 180, "超过 180 分钟会压到 06:00 的周更和 07:30 的限流监控"
    assert 0 in e["ok_results"], "ok_results 必须含 0，否则成功也会被报成失败"
    assert 5 in e["ok_results"], "5 = 窗口锁被占（按设计跳过），不该报失败"


def test_registry_entry_passes_the_existence_checks():
    """entry() 里写的每个路径都得真存在（否则任务在触发那一刻静默失败）。"""
    e = A.entry({})
    for p in (A.CMD, A.RUNNER, A.DOC):
        assert os.path.isfile(p), "缺文件：%s" % p
    assert os.path.isdir(e["dsh"]["workspace"]), "工作区目录不存在"


# ---------------------------------------------------------------- 已落盘的注册表

def _live_entry():
    if not os.path.isfile(REGISTRY):
        return None
    with open(REGISTRY, encoding="utf-8-sig") as fh:
        reg = json.load(fh)
    for t in reg.get("tasks") or []:
        if t.get("name") == A.TASK_NAME:
            return t
    return None


def test_live_registry_actually_uses_the_runner():
    """真注册表（不是 entry() 的返回值）也必须是这条路 —— 否则改了代码没 apply。"""
    t = _live_entry()
    if t is None:
        import pytest  # noqa: PLC0415
        pytest.skip("本机注册表里没有这条任务")
    args = (t.get("action") or {}).get("arguments") or ""
    assert "run_via_dsh.cmd" in args, "注册表里的 action 还是直调业务入口 ⇒ 起不了会话"


def test_live_registry_points_at_shipping_workspace():
    t = _live_entry()
    if t is None:
        import pytest  # noqa: PLC0415
        pytest.skip("本机注册表里没有这条任务")
    dsh = t.get("dsh") or {}
    assert dsh.get("workspace") == r"D:\AI-workplace\发货"
    assert dsh.get("enabled") is True


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
