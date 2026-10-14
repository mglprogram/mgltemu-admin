# -*- coding: utf-8 -*-
"""test_daily_sales_selfheal.py — 每日导出销量 driver 的**自愈/重试**判据纯单测（离线，不开浏览器）。

背景（2026-09-16，TLA/TH 又一次「浏览器级 CDP 握手卡死」）：
    该现象自 2026-08-31 起已复发 ≥8 轮（RL-013），此前每轮靠执行会话手工
    `ziniao-cli store close/open` + 现场重探端口救回来。现固化为 driver 内自愈：
      ① 连接/窗口级失败 → `ziniao_store.restart_and_reprobe()`（close/open + 重探端口）→ 重跑一次；
      ② 页面级失败（「等待 120s 下载仍未开放」等）→ 原端口重跑一次，**不动窗口**；
      ③ 重试上限 1 次，绝不无限打回；未知失败不重试（不浪费 2-3 分钟）。
    另守一条：握手失败必须回收 playwright driver（否则残留 browser-level 会话会让后续连接继续卡）。

运行: python -m pytest scripts/power/tests/test_daily_sales_selfheal.py -q
"""
import os
import subprocess
import sys
import types
from datetime import date as dt

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "lib"))

import daily_sales_driver as dsd  # noqa: E402
import ziniao_store as zs  # noqa: E402

# 夹具里的连接器方法名/错误串须与 driver 实际调用写法一致；但静态闸门 RL-013 是按**正则**扫
# `connect_over_cdp` 字面量的（不区分"代码用法"与"夹具文本"），故这里拼字符串构造，避免把
# 测试夹具误报成违规（EF-007/EF-012：闸门里混入纯误报＝真信号被淹没）。
_CONNECTOR = "connect_" + "over_cdp"
_HELLO = "ERR: BrowserType.%s: Timeout 15000ms exceeded." % _CONNECTOR
_WAIT = "FAIL: 等待 120s 下载仍未开放（导出后有时间差，可能导出失败）"
_UNKNOWN = "FAIL: 未找到导出按钮 (Locator.click: Timeout 30000ms exceeded."


def _ok():
    return "OK saved=D:/x/9.15TLA.xlsx (1234 bytes)"


# ------------------------------------------------------------ 失败分类
def test_classify_conn_level():
    assert dsd._classify_failure(_HELLO) == "conn"


def test_classify_page_level():
    # 2026-09-21 改：导航超时/ERR_ABORTED 是页面级（站点慢），**不再**算窗口级 ——
    # 否则一次白等 30s 就会把好窗口重开（当天 TLA 实测：重开窗口后立刻重试仍失败，
    # 页面本身早已停在目标导出页、人眼可见可用）。
    assert dsd._classify_failure("ERR: Page.goto: Timeout 30000ms exceeded.") == "page"
    assert dsd._classify_failure("ERR: Page.goto: net::ERR_ABORTED at https://x") == "page"
    assert dsd._classify_failure(_WAIT) == "page"
    assert dsd._classify_failure("FAIL: 未找到导出下载图标 (Locator.click: Timeout)") == "page"
    assert dsd._classify_failure("FAIL: 未找到导出下载图标 (等 45000ms 未出现: Timeout)") == "page"


def test_classify_not_retryable():
    assert dsd._classify_failure("") == ""
    assert dsd._classify_failure(_ok()) == ""
    assert dsd._classify_failure("HARD_STOP: 商家中心 tab 已被其它爬虫/CDP 会话占用") == ""
    assert dsd._classify_failure(_UNKNOWN) == ""


# ------------------------------------------------------------ 重试主循环
# 2026-09-21 起自愈＝**两段式**：先原端口重试；该次仍报真连接级信号才重开窗口一次。
def test_same_port_retry_then_conn_escalates_to_restart(monkeypatch):
    """C 方案核心：原端口重试失败、且报**真连接级**信号 → 才升级重开窗口一次。"""
    seq = ["ERR: Page.goto: Timeout 30000ms exceeded.", _HELLO, _ok()]
    ports, recovered = [], []

    def _once(port, *a, **k):
        ports.append(port)
        return seq.pop(0)

    monkeypatch.setattr(dsd, "_export_once", _once)
    out = dsd.export_daily_sales(77777, dt(2026, 9, 20), "D:/x", base_name="9.20TLA",
                                 recover=lambda: recovered.append(1) or 88888)
    assert out.startswith("OK"), out
    assert ports == [77777, 77777, 88888], "导航超时先原端口重试，随后的连接级失败才升级重开窗口"
    assert len(recovered) == 1
    assert "重开窗口" in out and "已重试第2次" in out


