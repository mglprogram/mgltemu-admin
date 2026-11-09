# -*- coding: utf-8 -*-
"""test_pw_browser_mode.py — 帕沃尔 Chrome 的**有头/无头**模式钉子。

用户 2026-11-09 令：
    「把帕沃尔仓发货改成有头」

为什么要钉（踩过两次，方向还相反）：
  · 2026-11-07 一度改成无头（无人值守跑通），结果**点错 / 点在视口外 /
    弹窗没等到**全都看不出来，只能事后翻截图猜；
  · 2026-11-09 改回有头，但原实现有三个坑：
    ① 默认值仍是「无头」，靠 `PW_HEADED=1` 才切 —— 用户要的是「改成有头」，
       默认就该有头，否则等于没改；
    ② `close()` 用 `taskkill /FI "COMMANDLINE eq *--headless=new*"` ——
       **taskkill 的过滤器不支持通配**，实测报
       `ERROR: The search filter cannot be recognized.`，旧的无头实例
       一直占着 9333，新模式起不来还被判成「已有实例，复用」；
    ③ `_headless_on()` 取「第一个匹配进程」，但 renderer 子进程也带
       `--remote-debugging-port` 且**不带** `--headless` ⇒ 无头实例被误判成有头，
       于是跳过「关掉重开」。

这些都只做**纯逻辑**校验（读 env / 读源码），不起浏览器。
"""
from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_PROJ, "scripts", "power", "lib"))

SRC_PATH = os.path.join(_PROJ, "scripts", "power", "lib", "pw_browser.py")


def _src() -> str:
    with open(SRC_PATH, encoding="utf-8") as fh:
        return fh.read()


def _import():
    """导入 pw_browser（会连带 cdp_transport；不起浏览器）。"""
    import pw_browser                                       # noqa: PLC0415
    return pw_browser


# ---------------------------------------------------------------- 默认值

def test_headed_is_the_default(monkeypatch):
    """不设任何环境变量时必须是**有头**。"""
    monkeypatch.delenv("PW_HEADED", raising=False)
    src = _src()
    m = re.search(r'os\.environ\.get\("PW_HEADED"\)\s*or\s*"([^"]*)"', src)
    assert m, "PW_HEADED 的默认值写法变了，请同步本测试"
    assert m.group(1) == "1", (
        '默认值应是 "1"（有头）；现在是 %r ⇒ 用户要的「改成有头」等于没生效' % m.group(1))


def test_headless_only_when_explicitly_disabled(monkeypatch):
    """`PW_HEADED=0` / false / no 才无头。"""
    src = _src()
    m = re.search(r'headed = str\(os\.environ\.get\("PW_HEADED"\)[^\n]*\n', src)
    assert m, "headed 的判定行找不到了"
    line = m.group(0)
    for off in ("0", "false", "no"):
        assert off in line, "关闭值 %r 没在判定里" % off


def test_headed_flag_controls_the_chrome_arg():
    """只有无头时才追加 `--headless=new`（不能无条件加，也不能无条件不加）。"""
    src = _src()
    assert "if not headed:" in src
    i = src.find("if not headed:")
    seg = src[i:i + 200]
    assert "--headless=new" in seg, "有头分支里还在加 --headless=new"


# ---------------------------------------------------------------- close 的坑

def test_close_does_not_use_taskkill_wildcard_filter():
    """taskkill 的 `/FI COMMANDLINE eq *...*` **不支持通配**，等于什么都没杀。"""
    src = _src()
    assert '"/FI"' not in src or "COMMANDLINE eq *" not in src, (
        "又用 taskkill 通配过滤了 → 实测报 `The search filter cannot be recognized.`，"
        "旧实例杀不掉，新模式起不来")


def test_close_kills_by_pid_tree():
    """必须按 PID + `/T` 杀整棵进程树。"""
    src = _src()
    i = src.find("def close(")
    assert i > 0
    seg = src[i:i + 1200]
    assert "/T" in seg, "没杀进程树，renderer 子进程会残留"
    assert "/PID" in seg, "没按 PID 杀"


def test_pid_lookup_skips_renderer_children():
    """找主进程时必须排除 `--type=` 子进程。"""
    src = _src()
    i = src.find("def _pids_on_port(")
    assert i > 0
    seg = src[i:i + 1200]
    assert "--type=" in seg, "没排除子进程 → 会拿到 renderer 的 PID"


def test_headless_probe_reads_main_process_only():
    """`_headless_on` 只看主进程，否则会误判。"""
    src = _src()
    i = src.find("def _headless_on(")
    assert i > 0
    seg = src[i:i + 1200]
    assert "--type=" in seg, (
        "取第一个匹配进程会把 renderer 子进程当成主进程 "
        "⇒ 无头实例被误判成有头，跳过「关掉重开」")


# ---------------------------------------------------------------- 与发货链路的关系

def test_fulfill_entrypoints_go_through_pw_browser():
    """三个帕沃尔发货入口都必须走 pw_browser.ensure()，不能自己起 Chrome。"""
    for rel in ("scripts/power/pw_deliver_ship.py",
                "scripts/power/pw_hwc_flow.py",
                "scripts/power/pw_fail_fix.py"):
        p = os.path.join(_PROJ, rel)
        src = open(p, encoding="utf-8").read()
        assert "pw_browser.ensure(" in src, "%s 没走 pw_browser.ensure()" % rel
        assert "--headless=new" not in src, (
            "%s 自己写死了 --headless=new，绕过了 pw_browser 的模式判定" % rel)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
