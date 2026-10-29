# -*- coding: utf-8 -*-
"""test_hung_heal_ownership.py — 卡死页清理**只许关自己登记的页**。

来历（用户 2026-10-29 截图）：「为什么覆盖我的页面，然后自己又开了一个新页」。
现场（BHY 窗口实测）：5 个页里 3 个被判 hung —— `goods/list`、一个 `www.temu.com`
商品页、一个销量分析页。`fetch_focus_spus._heal_hung` 把它们**全关了**，
随后 `prepare()` 又新开一张销量分析页 ⇒ 用户看到「我的页没了 + 凭空多一个销量分析页」。

判据必须回到**归属**：`cdp_transport.registered_targets()`（`create_target` 建页时
落盘的 targetId）。登记表里没有的 = 用户的页，永不关闭、连判都不判。

运行: python -m pytest scripts/power/tests/test_hung_heal_ownership.py -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_POWER))
for _p in (os.path.join(_POWER, "lib"), os.path.join(_REPO, "scripts", "bel_goods")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cdp_transport as cdp  # noqa: E402
import fetch_focus_spus as ff  # noqa: E402


def _t(tid, url):
    return {"id": tid, "targetId": tid, "url": url, "type": "page",
            "webSocketDebuggerUrl": "ws://x/%s" % tid}


def test_registered_targets_reads_only_our_scratch(tmp_path, monkeypatch):
    reg = tmp_path / "scratch_tabs.json"
    reg.write_text('{"tabs": [{"tid": "MINE", "port": 57964}, {"tid": "OTHER", "port": 50070}]}',
                   encoding="utf-8")
    monkeypatch.setattr(cdp, "_SCRATCH_REG", reg)
    assert cdp.registered_targets(57964) == {"MINE"}, "要按端口过滤"
    assert cdp.registered_targets() == {"MINE", "OTHER"}


def test_heal_never_touches_unregistered_hung_pages(monkeypatch):
    """**事故原型**：用户页全部 hung，但我们一个都不许关。"""
    pages = [_t("USER1", "https://agentseller.temu.com/goods/list"),
             _t("USER2", "https://www.temu.com/some-product"),
             _t("USER3", "https://agentseller.temu.com/main/data-center/goods-data")]
    closed = []
    monkeypatch.setattr(cdp, "list_pages", lambda p: pages)
    monkeypatch.setattr(cdp, "registered_targets", lambda p=0: set())
    monkeypatch.setattr(cdp, "probe_page_alive", lambda p, t, timeout=8.0: "hung")
    monkeypatch.setattr(cdp, "recover_hung_page",
                        lambda p, t, log=None, timeout=8.0, require_other_tabs=1:
                        closed.append(t.get("id")) or "closed")
    ff._heal_hung(57964, "TM-BHY-YJF")
    assert closed == [], "用户的页一个都不许关（实关 %s）" % closed


def test_heal_closes_only_registered_hung_page(monkeypatch):
    """自己开的页卡死了要清（否则会拖累后续建页），但只清那一张。"""
    pages = [_t("MINE", "https://agentseller.temu.com/main/data-center/goods-data"),
             _t("USER1", "https://agentseller.temu.com/goods/list")]
    closed = []
    monkeypatch.setattr(cdp, "list_pages", lambda p: pages)
    monkeypatch.setattr(cdp, "registered_targets", lambda p=0: {"MINE"})
    monkeypatch.setattr(cdp, "probe_page_alive", lambda p, t, timeout=8.0: "hung")
    monkeypatch.setattr(cdp, "recover_hung_page",
                        lambda p, t, log=None, timeout=8.0, require_other_tabs=1:
                        closed.append(t.get("id")) or "closed")
    monkeypatch.setattr(cdp, "find_port_after_recovery", lambda w: 0)
    ff._heal_hung(57964, "TM-BHY-YJF")
    assert closed == ["MINE"]


def test_heal_skips_alive_registered_page(monkeypatch):
    closed = []
    monkeypatch.setattr(cdp, "list_pages", lambda p: [_t("MINE", "x")])
    monkeypatch.setattr(cdp, "registered_targets", lambda p=0: {"MINE"})
    monkeypatch.setattr(cdp, "probe_page_alive", lambda p, t, timeout=8.0: "alive")
    monkeypatch.setattr(cdp, "recover_hung_page",
                        lambda *a, **k: closed.append(1) or "closed")
    ff._heal_hung(57964, "TM-BHY-YJF")
    assert closed == []


def test_source_uses_registry_not_all_pages():
    """源码级：`_heal_hung` 里必须出现归属过滤，别退回"遍历所有页"。"""
    src = open(os.path.join(_REPO, "scripts", "bel_goods", "fetch_focus_spus.py"),
               encoding="utf-8").read()
    body = src.split("def _heal_hung", 1)[1].split("def scrape_all", 1)[0]
    assert "registered_targets" in body, "缺归属过滤"
    assert "tid not in mine" in body, "必须先判归属再判死活"