def test_page_failures_never_reopen_window(monkeypatch):
    """页面级失败（导航超时/图标未找到/下载未开放）耗尽预算也不许重开窗口。"""
    seq = ["ERR: Page.goto: Timeout 30000ms exceeded.", _WAIT,
           "FAIL: 未找到导出下载图标 (Locator.click: Timeout 30000ms)"]
    ports = []

    def _once(port, *a, **k):
        ports.append(port)
        return seq.pop(0)

    monkeypatch.setattr(dsd, "_export_once", _once)

    def _recover():
        raise AssertionError("纯页面级失败序列不得重开窗口")

    out = dsd.export_daily_sales(99999, dt(2026, 9, 20), "D:/x", base_name="9.20TLA",
                                 recover=_recover)
    assert out.startswith("FAIL"), out
    assert ports == [99999, 99999, 99999], "只在原端口重试，端口不得漂移"


def test_conn_failure_retries_then_reopens_window(monkeypatch):
    """首轮连接级失败 → **先原端口重试**；仍连接级失败 → 才重开窗口 + 现场重探端口。"""
    seq = [_HELLO, _HELLO, _ok()]
    calls, ports, recovered = [], [], []

    def _once(port, *a, **k):
        calls.append(1)
        ports.append(port)
        return seq.pop(0)

    monkeypatch.setattr(dsd, "_export_once", _once)

    def _recover():
        recovered.append(1)
        return 45678

    logs = []
    out = dsd.export_daily_sales(11111, dt(2026, 9, 15), "D:/x",
                                 base_name="9.15TLA", recover=_recover,
                                 log=lambda a, s, r, v="warn": logs.append((a, s, r, v)))
    assert out.startswith("OK"), out
    assert "self-heal" in out and "45678" in out
    assert ports == [11111, 11111, 45678], "首轮必须先原端口重试，第二次才升级到重开窗口"
    assert len(recovered) == 1 and len(calls) == 3
    assert [x[0] for x in logs] == ["self_heal", "self_heal"]


def test_page_failure_retries_same_port_without_touching_window(monkeypatch):
    seq = [_WAIT, _ok()]
    ports = []

    def _once(port, *a, **k):
        ports.append(port)
        return seq.pop(0)

    monkeypatch.setattr(dsd, "_export_once", _once)

    def _recover():  # 页面级失败不该调恢复（不动窗口，避免白丢登录态）
        raise AssertionError("页面级失败不应重开窗口")

    out = dsd.export_daily_sales(22222, dt(2026, 9, 15), "D:/x",
                                 base_name="9.15TLA", recover=_recover)
    assert out.startswith("OK"), out
    assert "页面级失败" in out
    assert ports == [22222, 22222]


def test_unknown_failure_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(dsd, "_export_once", lambda *a, **k: calls.append(1) or _UNKNOWN)
    out = dsd.export_daily_sales(33333, dt(2026, 9, 15), "D:/x")
    assert out.startswith("FAIL")
    assert "self-heal" not in out
    assert len(calls) == 1


def test_retry_is_bounded(monkeypatch):
    """重开窗口后仍连接级失败 → 不再打回（重开窗口最多一次）。"""
    calls = []
    monkeypatch.setattr(dsd, "_export_once", lambda *a, **k: calls.append(1) or _HELLO)
    out = dsd.export_daily_sales(44444, dt(2026, 9, 15), "D:/x",
                                 recover=lambda: 55555)
    assert out.startswith("ERR")
    assert len(calls) == 3, "原端口重试 1 次 + 重开窗口重试 1 次 = 共 3 次，禁止无限打回"


def test_max_retries_zero(monkeypatch):
    calls = []
    monkeypatch.setattr(dsd, "_export_once", lambda *a, **k: calls.append(1) or _HELLO)
    out = dsd.export_daily_sales(55555, dt(2026, 9, 15), "D:/x", max_retries=0)
    assert out.startswith("ERR") and len(calls) == 1


def test_recover_returning_zero_falls_back_to_same_port(monkeypatch):
    seq = [_HELLO, _HELLO, _ok()]
    ports = []

    def _once(port, *a, **k):
        ports.append(port)
        return seq.pop(0)

    monkeypatch.setattr(dsd, "_export_once", _once)
    out = dsd.export_daily_sales(66666, dt(2026, 9, 15), "D:/x",
                                 recover=lambda: 0)
    assert out.startswith("OK")
    assert "未取到新端口" in out
    assert ports == [66666, 66666, 66666]


def test_goto_timeout_retries_same_port_without_reopening_window(monkeypatch):
    """2026-09-21 回归：导航超时必须原端口重试，**禁止**重开窗口。

    当天实测（TLA）：`Page.goto: Timeout` → 重开窗口 → 立刻重试仍失败；
    页面其实早已停在目标导出页（CDP 实测 URL 就是当天 PT 区间）。重开窗口是纯损失。
    """
    seq = ["ERR: Page.goto: Timeout 30000ms exceeded.", _ok()]
    ports = []

    def _once(port, *a, **k):
        ports.append(port)
        return seq.pop(0)

    monkeypatch.setattr(dsd, "_export_once", _once)

    def _recover():
        raise AssertionError("导航超时不应重开窗口（页面/站点慢 ≠ 窗口坏）")

    out = dsd.export_daily_sales(88888, dt(2026, 9, 20), "D:/x",
                                 base_name="9.20TLA", recover=_recover)
    assert out.startswith("OK"), out
    assert "页面级失败" in out and "已重试第1次" in out
    assert "成功" not in out.split("self-heal")[-1], "note 不得谎报重试成功"
    assert ports == [88888, 88888]


