# -*- coding: utf-8 -*-
"""test_pw_parallel.py — 帕沃尔「五店并行 + 无头」的回归钉。

用户 2026-11-13 口径：「五个店铺的帕沃尔发货改成无头，并且并行发货五个店铺」
⇒ ① 每店一个 Chrome 端口 + **独立 profile**（同一 user-data-dir 不能被两个进程占用）；
   ② 锁粒度从全局 `PW` 收敛到 `PW-<店>`（同店仍串行，不同店互不排队）；
   ③ 默认无头（有头只作人肉核对用，`PW_HEADED=1`）。
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


def test_five_stores_have_distinct_ports():
    """五店五个不同端口（并行前提）。"""
    assert set(F.PW_PORTS) == {"BHY", "BEL", "BS", "BD", "BAN"}, F.PW_PORTS
    ports = list(F.PW_PORTS.values())
    assert len(set(ports)) == 5, ports
    assert min(ports) >= 1024


def test_each_port_gets_its_own_profile():
    """端口 → 独立 profile 目录；9333 沿用老目录（不迁移 cookie）。"""
    base = pw_browser.profile_dir(9333)
    other = pw_browser.profile_dir(9334)
    assert base != other, (base, other)
    assert base.endswith(".chrome-headless-profile"), base
    assert other.endswith(".chrome-headless-profile-9334"), other
    for port in F.PW_PORTS.values():
        assert pw_browser.profile_dir(port)


def test_pw_lock_is_per_store(monkeypatch):
    """锁键是 `PW-<店>`：两家店各拿各的，同店第二次才互斥。"""
    import run_pw_fulfill as R  # noqa: PLC0415
    from window_lock import status  # noqa: PLC0415
    monkeypatch.delenv("PW_LOCK_KEY", raising=False)
    quiet = lambda *a, **k: None  # noqa: E731
    with R._pw_lock("query", "BEL", log=quiet):
        keys = {r["file"] for r in status()}
        assert "PW-BEL" in keys, keys
        assert "PW" not in keys, "不能再回到全局 PW 串行锁"
    assert not [r for r in status() if r["file"] == "PW-BEL"]


def test_global_lock_can_still_be_forced(monkeypatch):
    """`PW_LOCK_KEY=PW` 仍可回退成全局串行（出问题时的刹车）。"""
    import run_pw_fulfill as R  # noqa: PLC0415
    from window_lock import status  # noqa: PLC0415
    monkeypatch.setenv("PW_LOCK_KEY", "PW")
    quiet = lambda *a, **k: None  # noqa: E731
    with R._pw_lock("query", "BEL", log=quiet):
        assert "PW" in {r["file"] for r in status()}


def test_headless_is_the_default():
    """默认无头（`PW_HEADED` 不设或 =0 时 headed=False）。"""
    src = pw_browser.launch.__doc__ or ""
    assert "无头" in src
    code = open(pw_browser.__file__, encoding="utf-8").read()
    assert 'os.environ.get("PW_HEADED") or "0"' in code, "默认值必须是无头"
