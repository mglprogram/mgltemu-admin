# -*- coding: utf-8 -*-
"""test_auth_both_domains.py — 「所有页面登录态都失效」时，两套授权必须一起过。

来历（用户 2026-11-09 第二次报同一现象，截图里 3 个页签全在
`agentseller.temu.com/auth/authentication`）
--------------------------------------------------------------------
真因链（当天日志实证）：

  11:00  TEMU-Overdue-Confirm-Daily 发现 BEL 窗口进程在、但 CDP 端口不监听
  11:14  自愈用了 `ziniao_store.restart_and_reprobe` → close→open 重开 BEL 窗口
         （这一步**必然**丢会话：新的浏览器进程 + 新端口）
  11:20  事后只补了 `us`（订单页那条线），`cc`（商品列表/销量分析/生命周期）没人补
  ⇒ 三张 cc 页永远停在 auth/authentication，用户看到的就是"所有页面都要重新登录"

`agentseller-us.temu.com`（us）与 `agentseller.temu.com`（cc）是**两套独立授权**，
一套过了不代表另一套能用。所以「窗口被重开过」之后必须两套一起补。
"""
from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
_ENTRY = os.path.join(_PROJ, "scripts", "power", "run_temu_auth.py")
_DRIVER = os.path.join(_PROJ, "scripts", "power", "lib", "auth_temu_driver.py")


def _src(p: str) -> str:
    return open(p, encoding="utf-8").read()


# ---------------------------------------------------------------- ① 入口支持 both
def test_entry_accepts_both_domain():
    s = _src(_ENTRY)
    m = re.search(r'"--domain"[^)]*choices=\[([^\]]*)\]', s, re.S)
    assert m, "找不到 --domain 的 choices"
    assert '"both"' in m.group(1), "入口必须接受 `--domain both`（两套一起过）"


def test_both_runs_us_first_then_cc():
    """顺序有意义：us 是订单页主线（所有业务都依赖），先过它。"""
    s = _src(_ENTRY)
    m = re.search(r'domains = .*?args\.domain == "both".*?\n', s)
    assert m, "找不到 domains 的展开逻辑"
    line = m.group(0)
    assert '["us", "cc"]' in line, "both 必须展开成 us 在前、cc 在后"


def test_both_stops_at_first_failure():
    """一套失败就停 —— 继续过下一套只会把窗口搅得更乱。"""
    s = _src(_ENTRY)
    i = s.find("for dom in domains:")
    seg = s[i:i + 1200]
    assert "break" in seg, "某一套授权失败后必须 break"
    assert "startswith(\"OK\")" in seg


# ---------------------------------------------------------------- ② 清障后复检窗口
def test_cleanup_rechecks_window_and_reopens():
    """清障把窗口关没时必须当场重开，不能留给下一个任务去连死端口。

    2026-11-09 实测：授权成功后跑 `clean_dead_tabs`，它把仅剩的真实页也关掉，
    窗口随之消失（端口 63788 → 0），下一轮直接 `[HARD STOP] 未探测到 CDP 端口`。
    """
    s = _src(_ENTRY)
    # 锚在**成功分支**那句注释上（文件里还有一句"授权后清理跳过"在 except 里，别锚错）
    i = s.find("if verdict == \"ok\":")
    assert i > 0, "找不到授权成功分支"
    seg = s[i:i + 2000]
    assert "clean_dead_tabs" in seg, "成功分支里应有清障"
    assert "window_alive" in seg, "清障后必须复检 window_alive"
    assert "open_store" in seg, "窗口被清没时必须 open_store 重开"


# ---------------------------------------------------------------- ③ 两域判据独立
def test_driver_domains_are_independent():
    s = _src(_DRIVER)
    assert "DOMAIN_PROBE_URLS" in s
    assert "agentseller-us.temu.com" in s and "agentseller.temu.com" in s
    # `_url_is_seller_page` 必须吃 domain 参数（否则两套被当成一套）
    m = re.search(r"def _url_is_seller_page\(([^)]*)\)", s)
    assert m and "domain" in m.group(1), "_url_is_seller_page 必须接受 domain 参数"


# ---------------------------------------------------------------- 2026-11-16 需人工登录告警
def test_auth_failure_with_human_hints_alerts_once_per_day(tmp_path, monkeypatch):
    """自动授权失败且是「要人工」的那类 → 当天只推一次，别让十几个任务各推一遍。

    背景：11-14~15 BEL 会话票过期，自动授权卡在「点确认授权没推进」，之后
    十几个任务轮流 rc=42 跑了一整个周末，没有任何一条消息说「BEL 需要人登录」。
    """
    import importlib.util
    import sys as _sys

    sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # scripts/
    if sys_path not in _sys.path:
        _sys.path.insert(0, sys_path)
    spec = importlib.util.spec_from_file_location(
        "run_temu_auth_mod", os.path.join(sys_path, "power", "run_temu_auth.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    calls = []
    monkeypatch.setattr(mod, "LOG_DIR", str(tmp_path))
    import notify_push
    monkeypatch.setattr(notify_push, "push_group", lambda t, m, url=None: (calls.append("group") or (True, "ok")))
    monkeypatch.setattr(notify_push, "push_meow", lambda t, m, url=None: (calls.append("meow") or (True, "ok")))

    # 不是「要人工」的失败 → 不打扰
    mod._alert_human("BEL", "FAIL: 端口没探到")
    assert calls == []

    # 要人工 → 飞书 + 手机各一次
    mod._alert_human("BEL", "FAIL: 授权未完成（可能需人工验证码/多次失败）")
    assert calls == ["group", "meow"]

    # 同一天第二次（另一个任务）→ 不重复推
    mod._alert_human("BEL", "FAIL: 授权确认按钮点了没推进")
    assert calls == ["group", "meow"]

    # 另一家店独立计数
    mod._alert_human("BHY", "FAIL: 验证码")
    assert len(calls) == 4