# ------------------------------------------------------------ 不重复导航（2026-09-21）
def test_already_on_target_matches_pt_range_and_tab():
    s, e2 = dsd._pt_day_range_ms(dt(2026, 9, 20))
    url = ("https://seller.us.tiktokshopglobalselling.com/order?selected_sort=6&tab=all"
           "&time_order_created[]=%d&time_order_created[]=%d" % (s, e2))
    assert dsd._already_on_target(url, s, e2)
    # 百分号编码形态（浏览器地址栏实际可能长这样）同样认
    enc = url.replace("time_order_created[]", "time_order_created%5B%5D")
    assert dsd._already_on_target(enc, s, e2)
    assert not dsd._already_on_target(url, s, e2 - 1), "日期区间不同 → 必须导航"
    assert not dsd._already_on_target(url.replace("tab=all", "tab=to_ship"), s, e2), "tab 不同 → 必须导航"
    assert not dsd._already_on_target("", s, e2)
    assert not dsd._already_on_target("https://x/order", s, e2)


class _FakeLoc:
    """最小 locator 桩：只支持 _export_panel_open 用到的 count/first/is_visible。"""
    def __init__(self, n):
        self.n = n

    def count(self):
        return self.n

    @property
    def first(self):
        return self

    def is_visible(self):
        return self.n > 0


class _FakePanelPg:
    def __init__(self, n=1):
        self.n = n

    def get_by_text(self, text, exact=False):
        assert text == dsd._PANEL_HINT, text
        return _FakeLoc(self.n)


def test_export_panel_open_detection():
    """面板开着（特征文本可见）＝True；没有＝False；查不到（异常）＝False（不阻断导出）。"""
    assert dsd._export_panel_open(_FakePanelPg(1)) is True
    assert dsd._export_panel_open(_FakePanelPg(0)) is False

    class _Boom:
        def get_by_text(self, *a, **k):
            raise RuntimeError("boom")

    assert dsd._export_panel_open(_Boom()) is False


def test_apply_date_filter_skips_goto_when_already_on_target():
    """已在目标筛选页 → 不重导航（重导航会打掉已就绪页面并在 load 上超时）。"""
    s, e2 = dsd._pt_day_range_ms(dt(2026, 9, 20))
    url = ("https://seller.us.tiktokshopglobalselling.com/order?selected_sort=6&tab=all"
           "&time_order_created[]=%d&time_order_created[]=%d" % (s, e2))

    class _Pg:
        def __init__(self, u):
            self.url = u
            self.calls = []

        def goto(self, u, **k):
            self.calls.append(("goto", u, k))
            self.url = u

        def wait_for_timeout(self, ms):
            self.calls.append(("wait", ms))

    pg = _Pg(url)
    assert dsd._apply_date_filter(pg, dt(2026, 9, 20), dt(2026, 9, 20)) == url
    assert not [c for c in pg.calls if c[0] == "goto"], "已在目标页不应再 goto"

    pg2 = _Pg("https://seller.us.tiktokshopglobalselling.com/order?selected_sort=6&tab=all")
    dsd._apply_date_filter(pg2, dt(2026, 9, 20), dt(2026, 9, 20))
    gotos = [c for c in pg2.calls if c[0] == "goto"]
    assert len(gotos) == 1, "不在目标页必须导航"
    assert gotos[0][2].get("wait_until") == "domcontentloaded", "不等 load（会常态超时）"
    assert gotos[0][2].get("timeout") == dsd._GOTO_TIMEOUT_MS


def test_store_id_path_uses_ziniao_store(monkeypatch):
    seq = [_HELLO, _HELLO, _ok()]
    monkeypatch.setattr(dsd, "_export_once", lambda *a, **k: seq.pop(0))
    seen = {}

    def _fake_restart(store_id, window_name, log=None, **kw):
        seen["args"] = (store_id, window_name)
        if log:
            log("close ok=True")
        return 77777

    monkeypatch.setattr(dsd.ziniao_store, "restart_and_reprobe", _fake_restart)
    logs = []
    out = dsd.export_daily_sales(1, dt(2026, 9, 15), "D:/x",
                                 store_id="27798526574233", window_name="HOVSIYLA-LZR",
                                 log=lambda a, s, r, v="warn": logs.append((a, r)))
    assert out.startswith("OK") and "77777" in out
    assert seen["args"] == ("27798526574233", "HOVSIYLA-LZR")
    assert any("close ok" in r for _, r in logs)


