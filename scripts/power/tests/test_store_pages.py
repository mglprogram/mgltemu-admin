# -*- coding: utf-8 -*-
"""test_store_pages.py — 「默认页缺了就补、补的留着；自己开的即用即关」。

用户口径（2026-10-29）：「当你执行定时任务发现我默认的页面（商品列表、销量分析、
生命周期、订单列表）哪个页面不存在，帮我打开，你自己打开的即用即关。」

两类页必须分清，混在一起就是前三次事故：
  · **默认页**（四张）：缺就补，补完**留着**——它从此是默认页之一，不能被任何清障关掉。
    实现上不登记进 scratch 表，所以 `sweep_leaked_scratch` / `close_new_seller_pages` 碰不到它。
  · **任务自己的工作页**：`own_page.OwnPage` 开，退出即关。

本文件钉六条：
  ① 四张页齐全时**什么都不开**（幂等）；
  ② 缺哪张只补哪张，已存在的不重复开；
  ③ 窗口没开 ⇒ 跳过，**不替用户开窗**（开窗会杀掉他的页面）；
  ④ 补页过程绝不关任何已有页；
  ⑤ `run_with_window_lock` 取到锁后先补页、再跑业务，且补页失败不阻断业务；
  ⑥ 任务工作页（OwnPage）退出必关，异常路径也关。

运行: python -m pytest scripts/power/tests/test_store_pages.py -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_POWER))
for _p in (os.path.join(_POWER, "lib"), _REPO, os.path.join(_REPO, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import store_pages as sp  # noqa: E402
import ziniao_store as zs  # noqa: E402

FULL = ["https://agentseller.temu.com/goods/list",
        "https://agentseller.temu.com/newon/product-select",
        "https://agentseller.temu.com/main/data-center/goods-data",
        "https://agentseller-us.temu.com/mmsos/orders.html"]


def _t(url):
    return {"id": "t%d" % abs(hash(url)), "url": url, "type": "page"}


def _setup(monkeypatch, urls, port=57964, running=True):
    opened, closed = [], []
    monkeypatch.setattr(sp, "store_entry",
                        lambda s: {"ziniao_window_name": "TM-BHY-YJF"} if s == "BHY" else {})
    monkeypatch.setattr(zs, "probe_port_by_window", lambda w: port)
    monkeypatch.setattr(zs, "window_running", lambda w: running)
    monkeypatch.setattr(zs, "list_page_targets", lambda p: [_t(u) for u in urls])
    monkeypatch.setattr(zs, "open_tab", lambda p, u: opened.append(u) or "NEW")
    monkeypatch.setattr(zs, "close_target", lambda p, t: closed.append(t) or True)
    return opened, closed


# ---------------------------------------------------------------- ① 幂等
def test_all_pages_present_opens_nothing(monkeypatch):
    opened, closed = _setup(monkeypatch, FULL)
    r = sp.ensure_pages("BHY", log=lambda m: None)
    assert r["missing"] == [] and r["opened"] == []
    assert opened == [], "四张页都在时不许再开"


# ---------------------------------------------------------------- ② 只补缺的
def test_only_missing_pages_are_opened(monkeypatch):
    opened, _ = _setup(monkeypatch, FULL[:2])          # 有商品列表 + 生命周期
    r = sp.ensure_pages("BHY", log=lambda m: None)
    assert sorted(r["missing"]) == ["订单列表", "销量分析"]
    assert len(opened) == 2
    assert any("goods-data" in u for u in opened)
    assert any("/mmsos/orders" in u for u in opened)
    assert not any("/goods/list" in u for u in opened), "已有的不许重开"


def test_dry_run_never_opens(monkeypatch):
    opened, _ = _setup(monkeypatch, [])
    r = sp.ensure_pages("BHY", dry_run=True, log=lambda m: None)
    assert opened == []
    assert len(r["missing"]) == 4 and all(x.get("dry_run") for x in r["opened"])


# ---------------------------------------------------------------- ③ 不开窗
def test_skips_when_window_is_closed(monkeypatch):
    """窗口没开 ⇒ 只报跳过。**绝不**替用户开窗（开窗＝他的页面全没）。"""
    opened, _ = _setup(monkeypatch, [], port=0, running=False)
    called = {"open_store": 0}
    monkeypatch.setattr(zs, "open_store", lambda sid: called.__setitem__("open_store", 1) or True)
    r = sp.ensure_pages("BHY", log=lambda m: None)
    assert r["opened"] == [] and r["skipped"] == "窗口没开"
    assert called["open_store"] == 0, "不许为了补页去开窗"


def test_reports_when_window_alive_but_port_not_ready(monkeypatch):
    _, _ = _setup(monkeypatch, [], port=0, running=True)
    r = sp.ensure_pages("BHY", log=lambda m: None)
    assert "端口未就绪" in r["skipped"]


def test_unknown_store_is_skipped(monkeypatch):
    _setup(monkeypatch, FULL)
    r = sp.ensure_pages("NOPE", log=lambda m: None)
    assert r["skipped"].startswith("未知店")


# ---------------------------------------------------------------- ④ 只开不关
def test_ensure_never_closes_anything(monkeypatch):
    opened, closed = _setup(monkeypatch, FULL[:1])
    sp.ensure_pages("BHY", log=lambda m: None)
    assert closed == [], "补页只许开，绝不许关任何已有页（实关 %s）" % closed


# ---------------------------------------------------------------- ⑤ 定时任务接线
def test_window_lock_ensures_pages_before_running_business(monkeypatch):
    import run_with_window_lock as rwl

    seq = []
    monkeypatch.setattr(sp, "ensure_for_window_key",
                        lambda key, log=None: seq.append("ensure") or [])
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd: seq.append("run") or type(
        "R", (), {"returncode": 0})())
    class _Lk:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    import window_lock
    monkeypatch.setattr(window_lock, "window_lock", lambda *a, **k: _Lk())
    monkeypatch.setattr(sys, "argv", ["x", "--window", "BHY+BEL", "--", "echo", "hi"])
    rc = rwl.main()
    assert rc == 0
    assert seq == ["ensure", "run"], "必须先补默认页再跑业务（实际 %s）" % seq


def test_ensure_failure_does_not_block_business(monkeypatch):
    import run_with_window_lock as rwl
    import subprocess
    import window_lock

    def _boom(key, log=None):
        raise RuntimeError("port dead")
    monkeypatch.setattr(sp, "ensure_for_window_key", _boom)
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda cmd: ran.append(1) or type(
        "R", (), {"returncode": 0})())
    class _Lk:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(window_lock, "window_lock", lambda *a, **k: _Lk())
    monkeypatch.setattr(sys, "argv", ["x", "--window", "BHY", "--", "echo", "hi"])
    assert rwl.main() == 0
    assert ran == [1], "补页失败绝不能把业务拦住"


def test_no_ensure_pages_flag_skips_it(monkeypatch):
    import run_with_window_lock as rwl
    import subprocess
    import window_lock
    seq = []
    monkeypatch.setattr(sp, "ensure_for_window_key",
                        lambda key, log=None: seq.append("ensure") or [])
    monkeypatch.setattr(subprocess, "run", lambda cmd: seq.append("run") or type(
        "R", (), {"returncode": 0})())
    class _Lk:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(window_lock, "window_lock", lambda *a, **k: _Lk())
    monkeypatch.setattr(sys, "argv", ["x", "--window", "BHY", "--no-ensure-pages",
                                      "--", "echo", "hi"])
    assert rwl.main() == 0
    assert seq == ["run"]


# ---------------------------------------------------------------- ⑥ 工作页即用即关
def test_own_page_closes_on_exception(monkeypatch):
    import own_page
    closed = []
    monkeypatch.setattr(zs, "probe_port_by_window", lambda w: 57964)
    monkeypatch.setattr(zs, "open_tab", lambda p, u: "WORKTID")
    monkeypatch.setattr(zs, "close_target", lambda p, t: closed.append(t) or True)
    try:
        with own_page.OwnPage("x", "TM-BHY-YJF", log=lambda m: None):
            raise RuntimeError("业务炸了")
    except RuntimeError:
        pass
    assert closed == ["WORKTID"], "异常路径也必须关掉自己的工作页"


def test_default_pages_are_not_registered_as_scratch(monkeypatch):
    """默认页**不许**登记进 scratch 表 —— 否则会被 sweep 当残留关掉。"""
    src = open(os.path.join(_POWER, "lib", "store_pages.py"), encoding="utf-8").read()
    body = src.split("def ensure_pages", 1)[1]
    for bad in ("create_target(", "own_page", "OwnPage", "_reg_add"):
        assert bad not in body, "补默认页不该用 `%s`（那会进临时页登记表/被当成工作页）" % bad
