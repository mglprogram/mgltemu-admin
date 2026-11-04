# -*- coding: utf-8 -*-
"""`flux_auth.domain_for_url()` —— 授权探针必须对准**目标页所在的域**。

事故（2026-11-04 Today-Sales 两店全挂，rc=1）：
    [BHY] 登录态探针：ok https://agentseller-us.temu.com/mmsos/orders.html   ← 查的是美国站
    [auth] 临时页仍在认证页，重新导航（第 1/2/3 次）
    临时页打开了但一直没出数据行

真因：销量页 `fetch_focus_spus.SALES_URL = https://agentseller.temu.com/main/data-center/goods-data`
在**合作仓域 `cc`**，而 `ensure_logged_in()` 的探针**写死 `domain="us"`**。
temu-login-auth 的 D4 早就写明：这是**两套互相独立**的授权，查错域等于没查 ——
美国站说 ok，cc 域的临时页照样被弹回认证页。

跑：python -m pytest scripts/tests/test_flux_auth_domain.py -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)          # = scripts/
_REPO = os.path.dirname(_SCRIPTS)          # = 仓库根
for _p in (os.path.join(_SCRIPTS, "bel_goods"),
           os.path.join(_SCRIPTS, "power", "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import flux_auth  # noqa: E402

CC = "https://agentseller.temu.com/main/data-center/goods-data"
US = "https://agentseller-us.temu.com/mmsos/orders.html"


def test_cc_host_maps_to_cc_domain():
    """合作仓域（销量页所在）必须判 cc。"""
    assert flux_auth.domain_for_url(CC) == "cc"


def test_us_host_maps_to_us_domain():
    assert flux_auth.domain_for_url(US) == "us"


def test_subpaths_and_query_do_not_confuse_it():
    """带路径/query 也一样按 host 判。"""
    assert flux_auth.domain_for_url(
        "https://agentseller.temu.com/lgst/auth-warehouse/abnormal-order?a=1") == "cc"
    assert flux_auth.domain_for_url(
        "https://agentseller-us.temu.com/mmsos/export-order.html?x=2") == "us"


def test_unknown_or_empty_falls_back_to_us():
    """不认识的一律 us（历史行为），不要猜成 cc。"""
    assert flux_auth.domain_for_url("") == "us"
    assert flux_auth.domain_for_url("https://example.com/x") == "us"
    assert flux_auth.domain_for_url(None) == "us"


def test_cc_path_with_auth_word_is_still_cc():
    """`/lgst/auth-warehouse/` 含 auth，但它是**业务页**，不能因此换域。"""
    assert flux_auth.domain_for_url(
        "https://agentseller.temu.com/lgst/auth-warehouse/abnormal-order") == "cc"


def test_ensure_logged_in_accepts_target_url():
    """`ensure_logged_in` 必须收 `target_url` 并据此选域（否则又写死 us）。"""
    import inspect
    sig = inspect.signature(flux_auth.ensure_logged_in)
    assert "target_url" in sig.parameters, "没有 target_url 就只能写死域名"
    src = inspect.getsource(flux_auth.ensure_logged_in)
    assert "domain_for_url(target_url)" in src, "必须由 target_url 推导 domain"
    assert 'domain="us"' not in src, "不许再写死美国站"


def test_fetch_focus_spus_passes_its_own_sales_url():
    """销量抓取必须把自己的 SALES_URL 传下去（不传就退回 us ⇒ 又是今天这个 bug）。"""
    src = open(os.path.join(_REPO, "scripts", "bel_goods", "fetch_focus_spus.py"),
               encoding="utf-8").read()
    assert "target_url=SALES_URL" in src, \
        "fetch_focus_spus 调 ensure_logged_in 必须带 target_url=SALES_URL"
