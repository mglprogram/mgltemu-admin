# -*- coding: utf-8 -*-
"""test_auth_stale_shadow.py — 「窗口里挂着订单页 ≠ 已登录」的回归测试。

来历（2026-11-02 实测，BHY/BEL 连续两天整批白跑）
--------------------------------------------------
日志里两句话自相矛盾：

    [授权兜底] ✅ 通过（rc=0）                    ← 看窗口里的旧页 URL 判的"已登录"
    [FAIL] action=pending 失败：订单列表页未就绪：页面停在授权页

根因：判据是「窗口里**存在**一个 `/mmsos/` 页 ⇒ 已登录」。那页是上一次留下的
**残影**（URL 还停在 orders.html），登录态早失效；服务器只在**下一次请求**时
才把你打回认证页。同一分钟实测：

    BEL  port=50070  新开页 → /mmsos/orders.html              （真有效）
    BHY  port=57964  新开页 → /auth/authentication?redirect…   （窗口里却挂着 orders.html）

修法：`probe_login_state()` —— 真开一页请求一次，看它落到哪。

⚠ 还有一个差点又写错的细节：新页的跳转是**先好后坏**
（约 2s 显示 orders.html 的壳，约 4s 才被服务端 replace 成 auth）。
第一版「见到订单页就返回 ok」因此在 BHY 上判出假阳性（实测 2s=orders、4s=auth）。
⇒ 规则：**观察窗内出现过登录页就一律 LOST**；只有观察满 settle_s 且始终是商家中心页才 OK。

运行: python -m pytest scripts/power/tests/test_auth_stale_shadow.py -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
for _p in (os.path.join(_POWER, "lib"), _POWER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import auth_temu_driver as atd  # noqa: E402

US_ORDERS = "https://agentseller-us.temu.com/mmsos/orders.html"
US_AUTH = ("https://agentseller-us.temu.com/auth/authentication"
           "?redirectUrl=https%3A%2F%2Fagentseller-us.temu.com%2Fmmsos%2Forders.html")
SELLER_LOGIN = "https://seller.kuajingmaihuo.com/settle/seller-login"


# ---------------------------------------------------------------- URL 判定
def test_login_url_detection_covers_both_families():
    """认证页与授权框页都算"未登录"，别只认其中一种。"""
    assert atd._is_login_url(US_AUTH) is True
    assert atd._is_login_url(SELLER_LOGIN) is True
    assert atd._is_login_url(US_ORDERS) is False


def test_login_url_detection_is_segment_based_not_substring():
    """合作仓业务页 `/lgst/auth-warehouse/...` 含 "auth" 但不是登录页。

    子串判会把合作仓所有业务页判成"未登录" ⇒ 永远重授权（2026-11-02 踩过）。
    """
    assert atd._is_login_url(
        "https://agentseller.temu.com/lgst/auth-warehouse/abnormal-order") is False


# ---------------------------------------------------------------- 探针主逻辑
class _FakeCdp:
    """把 create_target / list_pages / close_target 换成一个可控的"页面跳转剧本"。"""

    def __init__(self, urls, tid="TID-1"):
        self.urls = list(urls)          # 每次 list_pages 依次返回的 URL（最后一项保持）
        self.tid = tid
        self.i = 0
        self.closed = []

    def create_target(self, port, url="about:blank", background=True):
        return self.tid

    def list_pages(self, port):
        u = self.urls[min(self.i, len(self.urls) - 1)]
        self.i += 1
        return [] if u is None else [{"id": self.tid, "url": u, "type": "page"}]

    def close_target(self, port, tid):
        self.closed.append(tid)
        return True


def _patch(monkeypatch, fake, sleeps=None):
    monkeypatch.setattr(atd.cdp_transport, "create_target", fake.create_target)
    monkeypatch.setattr(atd, "_pages_via_cdp", lambda port: fake.list_pages(port))
    monkeypatch.setattr(atd.cdp_transport, "close_target", fake.close_target)
    rec = []
    monkeypatch.setattr(atd.time, "sleep", lambda s: rec.append(s))


def test_stale_window_page_alone_is_not_enough(monkeypatch):
    """核心回归：新页先给 orders.html 的壳、随后被 replace 成 auth ⇒ 必须判 LOST。

    第一版就是在这里判出了假阳性（`ok`），然后下游整批失败。
    """
    fake = _FakeCdp([US_ORDERS, US_ORDERS, US_AUTH, US_AUTH])
    _patch(monkeypatch, fake)
    state, url = atd.probe_login_state(57964, gap=0.01, timeout_s=1.0, settle_s=0.05)
    assert state == atd.LOGIN_LOST, "见到登录页就必须判失效（先好后坏）"
    assert "authentication" in url


def test_real_login_stays_on_seller_page_and_is_ok(monkeypatch):
    fake = _FakeCdp([US_ORDERS] * 6)
    _patch(monkeypatch, fake)
    state, url = atd.probe_login_state(50070, gap=0.01, timeout_s=1.0, settle_s=0.0)
    assert state == atd.LOGIN_OK
    assert url == US_ORDERS


def test_probe_unknown_never_blocks(monkeypatch):
    """建页失败 ⇒ UNKNOWN（调用方按"已登录"保守处理，不乱触发授权）。"""
    def boom(*a, **kw):
        raise RuntimeError("CDP 挂了")

    monkeypatch.setattr(atd.cdp_transport, "create_target", boom)
    state, url = atd.probe_login_state(1)
    assert state == atd.LOGIN_UNKNOWN and url == ""


def test_probe_closes_its_own_tab_when_login_is_valid(monkeypatch):
    """探针页用完即关（用户口径：不要留孤儿标签页）。"""
    fake = _FakeCdp([US_ORDERS] * 4)
    _patch(monkeypatch, fake)
    atd.probe_login_state(50070, gap=0.01, timeout_s=1.0, settle_s=0.0)
    assert fake.closed == ["TID-1"]


def test_probe_keeps_the_login_tab_on_lost_for_auth_flow(monkeypatch):
    """判失效时那一页正好是认证页，留着给授权流程用（省一次导航）。"""
    fake = _FakeCdp([US_AUTH] * 3)
    _patch(monkeypatch, fake)
    atd.probe_login_state(57964, gap=0.01, timeout_s=1.0, keep_on_lost=True)
    assert fake.closed == []


# ---------------------------------------------------------------- 调用方契约
def test_run_auth_consults_the_probe_before_declaring_logged_in():
    """`run_auth` 判"已登录"之前必须经过探针，不能只看窗口里有没有那个页。"""
    src = open(os.path.join(_POWER, "lib", "auth_temu_driver.py"), encoding="utf-8").read()
    seg = src.split("def run_auth", 1)[1].split("for attempt in range", 1)[0]
    assert "probe_login_state(" in seg, "前置登录检查必须调用探针"
    assert "LOGIN_LOST" in seg, "探针判失效时必须继续走授权而不是返回已登录"


def test_buylabel_self_heals_once_on_auth_redirect():
    """被重定向到授权页时，订单页就绪检查必须**当场补一次授权**，而不是整批抛错。

    2026-11-02：BEL/BHY 每天都是「0 单 + rc=2」，修一次授权就能继续。
    自愈只能一次（`_auth_healed`），第二次仍不行要如实报错，不能无限重试。
    """
    src = open(os.path.join(_POWER, "lib", "temu_buylabel_driver.py"),
               encoding="utf-8").read()
    seg = src.split("def _orders_page_ready", 1)[1].split("def set_list_page_size", 1)[0]
    assert "call_auth_entry(" in seg, "订单页被重定向时必须调用唯一授权实现"
    assert "_auth_healed" in seg, "自愈必须只做一次（防无限重试）"
    assert "raise RuntimeError" in seg, "自愈失败后仍要如实抛错，不能静默吞掉"
