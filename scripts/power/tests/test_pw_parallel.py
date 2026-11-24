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
    """六店归两组、只用两个端口（TEMU 一个 chrome、SHEIN 一个 chrome）。

    ★ 2026-11-13 用户令：「BS/BD/BAN → SHEIN（端口 9334）：**加一个 BE**」
      —— BE 是 SHEIN 店（帕沃尔店 1640478289，库存全在帕沃尔仓）。
    """
    assert F.PW_GROUPS == {"BHY": "TEMU", "BEL": "TEMU",
                           #: ★ 2026-11-17 用户令：「在发货总链加上一个 TM-KinglamanSport
                           #:   店铺的发货」——TEMU 本土店，跟 BHY/BEL 共用 9333。
                           "KinglamanSport": "TEMU",
                           "BS": "SHEIN", "BD": "SHEIN", "BAN": "SHEIN",
                           "BE": "SHEIN"}, F.PW_GROUPS
    assert F.PW_GROUP_PORTS == {"TEMU": 9333, "SHEIN": 9334}, F.PW_GROUP_PORTS
    ports = {F.pw_port_of(s) for s in F.PW_GROUPS}
    assert ports == {9333, 9334}, ports


def test_store_lookup_is_case_insensitive():
    """驼峰店名（KinglamanSport）必须命中 TEMU 组，不能掉进"未知店=新端口"。

    ★ 2026-11-17 真机坑：`pw_group_of` 原来用 `.upper()` 的键去查大小写混合的
      `PW_GROUPS` ⇒ `KinglamanSport` miss ⇒ 拿到 9333+len(PW_GROUPS)=9340 的新端口，
      跟 BHY/BEL 抢不到同一把锁（等于给这家店单独开了一个帕沃尔实例）。
    """
    for name in ("KinglamanSport", "kinglamansport", "KINGLAMANSPORT"):
        assert F.pw_group_of(name) == "TEMU", name
        assert F.pw_port_of(name) == 9333, name


def test_be_is_a_shein_store():
    """BE 必须被认成 SHEIN 店（label_shein / export 走 OpenAPI）。"""
    assert F._is_shein_store("BE")
    assert F._shein_subject("BE") == "BE"
    assert F.pw_group_of("BE") == "SHEIN"


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


# ---------------------------------------------- 定时任务绝不许弹 Chrome 窗口
#
# 用户 2026-11-24：「这是哪个定时任务弹出来的 chrome，时不时弹出来…不弹窗口」。
# 弹窗只可能来自"有头"分支：默认值本来就是无头，但 `PW_HEADED=1` 留在环境里、
# 或某条路径走了有头，就会拉起可见窗口。无人值守时弹窗没有任何好处。

def test_unattended_forces_headless(monkeypatch):
    """`PW_UNATTENDED=1` ⇒ 即使 `PW_HEADED=1` 也按无头走。"""
    monkeypatch.setenv("PW_UNATTENDED", "1")
    assert pw_browser._unattended() is True


def test_unattended_flag_is_honoured_in_launch():
    """launch() 里必须**真的**把 headed 翻回 False（不能只是打印一句）。"""
    import inspect
    src = inspect.getsource(pw_browser.launch)
    assert "_unattended()" in src
    i = src.find("_unattended()")
    seg = src[i:i + 260]
    assert "headed = False" in seg, "无人值守时必须真的改成无头"
    assert "_headless_on" in src


def test_no_console_counts_as_unattended():
    """没有真实控制台（wscript/cmd 隐藏启动、DSH 子进程）也按无人值守处理。"""
    import inspect
    src = inspect.getsource(pw_browser._unattended)
    assert "GetConsoleWindow" in src


def test_task_launchers_pin_unattended():
    """每个隐藏启动的 .cmd 都要显式钉住 `PW_UNATTENDED=1` + `PW_HEADED=0`。

    WHY（2026-11-24）：两层保险。python 侧靠"有没有控制台"判断，
    .cmd 侧则无条件钉死 —— 万一将来有人把 python 侧判据改坏，
    定时任务仍然不会弹出窗口。
    """
    import glob
    import io
    import os
    root = os.path.join(os.path.dirname(pw_browser.__file__), "..", "..", "tasks")
    files = [p for p in glob.glob(os.path.join(root, "*.cmd"))
             if "run_" in os.path.basename(p)]
    # 这个是被别的任务 `cmd /c` 调用的**内层循环体**（只循环店铺），自己不拥有进程、
    # 也不起浏览器 —— 无人值守由外层 run_temu_bel_abnormal.cmd 钉住。
    skip = {"run_temu_abnormal_two.cmd"}
    files = [p for p in files if os.path.basename(p) not in skip]
    assert files, "没找到任务启动脚本"
    missing = []
    for p in files:
        s = io.open(p, encoding="utf-8", errors="replace").read()
        if "PW_UNATTENDED=1" not in s:
            missing.append(os.path.basename(p))
    assert not missing, "这些启动脚本没钉住无人值守：%s" % ", ".join(missing)
