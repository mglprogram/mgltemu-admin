# -*- coding: utf-8 -*-
"""test_fulfill_skip_dates.py — 发货定时任务的「跳过某些日期」闸门守门。

★★ 来历（用户 2026-11-27）：
    「把周末两天的发货的定时任务先暂停」。

为什么不直接禁用计划任务：
    Windows 计划任务**不支持"跳过指定日期"**（只有 Daily/Weekly/Boundary 那几种）。
    整任务禁用又要人记得恢复 —— 忘了就是好几天不发货。
    ⇒ 在 `run_fulfill_daily.cmd` 里放一道闸门：读 `fulfill_skip_dates.txt`，
      命中当天日期就 **exit 0**（按设计跳过，不算失败，不进 FAILED.log、不推失败告警）。

实测（2026-11-27）：把当天临时加进名单后跑整脚本 →
    [SKIP] 2026-11-27 在 skip_dates 名单里，今天不发（exit 0）
    EXIT=0  ELAPSED=0s
"""
from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_PROJ = os.path.dirname(os.path.dirname(_POWER))
_CMD = os.path.join(_PROJ, "scripts", "tasks", "run_fulfill_daily.cmd")
_SKIP = os.path.join(_PROJ, "scripts", "tasks", "fulfill_skip_dates.txt")


def _cmd() -> str:
    return open(_CMD, encoding="utf-8", errors="replace").read()


def _active_dates() -> list:
    out = []
    for ln in open(_SKIP, encoding="utf-8", errors="replace").read().splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


# ---------------------------------------------------------------- 闸门存在且正确


def test_skip_file_exists():
    assert os.path.isfile(_SKIP), "缺 %s（跳过日期名单）" % _SKIP


def test_cmd_has_skip_gate_before_doing_any_work():
    """闸门必须在**任何业务动作之前**（尤其窗口锁 / 真下单之前）。"""
    s = _cmd()
    i_gate = s.find("SKIP-DATE GATE")
    assert i_gate > 0, "run_fulfill_daily.cmd 里没有 skip-date 闸门"
    assert 'set "SKIPFILE=' in s, "闸门没指向名单文件"
    # 关键：闸门要排在这些之前
    for marker in ("run_with_window_lock.py", "run_fulfill_daily_inner.py"):
        j = s.find(marker)
        assert j > i_gate, "闸门必须排在 `%s` 之前" % marker


def test_skip_exits_zero_not_failure():
    """跳过要 exit 0 —— 否则巡检会把它当故障报出来。"""
    s = _cmd()
    seg = s[s.find("SKIP-DATE GATE"):s.find("end SKIP-DATE GATE")]
    assert "exit /b 0" in seg, "跳过时没有 exit 0"
    # 不许写成 exit /b 1 或其它非 0
    for bad in ("exit /b 1", "exit /b 42"):
        assert bad not in seg, "跳过分支里出现了非 0 退出：%s" % bad


def test_skip_comments_and_blank_lines_ignored():
    """`#` 注释和空行不能当日期用（否则会误跳或天天跳）。"""
    s = _cmd()
    seg = s[s.find("SKIP-DATE GATE"):s.find("end SKIP-DATE GATE")]
    assert "~0,1" in seg or "#" in seg, "没有排除注释行"
    assert '"%%S"==""' in seg or 'if not "%%S"==""' in seg, "没有跳过空行"


# ---------------------------------------------------------------- 登记内容


def test_weekend_dates_are_registered():
    """用户 2026-11-27 指定：2026-11-28（周六）+ 2026-11-29（周日）不发货。"""
    dates = _active_dates()
    assert "2026-11-28" in dates, "11-28 没登记"
    assert "2026-11-29" in dates, "11-29 没登记"


def test_only_valid_date_format():
    """名单里只许出现 yyyy-MM-dd，避免手误写成 11/28 之类永远匹配不上。"""
    import re as _re  # noqa: PLC0415
    for d in _active_dates():
        assert _re.fullmatch(r"\d{4}-\d{2}-\d{2}", d), "非法日期格式：%r" % d


def test_no_stale_past_dates():
    """过去的日期应被清掉（留着没意义，还让人以为还在跳）。"""
    import datetime as _dt  # noqa: PLC0415
    today = _dt.date.today()
    stale = []
    for d in _active_dates():
        try:
            if _dt.datetime.strptime(d, "%Y-%m-%d").date() < today:
                stale.append(d)
        except ValueError:
            pass
    assert not stale, "名单里有过期日期，建议清掉：%s" % stale
