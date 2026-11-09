# -*- coding: utf-8 -*-
"""test_watch_schedule_aware.py — 漏跑告警必须先问「今天轮不轮到它」。

来历（用户 2026-11-09 发的两张飞书截图）
--------------------------------------
`Powerwarehouse-SKU-Migrate-Daily` 只在**工作日** 12:30/18:30 跑
（注册表 `schedule.weekly.days = Mon..Fri`），但 `task_watch.json` 里只配了
`expect_by: 13:00`。旧巡检**每天**都判漏跑 ⇒ 周六 11-07、周日 11-08
连续两天推「⏰ 到点未运行，最近一次运行：11-06 18:30」。

任务那两天**本来就不该跑**。假告警的真实代价不是多看一眼，
而是人会**开始忽略告警** —— 真漏跑那天就没人管了。

修法：漏跑判定加一道「今天有排程吗」的闸门（`scheduled_today`）。
`expect_by` 只表达"几点该跑完"，不表达"哪天该跑"。
"""
from __future__ import annotations

import os
import sys
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import watch_tasks as W  # noqa: E402

MON = date(2026, 11, 9)
SAT = date(2026, 11, 7)
SUN = date(2026, 11, 8)

PW = {"schedule": {"weekly": {"days": ["Monday", "Tuesday", "Wednesday",
                                     "Thursday", "Friday"],
                             "times": ["12:30", "18:30"]}}}
DAILY = {"schedule": {"daily": ["08:00"]}}
EVERY = {"schedule": {"every_minutes": 30, "start": "08:27"}}
ONCE = {"schedule": {"once": {"at": "2026-12-23 10:00"}}}
THU = {"schedule": {"weekly": {"days": ["Thursday"], "times": ["06:00"]}}}


# ---------------------------------------------------------------- ① 逐种写法
def test_weekday_task_is_not_due_on_weekend():
    """事故原型：工作日任务在周六/周日**不该被判漏跑**。"""
    assert W.scheduled_today(PW, MON) is True
    assert W.scheduled_today(PW, SAT) is False, "周六不该判漏跑"
    assert W.scheduled_today(PW, SUN) is False, "周日不该判漏跑"


def test_every_day_task_is_always_due():
    for d in (MON, SAT, SUN):
        assert W.scheduled_today(DAILY, d) is True


def test_every_minutes_task_is_always_due():
    assert W.scheduled_today(EVERY, MON) is True


def test_weekly_single_day():
    assert W.scheduled_today(THU, date(2026, 11, 12)) is True
    assert W.scheduled_today(THU, date(2026, 11, 13)) is False


def test_once_task_only_on_its_date():
    assert W.scheduled_today(ONCE, date(2026, 12, 23)) is True
    assert W.scheduled_today(ONCE, date(2026, 12, 22)) is False


# ---------------------------------------------------------------- ② 判不了就不拦
def test_unknown_schedule_does_not_silence_alerts():
    """判不出来必须返回 None（照旧判漏跑），绝不能因为"不知道"就把告警关掉。"""
    assert W.scheduled_today({}, MON) is None
    assert W.scheduled_today({"schedule": {}}, MON) is None
    assert W.scheduled_today({"schedule": {"weekly": {}}}, MON) is None
    assert W.scheduled_today({"schedule": {"weekly": {"days": ["Funday"]}}}, MON) is None
    assert W.scheduled_today({"schedule": {"once": {}}}, MON) is None


# ---------------------------------------------------------------- ③ 大小写/空格容错
def test_weekday_names_are_case_and_space_tolerant():
    e = {"schedule": {"weekly": {"days": [" monday ", "FRIDAY"]}}}
    assert W.scheduled_today(e, MON) is True
    assert W.scheduled_today(e, date(2026, 11, 6)) is True      # Friday
    assert W.scheduled_today(e, SAT) is False


# ---------------------------------------------------------------- ④ 接线
def test_main_consults_schedule_before_reporting_missed():
    src = open(os.path.join(_SCRIPTS, "watch_tasks.py"), encoding="utf-8").read()
    i = src.find("# ---- ② 漏跑")
    seg = src[i:i + 900]
    assert "scheduled_today(" in seg, "漏跑分支没问「今天轮不轮到它」"
    assert "is False" in seg and "continue" in seg, "非排程日必须直接跳过"


def test_real_weekend_case_from_the_screenshot():
    """把用户截图里的真实条目回放一遍（注册表实际内容）。"""
    import io
    import json
    reg = os.path.join(os.path.expanduser("~"), ".agents", "skills",
                       "scheduled-tasks", "tasks.json")
    if not os.path.isfile(reg):
        import pytest
        pytest.skip("本机没有 scheduled-tasks 注册表")
    entries = {e["name"]: e for e in
               json.load(io.open(reg, encoding="utf-8-sig"))["tasks"]}
    pw = entries.get("Powerwarehouse-SKU-Migrate-Daily")
    assert pw is not None, "注册表里没有 Fnsku 迁移任务"
    assert W.scheduled_today(pw, MON) is True, "工作日必须判"
    assert W.scheduled_today(pw, SAT) is False
    assert W.scheduled_today(pw, SUN) is False
