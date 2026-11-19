# -*- coding: utf-8 -*-
"""test_cap_duplicate_pages.py — 「定时任务自己开的页用完即关」的**数量上限**判据。

背景（2026-11-19 用户复报：「执行定时任务-temu店铺为什么又不关闭你自己打开的页面」）
-------------------------------------------------------------------------------
BHY 窗口实测 **9 个页里 5 个是 `goods/list`**。两个已有的清理器都收不了它：

· `close_new_seller_pages` 只能用「本次新出现」+「URL 没在 before_urls 里」来证明归属。
  用户本来就开着 1 个商品列表页时，之后**每一个**新开的同款页都等于"运行前就存在"
  ⇒ 全被判成用户的页，一个都不关。
· `sweep_transient_pages` 只碰 `CLOSE_IF_OWNED_HINTS`（纯过程页），而 `goods/list`
  在 `KEEP_PATH_HINTS` 里，永远轮不到。

⇒ 新增按**数量上限**收的 `cap_duplicate_pages`。本文件钉死它的安全边界。

跑法: python -m pytest scripts/power/tests/test_cap_duplicate_pages.py -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "lib"))

import cdp_transport as t  # noqa: E402

GOODS = "https://agentseller.temu.com/goods/list"
ORDERS = "https://agentseller-us.temu.com/mmsos/orders.html?fulfillmentMode=0"


def _p(tid, url, type_="page"):
    return {"id": tid, "targetId": tid, "type": type_, "url": url,
            "webSocketDebuggerUrl": "ws://x/%s" % tid}


def _run(monkeypatch, pages, **kw):
    closed = []
    monkeypatch.setattr(t, "list_pages", lambda port: pages)
    monkeypatch.setattr(t, "close_target", lambda port, tid: closed.append(tid) or True)
    out = t.cap_duplicate_pages(1, **kw)
    return out, closed


def test_caps_duplicates_to_one(monkeypatch):
    pages = [_p("A", GOODS), _p("B", GOODS), _p("C", GOODS)]
    out, closed = _run(monkeypatch, pages)
    assert [tid for tid, _ in out] == ["A", "B"], "应关掉除最新一个之外的全部"
    assert set(closed) == {"A", "B"}


def test_keeps_the_newest(monkeypatch):
    """留最新那个：晚开的是任务工作页，早开的旧页跑完就没人用。"""
    pages = [_p("old", GOODS), _p("new", GOODS)]
    out, _ = _run(monkeypatch, pages)
    assert [tid for tid, _ in out] == ["old"]
    assert [x for x in ("old", "new") if x not in [tid for tid, _ in out]] == ["new"]


def test_never_touches_orders_page(monkeypatch):
    """orders.html 是任务目标页，永远不在清理范围内（2026-10-27 事故）。"""
    pages = [_p("O1", ORDERS), _p("O2", ORDERS), _p("O3", ORDERS)]
    out, closed = _run(monkeypatch, pages)
    assert out == [] and closed == []


def test_never_touches_external_or_extension_pages(monkeypatch):
    pages = [_p("T1", "https://www.temu.com/x"), _p("T2", "https://www.temu.com/x"),
             _p("E1", "chrome-extension://abc/offscreen.html"),
             _p("E2", "chrome-extension://abc/offscreen.html")]
    out, closed = _run(monkeypatch, pages)
    assert out == [] and closed == []


def test_keeps_one_real_tab_left(monkeypatch):
    """窗口至少留 1 个真实页，否则店铺窗口会整个消失。"""
    pages = [_p("A", GOODS), _p("B", GOODS)]
    out, closed = _run(monkeypatch, pages, keep_min_tabs=2)
    assert out == [] and closed == []


def test_dry_run_reports_without_closing(monkeypatch):
    pages = [_p("A", GOODS), _p("B", GOODS), _p("C", GOODS)]
    out, closed = _run(monkeypatch, pages, dry_run=True)
    assert len(out) == 2            # 报告了
    assert closed == []             # 但一个都没关


def test_hints_cover_goods_list_and_exclude_orders():
    assert t._is_dup_prone(GOODS) is True
    assert t._is_dup_prone("https://agentseller.temu.com/mmsos/export-order.html") is True
    assert t._is_dup_prone("https://agentseller.temu.com/main/data-center/goods-data") is True
    assert t._is_dup_prone(ORDERS) is False


def test_never_raises_on_dead_port(monkeypatch):
    monkeypatch.setattr(t, "list_pages", lambda port: (_ for _ in ()).throw(RuntimeError("x")))
    assert t.cap_duplicate_pages(1) == []


# ---------------------------------------------------------------- 接入点
def test_wired_into_window_lock_finally():
    """必须在 `finally` 里收尾：任务被 Ctrl+C / 超时强杀时也要清。"""
    import io
    p = os.path.join(_HERE, "..", "..", "run_with_window_lock.py")
    src = io.open(p, encoding="utf-8").read()
    assert "cap_duplicate_pages" in src
    i = src.find("try:\n                r = subprocess.run(cmd)")
    assert i > 0, "没找到业务执行段"
    body = src[i:i + 700]
    assert "finally:" in body, "收尾清理不在 finally 里 —— 被强杀时会漏页"
    assert "_cap_duplicate_pages(a.window)" in body
