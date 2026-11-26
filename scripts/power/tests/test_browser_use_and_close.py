"""钉住「用完即关」这条要求（2026-11-26 用户令）。

用户原话：「检查出会开无头 chrome 的任务，加一个要求：用完即关，不要一直开着占内存」

这里用源码断言而不是跑真 chrome：真要跑会弹真浏览器、在无人值守机器上
还要等登录，成本高且不稳。断言点挑的是「改坏了会漏关」的那几句。
"""

import ast
import inspect
import os
import sys
import textwrap

import pytest

# 本文件在 scripts/power/tests/，仓库根要往上三级
ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
POWER = os.path.join(ROOT, "scripts", "power")
sys.path.insert(0, POWER)
sys.path.insert(0, os.path.join(POWER, "lib"))


def _src(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_pw_browser_has_ephemeral_helpers():
    src = _src(os.path.join(POWER, "lib", "pw_browser.py"))
    for name in ("ensure_tracked", "close_if_started", "ephemeral", "sweep_orphans"):
        assert ("def %s" % name) in src or ("class %s" % name) in src, "缺 %s" % name


def test_close_if_started_only_closes_what_we_started():
    src = _src(os.path.join(POWER, "lib", "pw_browser.py"))
    body = src.split("def close_if_started")[1].split("\ndef ")[0]
    # 必须查记账表；没记账就直接返回，不许无脑 close()
    assert "_STARTED_BY_US" in body
    assert "if not _STARTED_BY_US.pop" in body
    # PW_KEEP_CHROME 的人肉调试开关要保留
    assert "_keep_chrome()" in body


def test_ensure_tracked_records_only_when_port_was_dead():
    src = _src(os.path.join(POWER, "lib", "pw_browser.py"))
    body = src.split("def ensure_tracked")[1].split("\ndef ")[0]
    assert "was_alive = port_alive(port)" in body
    assert "if pg is not None and not was_alive" in body


def test_ephemeral_closes_on_exception():
    src = _src(os.path.join(POWER, "lib", "pw_browser.py"))
    assert "def __exit__" in src
    body = src.split("def __exit__")[1].split("\ndef ")[0]
    assert "self.close()" in body
    assert "return False" in body, "__exit__ 不能吞掉业务异常"


def test_sweep_orphans_never_touches_user_chrome():
    src = _src(os.path.join(POWER, "lib", "pw_browser.py"))
    body = src.split("def sweep_orphans")[1]
    # 只认临时 profile + headless
    assert ".chrome-headless-profile" in body
    assert "--headless" in body
    # 必须排除子进程（否则会把 renderer 当主进程杀）
    assert "--type=" in body


def _main_block_runs_cleanup(path, port_expr="PORT"):
    """入口的 __main__ 里必须有 finally + close_if_started。"""
    src = _src(path)
    assert "close_if_started" in src, "%s 没接用完即关" % os.path.basename(path)
    tail = src.split('if __name__ == "__main__":')[-1]
    assert "finally" in tail, "%s 的收尾不在 finally 里（异常会漏关）" % os.path.basename(path)
    assert "close_if_started" in tail
    assert port_expr in tail


def test_pw_hwc_flow_closes_on_exit():
    _main_block_runs_cleanup(os.path.join(POWER, "pw_hwc_flow.py"))


def test_pw_deliver_ship_closes_on_exit():
    _main_block_runs_cleanup(os.path.join(POWER, "pw_deliver_ship.py"))


def test_pw_hwc_flow_uses_tracked_ensure():
    src = _src(os.path.join(POWER, "pw_hwc_flow.py"))
    assert "ensure_tracked(" in src, "改用 ensure_tracked，否则不知道是不是自己起的"


def test_no_task_launcher_sets_keep_chrome():
    """定时任务的启动器不许设 PW_KEEP_CHROME=1 —— 那等于主动放弃回收。"""
    tasks = os.path.join(ROOT, "scripts", "tasks")
    bad = []
    for name in os.listdir(tasks):
        if not name.lower().endswith(".cmd"):
            continue
        if "PW_KEEP_CHROME" in _src(os.path.join(tasks, name)):
            bad.append(name)
    assert not bad, "这些启动器设了 PW_KEEP_CHROME：%s" % bad
