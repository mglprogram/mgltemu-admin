# -*- coding: utf-8 -*-
"""test_temu_sales_pages.py — TEMU 销量导出 driver 的**选页/URL 判据**纯单测（离线，不开浏览器）。

背景（2026-09-16 实测，三家全中）：授权跳转后窗口里会留下一个**空白、0 个 input** 的
`seller.kuajingmaihuo.com/settle/seller-login?redirectUrl=...%2Fmmsos%2Fexport-order.html` 死 tab，
且它排在 tab 列表最前。旧实现两处踩坑：
  1) `_connect()` 取「第一个含 temu.com/kuajingmaihuo.com 的页」⇒ 取到该死 tab；
  2) 导出前的两道判断用整串包含 —— 该死 tab 的 URL **同时**含 `export-order.html`（在 redirectUrl query 里）
     与 `seller-login`：第一道（判"是否已在导出页"）被 query 里那串骗过 ⇒ **不导航**；
     第二道（判"是否停在登录页"）命中 ⇒ 直接误报「停在登录/认证页，需先授权」。
修法：① 选页按优先级（导出页 > 订单页 > 其它 /mmsos > 卖家中心其它页 > 其它 temu 页 > 认证页兜底）；
      ② 两道判断改 host+path（`_url_is_export_page` / `_url_is_auth_page`）。

本文件守的就是这两条，防止回退。

运行: python -m pytest scripts/power/tests/test_temu_sales_pages.py -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "lib"))

import temu_sales_driver as d  # noqa: E402

# 真实死 tab 形态（2026-09-16 现场 /json/list 抓到的 URL）
DEAD_TAB = ("https://seller.kuajingmaihuo.com/settle/seller-login"
            "?redirectUrl=https%3A%2F%2Fagentseller-us.temu.com%2Fmmsos%2Fexport-order.html"
            "&region=3&sessionType=0")
EXPORT_PAGE = "https://agentseller-us.temu.com/mmsos/export-order.html"
ORDERS_PAGE = "https://agentseller-us.temu.com/mmsos/orders.html"
HOME_PAGE = "https://agentseller-us.temu.com/"
AUTH_PAGE = ("https://agentseller-us.temu.com/auth/authentication"
             "?redirectUrl=https%3A%2F%2Fagentseller-us.temu.com%2Fmmsos%2Fexport-order.html")
EXT_PAGE = "chrome-extension://dkmfchocknfallppgbpegkppapkbjhml/offscreen.html"


class _Page:
    def __init__(self, url):
        self.url = url


class _DeadPage:
    """取 url 就抛（模拟已关闭的 target）——选页不得被它带崩。"""

    @property
    def url(self):
        raise RuntimeError("Target closed")


class _Ctx:
    def __init__(self, pages):
        self.pages = pages


class _Browser:
    def __init__(self, pages):
        self.contexts = [_Ctx(pages)]


def test_dead_tab_url_is_not_export_page():
    """死 tab 的 URL 里含（query 编码后的）export-order.html —— 但 host+path 判必须为 False。"""
    assert "export-order.html" in DEAD_TAB          # 旧写法的陷阱本体
    assert d._url_is_export_page(DEAD_TAB) is False
    assert d._url_is_auth_page(DEAD_TAB) is True


def test_url_judges_host_and_path():
    assert d._url_is_export_page(EXPORT_PAGE) is True
    assert d._url_is_export_page(EXPORT_PAGE + "?queryType=1") is True
    assert d._url_is_export_page(ORDERS_PAGE) is False
    assert d._url_is_export_page(HOME_PAGE) is False
    assert d._url_is_export_page("") is False
    # 别的站点同名路径不算
    assert d._url_is_export_page("https://evil.example.com/mmsos/export-order.html") is False
    # 认证页（path 是 /auth/authentication，redirectUrl 在 query 里）
    assert d._url_is_auth_page(AUTH_PAGE) is True
    assert d._url_is_auth_page(HOME_PAGE) is False
    assert d._url_is_auth_page(EXPORT_PAGE) is False


def test_page_rank_priority():
    assert d._page_rank(EXPORT_PAGE) < d._page_rank(ORDERS_PAGE) < d._page_rank(HOME_PAGE) < d._page_rank(DEAD_TAB)
    assert d._page_rank(DEAD_TAB) == d._page_rank(AUTH_PAGE)


def test_pick_page_prefers_export_page_over_dead_tab():
    """核心回归：死 tab 排在最前时，仍必须选到导出页（旧实现会选死 tab → 误报需授权）。"""
    b = _Browser([_Page(DEAD_TAB), _Page(EXPORT_PAGE), _Page(EXT_PAGE)])
    assert d._pick_page(b).url == EXPORT_PAGE


def test_pick_page_prefers_orders_or_home_over_auth():
    assert d._pick_page(_Browser([_Page(DEAD_TAB), _Page(ORDERS_PAGE)])).url == ORDERS_PAGE
    assert d._pick_page(_Browser([_Page(DEAD_TAB), _Page(HOME_PAGE)])).url == HOME_PAGE


def test_pick_page_falls_back_to_auth_page_when_only_auth():
    """窗口里确实只剩认证页 → 仍要选中它，让"需先授权"如实报出来（不能把真需授权吞掉）。"""
    assert d._pick_page(_Browser([_Page(DEAD_TAB)])).url == DEAD_TAB
    assert d._pick_page(_Browser([_Page(AUTH_PAGE)])).url == AUTH_PAGE


def test_pick_page_skips_broken_page():
    """页对象已关闭（取 url 抛错）不得带崩选页。"""
    assert d._pick_page(_Browser([_DeadPage(), _Page(EXPORT_PAGE)])).url == EXPORT_PAGE


def test_pick_page_none_when_no_temu_page():
    assert d._pick_page(_Browser([_Page(EXT_PAGE), _Page("about:blank")])) is None
