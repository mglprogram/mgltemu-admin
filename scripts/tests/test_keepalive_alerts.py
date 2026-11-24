# -*- coding: utf-8 -*-
"""test_keepalive_alerts.py — 保鲜任务失败必须**直接推飞书**（2026-11-24 用户令「加」）。

背景：`seller_temp` 只在「上次授权 + 24 小时」内有效。03:30 的保鲜任务一旦失败，
白天每个碰这家店的任务都会被踢到 `auth/authentication` —— 用户看到的就是
「时不时弹出登录/授权页」（11-24 当天 KLS 窗口 3 个标签页全被踢，实测）。

原来只有巡检汇总里一行，很容易翻过去 ⇒ 现在失败即推群 + 手机。
"""
import inspect
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_PROJ, "scripts", "tasks"))

import temu_auth_keepalive as K  # noqa: E402


def test_has_alert_helper():
    assert callable(K._alert)


def test_alert_pushes_group_and_phone():
    """两条通道都要发（群给团队看、手机给本人），且推送失败不许影响退出码。"""
    src = inspect.getsource(K._alert)
    assert "push_group" in src, "没推飞书机器人群"
    assert "push_meow" in src, "没推手机"
    assert "except" in src, "推送失败必须被吞掉（告警不能改业务结论）"


def test_main_alerts_only_when_some_store_failed():
    """`if bad:` 里才推 —— 全成功时不许打扰。"""
    src = inspect.getsource(K.main)
    i = src.find("if bad:")
    assert i > 0, "没找到失败分支"
    seg = src[i:i + 900]
    assert "_alert(" in seg, "失败时没调用 _alert"


def test_alert_text_names_stores_and_the_fix():
    """告警正文要直接给出店名 + 处理办法（用户要能照着做，不用再问）。"""
    src = inspect.getsource(K.main)
    i = src.find("if bad:")
    seg = src[i:i + 900]
    assert "%s：%s" in seg or "store" in seg, "没列出是哪家店"
    assert "run_temu_auth.py" in seg, "没给补救命令"
    assert "授权" in seg, "没说明表现/处理"


def test_success_path_does_not_alert():
    """3/3 成功时 `bad` 为空 ⇒ 不该进 alert 分支（逻辑上由 `if bad:` 保证）。"""
    src = inspect.getsource(K.main)
    assert "if bad:" in src
    assert "return 0 if not bad" in src
