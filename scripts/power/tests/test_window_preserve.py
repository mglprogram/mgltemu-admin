# -*- coding: utf-8 -*-
"""test_window_preserve.py — 钉死「定时任务的窗口自愈**不许**靠关窗」这条规矩。

来历（用户 2026-10-29 原话）：「处理关于触发定时任务去两个店铺开新标签页，把我之前开的页面也杀掉的问题」。

当天事故（日志 `_logs/temu-abnormal/2026-10-29.jsonl` 可复现）：
  · 08:30 起 BEL 异常单任务连续失败；
  · `temu_abnormal_driver.ensure_auth()` 旧实现**只要授权没点过就 close→open**；
  · 26 分钟内 BEL 窗口被重开 6 次（端口 64609→59611→49606→59745→59789→65173），
    用户自己打开的商品页/订单页全部消失，窗口里只剩一张登录页；
  · 另一个同源破坏面：`run_temu_sales.py` **导出成功就关整个店铺窗口**（旧规则 2026-08-31），
    活干完了照样把用户页面一起杀掉。

本文件钉四条：
  ① 窗口进程还活着 ⇒ 授权自愈**只做轻量清理 + 原窗口重试**，不关窗；
  ② 窗口进程真不在了 ⇒ 才允许重开（那时已无可损失）；
  ③ 万不得已重开（`restart_and_reprobe`，CDP 握手卡死的既有自愈）⇒ 先记标签页、重开后**开回来**；
  ④ "要不要关窗"的判据是「窗口里还有没有用户页面」（`ziniao_store.user_tabs`）。

运行: python -m pytest scripts/power/tests/test_window_preserve.py -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
for _p in (os.path.join(_POWER, "lib"), _POWER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

import ziniao_store as zs  # noqa: E402
import temu_abnormal_driver as tad  # noqa: E402
import temu_buylabel_driver as tbd  # noqa: E402

LOGIN = "https://seller.kuajingmaihuo.com/login?redirectUrl=%2Fmmsos"
AUTH = "https://agentseller-us.temu.com/auth/authentication?redirectUrl=x"
ORDERS = "https://agentseller-us.temu.com/mmsos/orders.html"
GOODS = "https://agentseller-us.temu.com/main/data-center/goods-data"
EXT = "chrome-extension://dkmfchocknfallppgbpegkppapkbjhml/offscreen.html"

ABNORMAL_CFG = {"url": "https://agentseller.temu.com/lgst/auth-warehouse/abnormal-order",
                "url_key": "abnormal-order"}


def _driver(msgs=None):
    return tad.TemuAbnormalDriver(ABNORMAL_CFG, log=(msgs.append if msgs is not None else (lambda m: None)),
                                  store="BEL")


# ---------------------------------------------------------------- ① 窗口还在 ⇒ 不关窗
def test_ensure_auth_light_heals_instead_of_closing_the_window(monkeypatch):
    """窗口活着时授权失败：清无响应页 + 原窗口重试一次，**绝不 close→open**。"""
    d = _driver()
    calls, closed = [], []
    monkeypatch.setattr(d, "_clean_tabs", lambda stage: None)
    monkeypatch.setattr(d, "_light_heal", lambda: calls.append("heal"))
    monkeypatch.setattr(tad, "_store_entry",
                        lambda s: {"ziniao_window_name": "TM-BEL-MGL", "ziniao_window_store_id": "sid-bel"})
    monkeypatch.setattr(zs, "window_running", lambda w: True)
    monkeypatch.setattr(zs, "close_store", lambda sid: closed.append(sid) or True)
    monkeypatch.setattr(zs, "open_store", lambda sid: True)

    def _always_fail():
        calls.append("auth")
        raise RuntimeError("点不过去")

    monkeypatch.setattr(d, "_ensure_auth_once", _always_fail)

    with pytest.raises(RuntimeError) as ei:
        d.ensure_auth()
    assert "heal" in calls, "必须先做轻量自愈"
    assert calls.count("auth") == 2, "轻量自愈后原窗口再点一次（不是关窗）"
    assert closed == [], "窗口还在却去关窗＝把用户页面全杀掉"
    assert "没有关窗重开" in str(ei.value), "报错里要讲清「是故意不关窗」：%s" % ei.value

def test_restart_store_window_refuses_when_window_is_alive(monkeypatch):
    """`_restart_store_window` 自己也要有闸：进程还在就返回 0，close/open 一个都不许调。"""
    d = _driver()
    monkeypatch.setattr(tad, "_store_entry",
                        lambda s: {"ziniao_window_name": "TM-BEL-MGL", "ziniao_window_store_id": "sid-bel"})
    monkeypatch.setattr(zs, "window_running", lambda w: True)
    closed, opened = [], []
    monkeypatch.setattr(zs, "close_store", lambda sid: closed.append(sid) or True)
    monkeypatch.setattr(zs, "open_store", lambda sid: opened.append(sid) or True)
    monkeypatch.setattr(zs, "probe_port_by_window", lambda w: 65173)

    assert d._restart_store_window() == 0
    assert closed == [] and opened == [], "窗口还活着 → 不许关窗重开（实关 %s / 实开 %s）" % (closed, opened)


# ---------------------------------------------------------------- ② 窗口真没了 ⇒ 允许重开
def test_restart_store_window_reopens_when_window_is_gone(monkeypatch):
    d = _driver()
    monkeypatch.setattr(tad, "_store_entry",
                        lambda s: {"ziniao_window_name": "TM-BEL-MGL", "ziniao_window_store_id": "sid-bel"})
    monkeypatch.setattr(zs, "window_running", lambda w: False)
    closed, opened = [], []
    monkeypatch.setattr(zs, "close_store", lambda sid: closed.append(sid) or True)
    monkeypatch.setattr(zs, "open_store", lambda sid: opened.append(sid) or True)
    monkeypatch.setattr(zs, "probe_port_by_window", lambda w: 53380)
    monkeypatch.setattr(d, "_clean_tabs", lambda stage: None)

    assert d._restart_store_window() == 53380
    assert closed == ["sid-bel"] and opened == ["sid-bel"], "窗口本来就不在 → 重开是无损的"


# ---------------------------------------------------------------- ③ 不得不重开 ⇒ 把页面开回来
def test_restart_and_reprobe_snapshots_and_restores_tabs(monkeypatch):
    """CDP 卡死时 `restart_and_reprobe` 仍会重开（既有自愈），但必须把用户的页开回来。"""
    calls = {}
    monkeypatch.setattr(zs, "close_store", lambda sid: True)
    monkeypatch.setattr(zs, "open_store", lambda sid: True)
    monkeypatch.setattr(zs, "probe_port_by_window", lambda w: 4321)
    monkeypatch.setattr(zs, "snapshot_tabs", lambda p: [GOODS, ORDERS])
    monkeypatch.setattr(zs, "restore_tabs",
                        lambda p, urls, log=None: calls.setdefault("restore", (p, list(urls))) or list(urls))
    monkeypatch.setattr(zs.time, "sleep", lambda s: None)      # 免等 6 秒

    port = zs.restart_and_reprobe("sid-bel", "TM-BEL-MGL", wait=0, poll_timeout=5, port=65173)
    assert port == 4321
    assert calls.get("restore") == (4321, [GOODS, ORDERS]), "重开后必须把原来的页开回来：%s" % (calls,)


# ---------------------------------------------------------------- ④ 关窗判据＝有没有用户页面
def test_user_tabs_ignores_regenerable_entry_pages(monkeypatch):
    monkeypatch.setattr(zs, "list_page_targets", lambda p: [
        {"id": "a", "url": LOGIN, "type": "page"},
        {"id": "b", "url": AUTH, "type": "page"},
        {"id": "c", "url": "about:blank", "type": "page"},
        {"id": "d", "url": EXT, "type": "page"},
        {"id": "e", "url": ORDERS, "type": "page"},
        {"id": "f", "url": GOODS, "type": "page"},
    ])
    assert zs.user_tabs(1) == [ORDERS, GOODS]


def test_user_tabs_empty_when_only_entry_pages(monkeypatch):
    monkeypatch.setattr(zs, "list_page_targets", lambda p: [
        {"id": "a", "url": LOGIN, "type": "page"},
        {"id": "b", "url": "about:blank", "type": "page"},
    ])
    assert zs.user_tabs(1) == [], "只剩入口/空白页 ⇒ 关窗无损（旧规则可以走）"


def test_user_tabs_never_raises_on_dead_port(monkeypatch):
    def _boom(port):
        raise RuntimeError("port dead")

    monkeypatch.setattr(zs, "list_page_targets", _boom)
    assert zs.user_tabs(9999) == []


# ---------------------------------------------------------------- ⑤ 面单域同样不许关窗
def test_buylabel_restart_refuses_when_window_alive(monkeypatch):
    closed, opened = [], []
    monkeypatch.setattr(zs, "window_running", lambda w: True)
    monkeypatch.setattr(zs, "probe_port_by_window", lambda w: 57964)
    monkeypatch.setattr(zs, "close_hung_tabs", lambda p, log=None: {"closed": []})
    monkeypatch.setattr(zs, "close_store", lambda sid: closed.append(sid) or True)
    monkeypatch.setattr(zs, "open_store", lambda sid: opened.append(sid) or True)

    assert tbd.restart_store_window("BEL", log=lambda m: None) == 0
    assert closed == [] and opened == [], "窗口还活着 → 面单域也不许关窗重开"