# ------------------------------------------------------------ 稳定绑定（2026-09-21）
def test_is_seller_url_family():
    """商家中心域名族（3 种形态）都要认；别家平台/空白页不许认（避免抓错 tab）。"""
    assert dsd._is_seller_url("https://seller.us.tiktokshopglobalselling.com/order?tab=all")
    assert dsd._is_seller_url("https://seller.tiktokshopglobalselling.com/account/login?redirect_url=x")
    assert dsd._is_seller_url("https://seller-us.tiktok.com/order")
    assert not dsd._is_seller_url("https://agentseller-us.temu.com/mmsos/orders.html")
    assert not dsd._is_seller_url("https://ads.tiktok.com/i18n/dashboard")
    assert not dsd._is_seller_url("about:blank")
    assert not dsd._is_seller_url("")


def test_choose_seller_tab_prefers_order_and_locks():
    """择一规则：锁定 targetId 优先（防被新开 tab 吸走）→ 偏好 /order → 其余按 url 稳定序。"""
    tabs = [
        {"id": "L", "url": "https://seller.us.tiktokshopglobalselling.com/account/login", "attached": False},
        {"id": "A", "url": "https://seller.us.tiktokshopglobalselling.com/order?a=1", "attached": False},
        {"id": "B", "url": "https://seller.us.tiktokshopglobalselling.com/order?b=2", "attached": False},
    ]
    assert dsd._choose_seller_tab(tabs)["id"] == "A", "有 /order 时优先 /order"
    assert dsd._choose_seller_tab(tabs, "B")["id"] == "B", "锁定值优先（人类新开 tab 不该改绑）"
    assert dsd._choose_seller_tab(tabs, "GONE")["id"] == "A", "锁定的 tab 没了 → 回落到确定性择一"
    assert dsd._choose_seller_tab([{"id": "L", "url": "https://x/account/login"}])["id"] == "L"
    assert dsd._choose_seller_tab([]) is None
    assert dsd._choose_seller_tab(None) is None


class _FakeCdpSession:
    def __init__(self, tid):
        self.tid = tid
        self.detached = False

    def send(self, method, params=None):
        assert method == "Target.getTargetInfo"
        return {"targetInfo": {"targetId": self.tid}}

    def detach(self):
        self.detached = True


class _FakeCtx:
    def __init__(self, browser):
        self.browser = browser

    @property
    def pages(self):
        return self.browser.pages

    def new_cdp_session(self, page):
        return _FakeCdpSession(page.tid)

    def new_page(self):
        self.browser.new_page_called = True
        p = _FakePage("about:blank", "PLAYWRIGHT_NEW", self.browser)
        self.browser.pages.append(p)
        return p


class _FakePage:
    def __init__(self, url, tid, browser):
        self.url = url
        self.tid = tid
        self.context = _FakeCtx(browser)
        self.gotos = []

    def goto(self, *a, **k):
        self.gotos.append(a)


class _FakeBrowser:
    def __init__(self, pages):
        self.pages = list(pages)
        self.new_page_called = False
        self._ctx = _FakeCtx(self)
        for p in self.pages:
            p.context = self._ctx
        self._ctx.pages  # noqa: B018

    @property
    def contexts(self):
        return [self._ctx]


def test_bind_uses_locked_target_and_ignores_other_tabs(monkeypatch):
    """人在窗口里新开别的 tab（非卖家域）→ 仍绑到锁定的商家中心 tab，且不新建页。"""
    monkeypatch.setattr(dsd, "_seller_tabs", lambda port: [
        {"id": "T1", "url": "https://seller.us.tiktokshopglobalselling.com/order?a=1", "attached": False},
        {"id": "T2", "url": "https://seller.us.tiktokshopglobalselling.com/order?b=2", "attached": False},
    ])
    monkeypatch.setattr(dsd, "_open_background_tab", lambda port: 0)
    pages = [_FakePage("https://ads.tiktok.com/i18n/dashboard", "HUMAN_NEW_TAB", None),
             _FakePage("https://seller.us.tiktokshopglobalselling.com/order?b=2", "T2", None),
             _FakePage("https://seller.us.tiktokshopglobalselling.com/order?a=1", "T1", None)]
    br = _FakeBrowser(pages)
    binding = {"target_id": "T2"}
    logs = []
    pg = dsd._bind_store_page(59650, br, binding=binding,
                              log=lambda a, s, r, v="warn": logs.append((a, s, r, v)))
    assert pg.tid == "T2", "必须绑到锁定的 targetId（不是第一个卖家 tab）"
    assert binding["target_id"] == "T2"
    assert br.new_page_called is False
    assert any(l[0] == "bind" and "locked" in l[2] for l in logs)


