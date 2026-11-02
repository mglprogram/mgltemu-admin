# -*- coding: utf-8 -*-
"""test_auth_domain.py — 授权**分域**判据 + 不得读页对象 `.url` 拖死流程。

来历（2026-11-01 → 11-02 排障）：
    昨天（11-01）多个定时任务因"登录态/授权页"失败，日志里有一对**自相矛盾**的话：

        [授权] 现有登录入口结果：[OK] OK: 已登录，无需授权 (rc=0)
        [授权] 第 1/4 轮：状态=authentication url=https://agentseller.temu.com/auth/authentication

    两个真根因（都已实测确认）：

    ① **假"已登录"**：`run_auth()` 的判据写死美国站 `agentseller-us.temu.com`。
       合作仓域 `agentseller.temu.com`（异常出库单）是**另一套授权**。窗口里只要有
       美国站订单页，`run_temu_auth.py` 就回"已登录" —— 调用方信了这句，
       合作仓那层根本没修，它自己 4 轮点「商家中心」全点在失效的认证页上 → rc=42。

    ② **单页 `.url` 读 60 秒**：`run_auth` 用 `pp.url` 逐个读 Playwright 页对象判"已登录"。
       实测某个渲染进程无响应的页会让这次读取**卡满 60 秒**（CDP 等不到回包），
       再叠加一次重试 = 120 秒；兜底超时是 300 秒，于是 `auth_guard` 每次都报
       「⚠ 超时（300s）」，业务等不到授权结果就自己失败。
       ⇒ 判断"是否已登录"改用**原始 CDP `/json/list`**（纯 HTTP，0.1s）。

运行: python -m pytest scripts/power/tests/test_auth_domain.py -q
"""
import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_REPO = os.path.dirname(os.path.dirname(_POWER))
for _p in (os.path.join(_POWER, "lib"), _POWER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import auth_temu_driver as atd  # noqa: E402

US_ORDERS = "https://agentseller-us.temu.com/mmsos/orders.html"
US_EXPORT = "https://agentseller-us.temu.com/mmsos/export-order.html"
US_MAIN = "https://agentseller-us.temu.com/main/flux-analysis"
US_AUTH = ("https://agentseller-us.temu.com/auth/authentication"
           "?redirectUrl=https%3A%2F%2Fagentseller-us.temu.com%2Fmmsos%2Forders.html")
CC_GOODS = "https://agentseller.temu.com/main/data-center/goods-data"
CC_TARGET = "https://agentseller.temu.com/lgst/auth-warehouse/abnormal-order"
CC_AUTH = ("https://agentseller.temu.com/auth/authentication"
           "?redirectUrl=https%3A%2F%2Fagentseller.temu.com%2Flgst%2Fauth-warehouse%2Fabnormal-order")


# ---------------------------------------------------------------- ① 分域判据
def test_us_pages_only_count_for_us_domain():
    for u in (US_ORDERS, US_EXPORT, US_MAIN):
        assert atd._url_is_seller_page(u, domain="us") is True, u
        assert atd._url_is_seller_page(u, domain="cc") is False, \
            "%s 是美国站页，不能算作合作仓已登录" % u


def test_cc_pages_only_count_for_cc_domain():
    """合作仓的**业务目标页在 `/lgst/` 下**（不在 /mmsos/ 也不在 /main/）。

    2026-11-02 实测：沿用美国站前缀时，`/lgst/auth-warehouse/abnormal-order`
    永远判"未登录" ⇒ 异常单任务即使授权成功也报「授权未完成」。
    """
    for u in (CC_GOODS, CC_TARGET):
        assert atd._url_is_seller_page(u, domain="cc") is True, u
        assert atd._url_is_seller_page(u, domain="us") is False, \
            "%s 是合作仓页，不能算作美国站已登录" % u


def test_cc_target_path_containing_auth_word_is_not_a_login_page():
    """`/lgst/auth-warehouse/...` 里含 "auth"，但它是**业务页**不是认证页。

    黑名单必须按路径段判（`/auth/` 这一整段），不能子串匹配。
    """
    assert atd._url_is_seller_page(CC_TARGET, domain="cc") is True, \
        "auth-warehouse 是业务路径，被误判成认证页会导致永远判未登录"


def test_auth_pages_never_count_as_logged_in():
    """认证页 URL 的 query 里带 redirectUrl=.../mmsos/orders.html —— 必须判未登录。"""
    for u, dom in ((US_AUTH, "us"), (CC_AUTH, "cc")):
        assert atd._url_is_seller_page(u, domain=dom) is False, \
            "%s 是认证页，绝不能判成已登录（TLA-011 陷阱）" % u[:60]


def test_unknown_domain_is_false_not_guess():
    """未知域名一律 False —— 宁可多重授权一次，也不要把"别的域已登录"当成目标域。"""
    assert atd._url_is_seller_page(US_ORDERS, domain="nope") is False


def test_default_domain_is_us():
    """不传 domain 时行为必须与历史一致（美国站），避免悄悄改掉所有老调用方。"""
    assert atd._url_is_seller_page(US_ORDERS) is True
    assert atd._url_is_seller_page(CC_TARGET) is False


# ---------------------------------------------------------------- ② 调用方必须声明域
def test_abnormal_driver_asks_for_cc_domain():
    """合作仓异常单调用授权入口时，必须显式 --domain cc（否则拿回假的"已登录"）。"""
    src = open(os.path.join(_POWER, "lib", "temu_abnormal_driver.py"), encoding="utf-8").read()
    body = src.split("def _call_existing_auth", 1)[1].split("def _clean_tabs", 1)[0]
    assert '"--domain", "cc"' in body, "合作仓授权必须声明 --domain cc"


def test_auth_entry_exposes_domain_flag():
    src = open(os.path.join(_POWER, "run_temu_auth.py"), encoding="utf-8").read()
    assert '"--domain"' in src and 'choices=["us", "cc"]' in src
    assert "domain=args.domain" in src, "入口必须把 domain 传下去"


# ---------------------------------------------------------------- ③ 不许读页对象 .url
def test_login_check_uses_raw_cdp_not_page_url():
    """判"已登录"必须走 /json/list（纯 HTTP），不许遍历 Playwright 页对象读 .url。

    实测：某个渲染进程无响应的页会让 `pp.url` 卡 60 秒 ⇒ 300s 兜底超时。
    """
    src = open(os.path.join(_POWER, "lib", "auth_temu_driver.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "run_auth")
    body = ast.get_source_segment(src, fn) or ""
    head = body.split("# 引导导航", 1)[0]
    assert "_entered_seller_page_via_cdp" in head, "前置判登录要走原始 CDP"
    # 只看**代码行**（去掉注释），避免把说明文字里的 `.url` 当违规
    code_lines = [ln.split("#", 1)[0] for ln in head.splitlines()]
    assert not any("pp.url" in ln for ln in code_lines), \
        "前置判登录不许读 Playwright 页对象的 .url（会卡 60s）"


def test_wait_seller_page_uses_raw_cdp():
    """`_wait_seller_page` 也必须走 /json/list。"""
    src = open(os.path.join(_POWER, "lib", "auth_temu_driver.py"), encoding="utf-8").read()
    body = src.split("def _wait_seller_page", 1)[1].split("def _pages_via_cdp", 1)[0]
    assert "_pages_via_cdp" in body, "等落地页要用原始 CDP 列页"


# ---------------------------------------------------------------- ④ 点完必须切到授权框
def test_abnormal_switches_to_auth_frame_after_region_click():
    """点「商家中心」后平台**新开** seller-login tab 而当前页 URL 不变 —— 必须扫全窗口切过去。

    旧实现 `page = self._page_live()` 只认本域页，看不到授权框 ⇒ 每轮都从 authentication 重来，
    昨天 4 轮 CLICKED 却零推进。
    """
    src = open(os.path.join(_POWER, "lib", "temu_abnormal_driver.py"), encoding="utf-8").read()
    assert "def _switch_to_auth_frame" in src
    seg = src.split('if kind == "authentication":', 1)[1].split('if kind == "seller-login":', 1)[0]
    assert "_switch_to_auth_frame" in seg, "点完商家中心必须切到授权框页"
