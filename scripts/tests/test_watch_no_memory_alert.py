# -*- coding: utf-8 -*-
"""test_watch_no_memory_alert.py — 内存告警**不再推送**（2026-11-09 用户令）。

用户原话（截图是一条每 30 分钟就来一条的「本机内存不足」）：
「把这个定时任务删除，无意义的推送通知」。

为什么它确实是噪音，而不是"该修的问题"
--------------------------------------
这台机器 16 GB，常驻开着紫鸟（多店窗口）+ Chrome + WPS + DSH，
可用物理内存长期在 300~700 MB 徘徊；阈值 800 MB ⇒ **几乎每一轮巡检都命中**，
一天几十条。而收件人既不能去关那些程序（正是干活要用的），
也无法据此做任何决定 —— 一条"你该关程序"的告警重复几十次，只会训练人忽略告警。

⚠ 关掉的是**推送**，不是内存感知能力：
  · `watch_tasks._memory_alert()` 函数保留（`--memory` 手工可查、测试仍覆盖）
  · `auto_repair.mem_is_tight()` / `auth_guard.heal_own_hung_pages` 照旧用它
    决定「紧张时不要重载页面 / 不要自动重跑」—— 那部分行为一个字没改
"""
from __future__ import annotations

import inspect
import os
import sys

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src() -> str:
    return open(os.path.join(_PROJ, "scripts", "watch_tasks.py"), encoding="utf-8").read()


def test_memory_alert_is_not_appended_to_alerts():
    """`main()` 里不许再把 `_memory_alert()` 的结果塞进 alerts（那就是推送内容）。"""
    s = _src()
    body = s.split("def main()", 1)[1]
    # 允许在注释里提 "不要改回"，但不允许有真正的调用赋值
    code_lines = [ln for ln in body.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    offenders = [ln for ln in code_lines
                 if "_memory_alert()" in ln and "alerts.append" in ln]
    assert not offenders, "内存告警又被接回推送了：%s" % offenders


def test_memory_function_is_kept_for_manual_use():
    """函数本身要留着 —— 内存紧张仍然影响「要不要重载页面/要不要自动重跑」。"""
    sys.path.insert(0, os.path.join(_PROJ, "scripts"))
    import watch_tasks as W
    assert callable(getattr(W, "_memory_alert", None))
    assert isinstance(W._memory_alert(), str)


def test_memory_flag_prints_without_pushing():
    s = _src()
    assert '"--memory" in sys.argv' in s, "缺少 --memory 手工查看开关"
    i = s.find('"--memory" in sys.argv')
    seg = s[i:i + 400]
    assert "_memory_alert()" in seg, "--memory 应直接打印内存状况"
    assert "send_feishu" not in seg and "send_meow" not in seg, "--memory 不许推送"


def test_help_mentions_the_memory_switch():
    s = _src()
    i = s.find('"--help" in sys.argv')
    assert "--memory" in s[i:i + 600], "help 里应说明 --memory"