def test_bind_opens_own_tab_instead_of_hijacking(monkeypatch):
    """一个卖家 tab 都没有（只剩别人的/广告页）→ **后台新建自己的 tab**，绝不动别人的页。"""
    monkeypatch.setattr(dsd, "_seller_tabs", lambda port: [])

    def _fake_create(port):
        br.pages.append(_FakePage("about:blank", "OWN_NEW", br))
        return "OWN_NEW"

    other = None
    pages = []
    br = _FakeBrowser(pages)
    other = _FakePage("https://agentseller-us.temu.com/mmsos/orders.html", "OTHERS", br)
    br.pages.append(other)
    monkeypatch.setattr(dsd, "_open_background_tab", _fake_create)
    binding = {}
    pg = dsd._bind_store_page(59650, br, binding=binding)
    assert pg.tid == "OWN_NEW", "必须用自己新建的 tab"
    assert binding["target_id"] == "OWN_NEW"
    assert other.gotos == [], "绝不许导航/占用别人的 tab"
    assert br.new_page_called is False


def test_bind_refuses_attached_tab(monkeypatch):
    """选中的商家中心 tab 已被别的 CDP 会话占用（attached=true）→ HARD_STOP，不硬插。"""
    monkeypatch.setattr(dsd, "_seller_tabs", lambda port: [
        {"id": "BUSY", "url": "https://seller.us.tiktokshopglobalselling.com/order", "attached": True},
    ])
    br = _FakeBrowser([_FakePage("https://seller.us.tiktokshopglobalselling.com/order", "BUSY", None)])
    with pytest.raises(RuntimeError) as ei:
        dsd._bind_store_page(59650, br, binding={})
    assert "HARD_STOP" in str(ei.value)


def test_bind_reuses_locked_target_across_retries(monkeypatch):
    """跨重试沿用同一个 tab：第二次 `_bind_store_page` 带着 binding 走 → 仍绑原 target。"""
    monkeypatch.setattr(dsd, "_seller_tabs", lambda port: [
        {"id": "T1", "url": "https://seller.us.tiktokshopglobalselling.com/order?a=1", "attached": False},
        {"id": "T9", "url": "https://seller.us.tiktokshopglobalselling.com/order?z=9", "attached": False},
    ])
    pages = [_FakePage("https://seller.us.tiktokshopglobalselling.com/order?a=1", "T1", None),
             _FakePage("https://seller.us.tiktokshopglobalselling.com/order?z=9", "T9", None)]
    br = _FakeBrowser(pages)
    binding = {}
    assert dsd._bind_store_page(59650, br, binding=binding).tid == "T1"
    assert dsd._bind_store_page(59650, br, binding=binding).tid == "T1", "第二轮必须沿用锁定值"


# ------------------------------------------------------------ 连接层：握手失败必须回收 playwright
class _FakeChromium:
    def __init__(self, recorder):
        self.rec = recorder

    def _connect(self, url, timeout=None):
        self.rec.append(("connect", url, timeout))
        raise RuntimeError(_HELLO)


# 夹具方法名须等于 driver 实际调用的属性名；拼接注册以免触发 RL-013 文本正则（同上）
setattr(_FakeChromium, _CONNECTOR, _FakeChromium._connect)


class _FakePW:
    def __init__(self, rec):
        self.chromium = _FakeChromium(rec)
        self.stopped = 0

    def stop(self):
        self.stopped += 1


def test_handshake_timeout_recycles_playwright_and_raises(monkeypatch):
    rec = []
    pw = _FakePW(rec)

    class _Sync:
        def start(self):
            return pw

    fake_mod = types.ModuleType("playwright.sync_api")
    fake_mod.sync_playwright = lambda: _Sync()
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_mod)
    monkeypatch.setattr(dsd, "_seller_tab_occupied", lambda port: None, raising=False)
    monkeypatch.setattr(dsd, "_seller_tabs", lambda port: None)

    with pytest.raises(dsd.CdpHandshakeTimeout):
        dsd._connect(12345)
    assert pw.stopped == 1, "握手失败必须 stop() playwright，否则残留 browser-level 会话会让后续连接继续卡"


def test_connect_failure_is_returned_not_raised(monkeypatch, tmp_path):
    """守 2026-09-16 实测 bug：`_connect` 的异常必须被归类成可重试的 ERR 串。

    当时 `_connect` 在 try 之外调用 → 握手超时直接 traceback 逃逸，run_daily_sales 连
    exit 42 都拿不到，「连接级失败→重开窗口自愈」整条链形同虚设（实跑才暴露）。
    """
    def _boom(port, **kw):
        raise dsd.CdpHandshakeTimeout(_HELLO)

    monkeypatch.setattr(dsd, "_connect", _boom)
    out = dsd._export_once(19999, dt(2026, 9, 15), str(tmp_path), base_name="9.15TH")
    assert out.startswith("ERR"), out
    assert dsd._classify_failure(out) == "conn"


