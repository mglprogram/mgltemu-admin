# -*- coding: utf-8 -*-
"""test_close_transient_pages.py — 跑完要关掉「这次冒出来的过程页」。

来历（用户 2026-11-12 截图）
--------------------------
紫鸟窗口里堆着 `订单详情` + `在线下单` ×2 + `商品流量` —— 全是脚本跑完该走却没走的。

真因不是"没调用清理"，而是清理器**判它们不许碰**：
`cdp_transport.KEEP_PATH_HINTS` 里有 `/mmsos/` 和 `/main/` 两条通配，而商家中心
几乎所有页都落在这两个前缀下 ⇒ 「批量在线下单」「订单详情」「导出订单」「商品流量」
一律被当成"用户工作面"放过，一天堆几个。

修法：另立一张 `CLOSE_IF_OWNED_HINTS`（用完就该走的过程页），
命中它 + **能证明是我方开的** ⇒ 关；证明不到 ⇒ 仍然不碰（不误杀用户页）。
`orders.html` 故意留在保护里（2026-10-27 事故：关掉它窗口退回登录页）。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)          # scripts/power
for _p in (os.path.join(_POWER, "lib"), _POWER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cdp_transport as cdp  # noqa: E402

SELLER = "agentseller-us.temu.com"


def _page(tid, url):
    return {"targetId": tid, "id": tid, "type": "page", "url": url,
            "webSocketDebuggerUrl": "ws://x/%s" % tid}


def _run(monkeypatch, pages, *, before_ids=(), before_urls=(),
         owned_ids=(), reg=(), openers=None):
    """跑一次 close_new_seller_pages，返回被关掉的 tid 列表。"""
    closed = []
    monkeypatch.setattr(cdp, "list_pages", lambda port: list(pages))
    monkeypatch.setattr(cdp, "_openers_of", lambda port: dict(openers or {}))
    monkeypatch.setattr(cdp, "_reg_load", lambda: list(reg))
    monkeypatch.setattr(cdp, "close_target",
                        lambda port, tid: (closed.append(tid) or True))
    cdp.close_new_seller_pages(
        port=1234, before_ids=set(before_ids), keep_min_tabs=1,
        before_urls=set(before_urls), owned_ids=set(owned_ids))
    return closed


# ---------------------------------------------------------------- ① 该关的
def test_online_shipping_page_opened_by_us_is_closed(monkeypatch):
    pages = [_page("keep", "https://%s/mmsos/orders.html" % SELLER),
             _page("mine", "https://%s/mmsos/orders.html" % SELLER),
             _page("batch", "https://%s/mmsos/online-shipping.html?parent_order_sn_list=x" % SELLER)]
    # batch 是站点从我们那页弹出来的
    got = _run(monkeypatch, pages, owned_ids=["mine"],
               openers={"batch": "mine", "keep": "", "mine": ""})
    assert "batch" in got, "批量在线下单页是跑出来的过程页，必须关"
    assert "keep" not in got, "运行前就在的页不许碰"


def test_order_detail_opened_by_us_is_closed(monkeypatch):
    pages = [_page("orders", "https://%s/mmsos/orders.html" % SELLER),
             _page("detail", "https://%s/mmsos/order-detail.html?id=1" % SELLER)]
    got = _run(monkeypatch, pages, owned_ids=["orders"],
               openers={"detail": "orders"})
    assert "detail" in got


def test_flux_analysis_is_closed_when_we_opened_it(monkeypatch):
    pages = [_page("orders", "https://%s/mmsos/orders.html" % SELLER),
             _page("flux", "https://%s/main/flux-analysis" % SELLER)]
    got = _run(monkeypatch, pages, owned_ids=["orders"], openers={"flux": "orders"})
    assert "flux" in got, "商品流量是抓数据的工作面，跑完该关"


def test_cross_process_leftover_is_closed(monkeypatch):
    """发货链每步一个子进程：上一步开的页，这一步要能凭登记表收掉。"""
    pages = [_page("orders", "https://%s/mmsos/orders.html" % SELLER),
             _page("batch", "https://%s/mmsos/online-shipping.html?big_array_key=k" % SELLER)]
    got = _run(monkeypatch, pages, owned_ids=[], reg=[{"pid": 999, "tid": "orders"}],
               openers={"batch": "orders"})
    assert "batch" in got, "跨进程的过程页也必须能被收掉"


# ---------------------------------------------------------------- ② 不许误伤
def test_orders_page_is_never_closed(monkeypatch):
    """2026-10-27 事故：关掉订单页 ⇒ 窗口退回登录页。"""
    pages = [_page("mine", "https://%s/mmsos/orders.html?fulfillmentMode=0" % SELLER)]
    got = _run(monkeypatch, pages, owned_ids=["mine"])
    assert got == [], "订单页是任务目标页，即使是我们开的也不能关"


def test_user_work_surface_opened_during_run_is_kept(monkeypatch):
    """用户运行期间打开**工作面**（新建/编辑商品）必须保住。

    这是 2026-10-28 的真实事故：收尾清理把用户正在编辑的
    `goods/edit?productId=…` 当临时页关掉了。
    过程页（批量下单/详情）与工作面的区别就在 `KEEP_PATH_HINTS`。
    """
    pages = [_page("orders", "https://%s/mmsos/orders.html" % SELLER),
             _page("edit", "https://agentseller.temu.com/goods/edit?productId=123"),
             _page("list", "https://agentseller.temu.com/goods/list")]
    got = _run(monkeypatch, pages, owned_ids=["orders"])
    assert "edit" not in got, "用户正在编辑的商品页绝不能关"
    assert "list" not in got, "商品列表是用户工作面，不能关"


def test_transient_page_without_ownership_is_still_closed(monkeypatch):
    """★ 2026-11-12 口径变化：过程页**不再**强求归属证据。

    真机上 `openerId` 恒为 None、登记表跑完即清空 ⇒ 旧判据永远拿不到证据
    ⇒ 清理器一个页都没关过（用户截图里那堆标签页就是这么来的）。
    现在的判据是「本次新出现 + URL 属于已知过程页类型」，代价是用户恰好在任务
    运行那几分钟里打开同类过程页会被一起收掉 —— 可再点一次，接受。
    """
    pages = [_page("orders", "https://%s/mmsos/orders.html" % SELLER),
             _page("batch", "https://%s/mmsos/online-shipping.html" % SELLER)]
    got = _run(monkeypatch, pages, owned_ids=["orders"],
               openers={})                       # 真机就是这样：没有任何 opener
    assert "batch" in got, "过程页即使证明不了归属，也该在收尾时关掉"


def test_page_already_open_before_run_is_kept(monkeypatch):
    pages = [_page("orders", "https://%s/mmsos/orders.html" % SELLER),
             _page("old", "https://%s/mmsos/online-shipping.html" % SELLER)]
    got = _run(monkeypatch, pages, before_ids={"old"}, owned_ids=["orders"],
               openers={"old": "orders"})
    assert "old" not in got, "运行前就开着的页一律不碰"


def test_never_leaves_zero_real_tabs(monkeypatch):
    """关页保底：窗口里至少留 1 个真实页，否则整个店铺窗口会消失。"""
    pages = [_page("batch", "https://%s/mmsos/online-shipping.html" % SELLER)]
    got = _run(monkeypatch, pages, owned_ids=["batch"])
    assert got == [], "只剩一页时不许关"


# ---------------------------------------------------------------- ③ 路径表
def test_transient_hints_do_not_include_orders_page():
    assert "/mmsos/orders.html" not in cdp.CLOSE_IF_OWNED_HINTS, (
        "orders.html 进过程页表会重演 2026-10-27 事故")
    for h in ("/mmsos/online-shipping", "/mmsos/order-detail", "/main/flux-analysis"):
        assert h in cdp.CLOSE_IF_OWNED_HINTS


# ---------------------------------------------------------------- ④ 单批过滤页
def _filtered_orders():
    return ("https://%s/mmsos/orders.html?fulfillmentMode=0"
            "&parentOrderSnList=%%5B%%22PO-211-03230254049910939%%22%%5D" % SELLER)


def test_filtered_orders_page_is_transient():
    assert cdp._is_filtered_orders_page(_filtered_orders()) is True
    assert cdp._is_filtered_orders_page(
        "https://%s/mmsos/orders.html?fulfillmentMode=0" % SELLER) is False, (
        "干净订单列表页不是遗留物，必须留")


def test_filtered_orders_page_opened_by_us_is_closed(monkeypatch):
    pages = [_page("orders", "https://%s/mmsos/orders.html" % SELLER),
             _page("batch", _filtered_orders())]
    got = _run(monkeypatch, pages, owned_ids=["orders"],
               openers={"batch": "orders"})
    assert "batch" in got, "按单号过滤的单批页是买面单遗留，跑完该关"


# ---------------------------------------------------------------- ⑤ 一次性清扫
def test_sweep_closes_only_transient_pages(monkeypatch):
    """清扫（不看 baseline）只该碰纯过程页，绝不碰订单列表/工作面。

    2026-11-12 实测踩到：第一版清扫把两店的「订单列表」默认页也关了
    —— 只因它当时带着 `parentOrderSnList` 过滤参数。
    """
    closed = []
    pages = [
        _page("orders", "https://%s/mmsos/orders.html" % SELLER),
        _page("filtered", _filtered_orders()),
        _page("goods", "https://agentseller.temu.com/goods/list"),
        _page("flux", "https://%s/main/flux-analysis" % SELLER),
        _page("shipping", "https://%s/mmsos/online-shipping.html" % SELLER),
    ]
    monkeypatch.setattr(cdp, "list_pages", lambda port: list(pages))
    monkeypatch.setattr(cdp, "close_target",
                        lambda port, tid: (closed.append(tid) or True))
    got = [tid for tid, _u in cdp.sweep_transient_pages(port=1234)]
    assert set(got) == {"flux", "shipping"}, "清扫范围跑偏了：%s" % got
    assert "orders" not in got and "filtered" not in got, "订单列表不许被清扫"
    assert "goods" not in got


def test_sweep_keeps_at_least_one_tab(monkeypatch):
    closed = []
    pages = [_page("only", "https://%s/main/flux-analysis" % SELLER)]
    monkeypatch.setattr(cdp, "list_pages", lambda port: list(pages))
    monkeypatch.setattr(cdp, "close_target",
                        lambda port, tid: (closed.append(tid) or True))
    assert cdp.sweep_transient_pages(port=1234) == [], "只剩一页时不许关"
