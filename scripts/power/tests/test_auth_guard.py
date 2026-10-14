# -*- coding: utf-8 -*-
"""auth_guard 的离线判据测试（2026-10-10 用户口径「所有 temu 脚本都兜底一次 auth」）。

纯离线：只验 `should_guard()` 判据 + `ensure_auth()` 的健壮性（不真连浏览器）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

from lib import auth_guard as ag  # noqa: E402


def test_should_guard_skips_when_no_store():
    """没有店 ⇒ 没有店窗口要授权 ⇒ 跳过。"""
    assert ag.should_guard("overdue", "", 51581) is False


def test_should_guard_skips_when_no_port():
    """端口都没探到 ⇒ 授权也没法做 ⇒ 跳过（交业务报错，诊断更准）。"""
    assert ag.should_guard("overdue", "BHY", 0) is False


def test_should_guard_skips_auth_action():
    """action=auth 本身就是授权 ⇒ 不能再兜底（否则自我递归）。"""
    assert ag.should_guard("auth", "BHY", 51581) is False


def test_should_guard_skips_plan_action():
    """plan 不碰浏览器 ⇒ 无需授权。"""
    assert ag.should_guard("plan", "BHY", 51581) is False


def test_should_guard_runs_for_real_actions():
    """真实业务动作 + 有店 + 有端口 ⇒ 要兜底。"""
    for act in ("overdue", "confirm", "tracking", "record", "fill", "pipeline", "widen"):
        assert ag.should_guard(act, "BHY", 51581) is True, act


def test_ensure_auth_never_raises_on_missing_entry():
    """入口不存在时：不抛异常、如实返回 skipped。"""
    r = ag.ensure_auth("BHY", port=51581, log=lambda *a: None,
                       entry=r"C:\definitely\not\here.py")
    assert r["ran"] is False and r["skipped"] is True


def test_ensure_auth_skips_without_port():
    r = ag.ensure_auth("BHY", port=0, log=lambda *a: None)
    assert r["ran"] is False


def test_default_entry_is_us_seller_center():
    """默认入口＝美国站 run_temu_auth.py（授权分域：合作仓要显式传 entry）。"""
    assert ag.AUTH_ENTRY.endswith(os.path.join("power", "run_temu_auth.py"))