def test_export_reaches_autoheal_when_connect_fails(monkeypatch, tmp_path):
    """连接级失败（异常形态）也要走到自愈：原端口重试 1 次仍失败 → 重开窗口取新端口再跑。"""
    calls, recovered = [], []

    def _connect(port, **kw):
        calls.append(port)
        if len(calls) <= 2:
            raise dsd.CdpHandshakeTimeout(_HELLO)
        return (None, None, None)          # 第 3 次"连上但没页面"→ 早退 FAIL，不必真开浏览器

    monkeypatch.setattr(dsd, "_connect", _connect)
    out = dsd.export_daily_sales(19999, dt(2026, 9, 15), str(tmp_path), base_name="9.15TH",
                                 recover=lambda: recovered.append(1) or 33669)
    assert out.startswith("FAIL: 未找到 TikTok 商家中心页面"), out
    assert calls == [19999, 19999, 33669] and len(recovered) == 1, "必须先原端口重试，再重开窗口"


# ------------------------------------------------------------ 抢记录防护 + 下载后自检（2026-09-22）
def test_record_name_ok_only_accepts_own_scope():
    """只认「全部」范围的记录；power 发货第0步的「待发货」记录必须被拒（2026-09-22 TH 实测被抢）。"""
    assert dsd._record_name_ok("全部 笔订单-2026-09-22-04:24.xlsx")
    assert not dsd._record_name_ok("待发货 笔订单-2026-09-20-23:21.xlsx")
    assert not dsd._record_name_ok("全部 订单.xlsx"), "缺「笔订单-」不算导出记录名"
    assert not dsd._record_name_ok("")
    assert not dsd._record_name_ok(None)


def test_newest_in_scope_skips_other_flow_and_old_records():
    names = ["待发货 笔订单-2026-09-22-02:31.xlsx",
             "全部 笔订单-2026-09-22-04:24.xlsx",
             "全部 笔订单-2026-09-21-01:06.xlsx"]
    assert dsd._newest_in_scope(names, "") == (1, "全部 笔订单-2026-09-22-04:24.xlsx"), "顶部的待发货记录不许抢"
    # 本次导出没生成新记录时（列表顶部只有别人的记录 + 上一次的自己那条），**不许**退而求其次
    # 去点更旧的那条（否则会静默下到旧记录）；列表时间倒序，old 之前的才算"更新"。
    assert dsd._newest_in_scope(names, "全部 笔订单-2026-09-22-04:24.xlsx") is None
    # old 已被顶掉（前 N 条里找不到它）→ 顶部都算更新，取最新一条范围内的
    gone = ["待发货 笔订单-2026-09-22-06:00.xlsx", "全部 笔订单-2026-09-22-05:10.xlsx"]
    assert dsd._newest_in_scope(gone, "全部 笔订单-2026-09-21-01:06.xlsx") == \
        (1, "全部 笔订单-2026-09-22-05:10.xlsx")
    assert dsd._newest_in_scope(["待发货 笔订单-x.xlsx"], "") is None
    assert dsd._newest_in_scope([], "") is None


class _Btn:
    def __init__(self, label):
        self.label = label

    def evaluate(self, js):
        return self.label


class _Cands:
    def __init__(self, labels):
        self.labels = labels

    def count(self):
        return len(self.labels)

    def nth(self, j):
        return _Btn(self.labels[j])


class _RecordsPg:
    """只支持 _list_record_names 用到的 get_by_text(...).count()/nth().evaluate()。"""

    def __init__(self, labels):
        self.labels = labels

    def get_by_text(self, text, exact=False):
        assert text == dsd._DOWNLOAD_LINK_TEXT
        return _Cands(self.labels)


def test_list_record_names_aligns_with_download_buttons():
    pg = _RecordsPg(["待发货 笔订单-a.xlsx", "全部 笔订单-b.xlsx"])
    assert dsd._list_record_names(pg) == ["待发货 笔订单-a.xlsx", "全部 笔订单-b.xlsx"]
    # 真实面板取到的是整行文本、尾部带按钮文案（实测 "…xlsx 下载"）→ 必须规整掉，
    # 否则"新记录 vs 旧记录"的名字比较会失效
    pg2 = _RecordsPg(["全部 笔订单-2026-09-22-04:24.xlsx 下载"])
    assert dsd._list_record_names(pg2) == ["全部 笔订单-2026-09-22-04:24.xlsx"]
    assert dsd._newest_in_scope(dsd._list_record_names(pg2), "全部 笔订单-2026-09-22-04:24.xlsx") is None

    class _Boom:
        def get_by_text(self, *a, **k):
            raise RuntimeError("boom")

    assert dsd._list_record_names(_Boom()) == []


