# -*- coding: utf-8 -*-
"""test_temu_auth.py — TEMU 登录/授权 driver 的**纯判据**单测（离线，不开浏览器）。

背景（2026-09-15 实测，TLA-017）：`run_temu_auth.py --store BHY` 授权其实**成功**，
但点「授权登录」后页面跳转关闭 → Playwright 抛 `Target page, context or browser has been closed`
（TLA-010 说是预期）→ 旧实现让它冒到最外层，报 `ERR: ...` + exit 42，把人/自动化误导成失败。
修法＝跳转期异常后按 **D4** 用原始 CDP `/json/list` 复核落地页。

背景（2026-09-16 实测，D4 放宽）：三家店的授权都是从 `.../mmsos/export-order.html` 的 redirectUrl
进来的，**落地页就是 `export-order.html`** 而不是 `orders.html`；旧 D4 只认 `orders.html` ⇒
授权成功却被判「未进入订单页」→ FAIL + exit 42（三家全中）。故 D4 放宽为
「host = agentseller-us.temu.com 且 path 在 /mmsos/ 下且不是 auth/login 页」。

本文件守的两件事：
  1) D4 判据**必须 host + path**，禁止整串 `in`（认证页 query 里带 `redirectUrl=...%2Fmmsos%2Forders.html`
     → 用整串包含会误报成功，TLA-011）；
  2) 落地页 `export-order.html` 与 `orders.html` **同等算成功**（放宽后不得回退）。

运行: python -m pytest scripts/power/tests/test_temu_auth.py -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "lib"))

import auth_temu_driver as a  # noqa: E402


def test_d4_accepts_orders_page():
    """D4：/mmsos/orders(.html) 算进入商家中心可用页（query 参数不影响）。"""
    assert a._url_is_seller_page("https://agentseller-us.temu.com/mmsos/orders.html") is True
    assert a._url_is_seller_page(
        "https://agentseller-us.temu.com/mmsos/orders.html?fulfillmentMode=0&queryType=4") is True
    assert a._url_is_seller_page("https://agentseller-us.temu.com/mmsos/orders") is True


def test_d4_accepts_export_order_page():
    """D4 放宽（2026-09-16）：授权从 export-order.html 的 redirectUrl 进来 → 落地页是导出页，必须算成功。"""
    assert a._url_is_seller_page("https://agentseller-us.temu.com/mmsos/export-order.html") is True
    assert a._url_is_seller_page(
        "https://agentseller-us.temu.com/mmsos/export-order.html?queryType=1") is True


def test_d4_rejects_auth_redirect_trap():
    """TLA-011：认证页 redirectUrl 的 query 里带 orders/export-order —— 整串包含会误报「已进页」。"""
    bad = ("https://agentseller-us.temu.com/auth/authentication"
           "?redirectUrl=https%3A%2F%2Fagentseller-us.temu.com%2Fmmsos%2Forders.html")
    assert a._url_is_seller_page(bad) is False
    bad2 = ("https://seller.kuajingmaihuo.com/settle/seller-login"
            "?redirectUrl=https%3A%2F%2Fagentseller-us.temu.com%2Fmmsos%2Fexport-order.html")
    assert a._url_is_seller_page(bad2) is False
    assert a._url_is_seller_page("https://seller.kuajingmaihuo.com/login?redirectUrl=%2Fmmsos%2Forders.html") is False
    assert a._url_is_seller_page("https://seller.kuajingmaihuo.com/settle/seller-login?redirectUrl=x") is False
    # 卖家中心首页（店铺视图不对时导航被弹回的地方）不是可用页
    assert a._url_is_seller_page("https://agentseller-us.temu.com/") is False
    assert a._url_is_seller_page("") is False
    # 别的站点的同名路径不算（host 必须对）
    assert a._url_is_seller_page("https://evil.example.com/mmsos/orders.html") is False
