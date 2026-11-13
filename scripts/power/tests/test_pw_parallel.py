# -*- coding: utf-8 -*-
"""test_pw_parallel.py — 帕沃尔「两个无头实例：TEMU 一组 + SHEIN 一组」的回归钉。

用户 2026-11-13 最终口径：「**TEMU 店铺一个无头 chrome + SHEIN 店铺一个无头 chrome**」
⇒ ① 两套实例、两套 profile：TEMU 组（BHY/BEL）端口 9333、SHEIN 组（BS/BD/BAN）端口 9334；
   ② 组间**并行**、组内**串行**（锁键 `PW-TEMU` / `PW-SHEIN`）；
   ③ 默认无头（有头只作人肉核对，`PW_HEADED=1`）。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_SCRIPTS = os.path.dirname(_POWER)
for p in (_POWER, os.path.join(_POWER, "lib"), _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import pw_browser  # noqa: E402
import run_temu_fulfill as F  # noqa: E402


def test_two_groups_two_ports():
    """五店归两组、只用两个端口（TEMU 一个 chrome、SHEIN 一个 chrome）。"""
    assert F.PW_GROUPS == {"BHY": "TEMU", "BEL": "TEMU",
                           "BS": "SHEIN", "BD": "SHEIN", "BAN": "SHEIN"}, F.PW_GROUPS
    assert F.PW_GROUP_PORTS == {"TEMU": 9333, "SHEIN": 9334}, F.PW_GROUP_PORTS
    ports = {F.pw_port_of(s) for s in F.PW_GROUPS}
    assert ports == {9333, 9334}, ports


def test_group_and_port_lookup():
    assert F.pw_group_of("BHY") == "TEMU"
    assert F.pw_group_of("BEL") == "TEMU"
    assert F.pw_group_of("BS") == "SHEIN"
    assert F.pw_group_of("ban") == "SHEIN", "小写也要认"
    assert F.pw_port_of("BHY") == F.pw_port_of("BEL") == 9333
    assert F.pw_port_of("BS") == F.pw_port_of("BD") == F.pw_port_of("BAN") == 9334


def test_each_group_gets_its_own_profile():
    """两个端口各自一份 profile（同一个 user-data-dir 不能被两个 Chrome 占用）。"""
    a = pw_browser.profile_dir(9333)
    b = pw_browser.profile_dir(9334)
    assert a != b, (a, b)
    assert a.endswith(".chrome-headless-profile"), a
    assert b.endswith(".chrome-headless-profile-9334"), b


def test_lock_is_per_group(monkeypatch):
    """锁键按组：TEMU 组拿 `PW-TEMU`，组内串行、组间不互斥。"""
    import run_pw_fulfill as R  # noqa: PLC0415
    from window_lock import status  # noqa: PLC0415
    quiet = lambda *a, **k: None  # noqa: E731
    monkeypatch.setenv("PW_LOCK_KEY", "PW-TEMU")
    with R._pw_lock("query", "BEL", log=quiet):
        keys = {r["file"] for r in status()}
        assert "PW-TEMU" in keys, keys
    assert not [r for r in status() if r["file"] == "PW-TEMU"]


def test_global_lock_can_still_be_forced(monkeypatch):
    """`PW_LOCK_KEY=PW` 仍可回退成全局串行（出问题时的刹车）。"""
    import run_pw_fulfill as R  # noqa: PLC0415
    from window_lock import status  # noqa: PLC0415
    quiet = lambda *a, **k: None  # noqa: E731
    monkeypatch.setenv("PW_LOCK_KEY", "PW")
    with R._pw_lock("query", "BEL", log=quiet):
        assert "PW" in {r["file"] for r in status()}


def test_headless_is_the_default():
    """默认无头（`PW_HEADED` 不设或 =0 时 headed=False）。"""
    code = open(pw_browser.__file__, encoding="utf-8").read()
    assert 'os.environ.get("PW_HEADED") or "0"' in code, "默认值必须是无头"