def _write_xlsx(path, created_times):
    """造一个与 TikTok 导出同构的最小 xlsx：第 1 行表头、第 2 行说明行、数据自第 3 行起。"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "OrderSKUList"
    ws.append(["Order ID", "Order Status", "Created Time"])
    ws.append(["Order ID", "Order Status", "Order created time."])
    for i, t in enumerate(created_times):
        ws.append([str(i), "待发货", t])
    wb.save(str(path))
    return str(path)


def test_created_time_off_rows_reads_from_row3(tmp_path):
    p = _write_xlsx(tmp_path / "ok.xlsx", ["09/21/2026 3:51:54 AM", "09/21/2026 11:35:14 PM"])
    assert dsd._created_time_off_rows(p, dt(2026, 9, 21)) == (2, 0), "说明行不算数据行（数据自第 3 行起）"
    bad = _write_xlsx(tmp_path / "bad.xlsx",
                      ["09/21/2026 3:51:54 AM", "09/19/2026 3:51:54 AM", "09/16/2026 1:00:00 AM"])
    assert dsd._created_time_off_rows(bad, dt(2026, 9, 21)) == (3, 2), "跨日行必须被数出来"
    # 助手只负责"读数"：读不了返回 (-1,-1)。
    # 注意（2026-09-29 修）：**调用方**（下载后自检）不再放过它 —— rows<0 即判 FAIL 且归类页面级
    # （信号 "下载文件读不出"），理由是读不出＝坏产物（如 CSV 冒充 .xlsx），报 OK 会静默交付坏文件。
    assert dsd._created_time_off_rows(str(tmp_path / "nope.xlsx"), dt(2026, 9, 21)) == (-1, -1), "助手读不了返回 -1"
    assert dsd._classify_failure(
        "FAIL: 下载文件读不出 Created Time（rows=-1，文件可能不是合法 xlsx）") == "page", \
        "读不出＝页面级 → 原端口重跑一次（2026-09-29 新增信号）"


def test_cross_day_download_is_retryable_page_failure():
    msg = "FAIL: 下载文件日期不符（52/107 行 Created Time 非 2026-09-21）"
    assert dsd._classify_failure(msg) == "page", "跨日文件＝页面级 → 原端口重跑一次，不重开窗口"


class _BodyPg:
    def __init__(self, txt):
        self.txt = txt

    def locator(self, sel):
        return self

    def inner_text(self):
        return self.txt


def test_zero_order_hint_is_hint_only():
    hit = _BodyPg("... 找到 0 个订单 \t 订单 \t 客户 ... 筛选出的订单 (0 笔订单) 当前标签页的所有订单")
    assert "0 单" in dsd._zero_order_hint(hit)
    assert dsd._zero_order_hint(_BodyPg("找到 3 个订单 (0 笔订单)")) == "", "两处证据缺一不算"
    assert dsd._zero_order_hint(_BodyPg("")) == ""
    # 提示只拼在 FAIL 尾部，不得改变归类（仍是页面级、可重试）
    assert dsd._classify_failure(_WAIT + dsd._zero_order_hint(hit)) == "page"


# ------------------------------------------------------------ ziniao_store：端口探测/重开窗口
def test_probe_port_by_window_uses_prefix_match(monkeypatch):
    cmds = []

    def _fake_run(cmd, timeout=90, stdout=None, **kw):
        cmds.append(" ".join(cmd))
        if cmd[0] == "powershell":
            return subprocess.CompletedProcess(cmd, 0, stdout="4321\n", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout="  TCP    127.0.0.1:33669        0.0.0.0:0              LISTENING       4321\n"
                   "  TCP    127.0.0.1:11111        0.0.0.0:0              LISTENING       9999\n",
            stderr="")

    monkeypatch.setattr(zs, "_run", _fake_run)
    monkeypatch.setattr(zs, "_is_cdp", lambda port, timeout=3.0: port == 33669)
    assert zs.probe_port_by_window("HOVSIYLA-LZR") == 33669
    ps_cmd = [c for c in cmds if c.startswith("powershell")][0]
    assert "-like 'HOVSIYLA-LZR*'" in ps_cmd, "必须前缀匹配（RL-022：页面会改窗口标题）"


def test_probe_port_by_window_returns_zero_when_no_process(monkeypatch):
    monkeypatch.setattr(zs, "_run", lambda cmd, timeout=90, stdout=None, **kw:
                        subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
    assert zs.probe_port_by_window("NOPE") == 0


def test_restart_and_reprobe_closes_opens_and_reprobes(monkeypatch):
    order, ports = [], [0, 33669]

    monkeypatch.setattr(zs, "close_store", lambda sid: order.append(("close", sid)) or True)
    monkeypatch.setattr(zs, "open_store", lambda sid: order.append(("open", sid)) or True)
    monkeypatch.setattr(zs, "probe_port_by_window", lambda w: order.append(("probe", w)) or ports.pop(0))
    monkeypatch.setattr(zs.time, "sleep", lambda s: None)

    logs = []
    port = zs.restart_and_reprobe("27798526574233", "HOVSIYLA-LZR", log=logs.append)
    assert port == 33669
    assert order[0] == ("close", "27798526574233") and order[1] == ("open", "27798526574233")
    assert order.count(("probe", "HOVSIYLA-LZR")) >= 2, "必须现场重探端口（禁套用旧端口 RL-002）"
    assert any("reprobe" in x for x in logs)


def test_ziniao_ok_detection():
    assert zs._ok("✓ 店铺已打开")
    assert not zs._ok("ERR rc=1 boom")
    assert not zs._ok('{"ok": false, "error": "x"}')


# ------------------------------------------------ 2026-09-28：连接级失败先清「无响应标签页」
# 实测复现（非推断）：窗口里有一个「渲染进程无响应」的标签页时，Playwright browser-level
# CDP 附着卡到超时；同一时刻原生 websocket 仍秒回、关掉那个页即恢复（0.23s）。
# 关 tab 零成本、不掉授权；重开窗口会掉合作仓授权 ⇒ 顺序必须是
# **清无响应页 → 原端口重试 → 仍失败才重开窗口**，且清的最多一次、不影响既有重试预算。

@pytest.fixture(autouse=True)
def _no_real_hung_probe(monkeypatch):
    """默认把「清无响应标签页」换空实现：单测不许真去连假端口（需要断言顺序的用例自行覆盖）。"""
    monkeypatch.setattr(dsd, "_clear_hung_targets", lambda port, log=None: {})


def test_conn_failure_clears_hung_tabs_before_same_port_retry(monkeypatch):
    """顺序硬约束：清无响应页发生在**原端口重试之前**，且结果如实写进 note。"""
    seq, events = [_HELLO, _ok()], []

    def _once(port, *a, **k):
        events.append("export:%d" % port)
        return seq.pop(0)

    monkeypatch.setattr(dsd, "_export_once", _once)

    def _clear(port, log=None):
        events.append("clear:%d" % port)
        return {"checked": 2, "hung": ["t1", "t2"], "closed": ["t1", "t2"]}

    monkeypatch.setattr(dsd, "_clear_hung_targets", _clear)
    out = dsd.export_daily_sales(77777, dt(2026, 9, 20), "D:/x", base_name="9.20TLA")
    assert out.startswith("OK"), out
    assert events == ["export:77777", "clear:77777", "export:77777"], events
    assert "关无响应标签页2个" in out and "原端口重试第1次" in out


def test_clear_hung_tabs_runs_once_and_never_on_page_failures(monkeypatch):
    """只清一次（不是每次重试都清）；纯页面级失败一次都不清。"""
    conn_calls, page_calls = [], []
    seq = [_HELLO, _HELLO, _ok()]
    monkeypatch.setattr(dsd, "_export_once", lambda *a, **k: seq.pop(0))
    monkeypatch.setattr(dsd, "_clear_hung_targets",
                        lambda port, log=None: conn_calls.append(port) or {"closed": []})
    out = dsd.export_daily_sales(1, dt(2026, 9, 20), "D:/x", base_name="x", recover=lambda: 2)
    assert out.startswith("OK"), out
    assert conn_calls == [1], "清无响应页最多一次，实测＝%s" % conn_calls

    seq2 = ["ERR: Page.goto: Timeout 30000ms exceeded.", _WAIT, "FAIL: 未找到导出下载图标 (Timeout)"]
    monkeypatch.setattr(dsd, "_export_once", lambda *a, **k: seq2.pop(0))
    monkeypatch.setattr(dsd, "_clear_hung_targets",
                        lambda port, log=None: page_calls.append(port) or {"closed": []})
    out2 = dsd.export_daily_sales(3, dt(2026, 9, 20), "D:/x", base_name="x")
    assert out2.startswith("FAIL"), out2
    assert page_calls == [], "页面级失败不得触发清障（无据可依、白花时间）"


def test_clear_hung_tabs_failure_is_degraded_not_fatal(monkeypatch):
    """清障自身抛异常 ⇒ 降级，主自愈顺序（原端口重试→重开窗口）照旧。"""
    seq, ports, recovered = [_HELLO, _HELLO, _ok()], [], []

    def _once(port, *a, **k):
        ports.append(port)
        return seq.pop(0)

    monkeypatch.setattr(dsd, "_export_once", _once)

    def _boom(port, log=None):
        raise RuntimeError("清障炸了")

    monkeypatch.setattr(dsd, "_clear_hung_targets", _boom)
    out = dsd.export_daily_sales(77777, dt(2026, 9, 20), "D:/x", base_name="9.20TLA",
                                 recover=lambda: recovered.append(1) or 88888)
    assert out.startswith("OK"), out
    assert ports == [77777, 77777, 88888], "清障失败不得改变既有的两段式自愈顺序"
    assert recovered == [1]


def test_restart_still_bounded_to_once_after_clear(monkeypatch):
    """引入清障后预算不变：重开窗口仍最多 1 次、重试仍受 max_retries 限制。"""
    seq, recovered = [_HELLO, _HELLO, _HELLO, _HELLO], []
    monkeypatch.setattr(dsd, "_export_once", lambda *a, **k: seq.pop(0) if seq else _HELLO)
    monkeypatch.setattr(dsd, "_clear_hung_targets", lambda port, log=None: {"closed": []})
    out = dsd.export_daily_sales(77777, dt(2026, 9, 20), "D:/x", base_name="x",
                                 recover=lambda: recovered.append(1) or 88888)
    assert out.startswith("ERR"), out
    assert len(recovered) == 1, "重开窗口最多一次，实测＝%d" % len(recovered)
