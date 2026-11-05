# -*- coding: utf-8 -*-
"""test_login_state_fingerprint.py — 「所有页面登录态突然全部消失」的可归因化 + 切店弹窗红线。

来历（用户 2026-11-05 原话）
    「为什么你去执行 BEL 的任务时，莫名其妙的所有页面的登录态全部消失，要重新登录：修复」

实测排查结论（本机当时数据）
    · 两店 `Network/Cookies` 都在：`seller_temp`（到次日）、`sessionType`（到 11-08）、
      `mallid`（到 12-05）齐全 ⇒ **不是本地 cookie 被清**；
    · 两店 × 两域（us / cc）真请求探针全 `ok` ⇒ 排查那一刻是登录着的；
    · 所以"全体页面同时失效"只可能来自**会话侧**动作：
        ① `ziniao-cli store close`（关窗 → 所有页消失，重开必掉登录態）；
        ② Seller Central 的「当前已有登录中的店铺，确认切换吗」被点「确认切换」
           ⇒ 本窗口换店登录，原店铺所有标签同时失效。

本文件钉四件事
    ① 指纹读得到、读不到都要老实说，绝不把"读不到"当成"掉了"（避免假告警）；
    ② `lost()` 既抓"会话 cookie 消失"，也抓 `mallid` **换值**（换店）；
    ③ 切店弹窗**默认只关不换店**，要多点 `确认切换` 必须显式设环境变量；
    ④ 关窗重开这类破坏性自愈前后必须留登录态证据。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(os.path.dirname(_HERE), "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import pytest  # noqa: E402

import login_state as ls  # noqa: E402


def _ck(name, value="x", domain="agentseller.temu.com"):
    return {"name": name, "value": value, "domain": domain}


FULL = [_ck("seller_temp", "t1"), _ck("mallid", "123456"), _ck("sessionType", "1"),
        _ck("api_uid", "u")]


# ---------------------------------------------------------------- ① 指纹
def test_fingerprint_reads_session_cookies():
    fp = ls.fingerprint(1234, fetch=lambda p, *a: FULL)
    assert fp["ok"] is True
    assert fp["cookies"] == 4
    assert fp["session"] == {"seller_temp": True, "mallid": True, "sessionType": True}
    assert fp["mallid"] == "123456"
    assert len(fp["digest"]) == 10 and "t1" not in fp["digest"], "digest 只记名集合，不泄 cookie 值"


def test_fingerprint_flags_missing_session_cookie():
    fp = ls.fingerprint(1234, fetch=lambda p, *a: [_ck("api_uid", "u")])
    assert fp["ok"] is True, "读到了就不算读不到"
    assert fp["session"]["seller_temp"] is False


def test_fingerprint_unreadable_is_not_a_loss():
    """读不到 ≠ 掉了。副作用必须落在 `ok=False`，否则每次端口抖动都会假告警。"""
    def _boom(p, *a):
        raise RuntimeError("CDP 挂了")

    fp = ls.fingerprint(1234, fetch=_boom)
    assert fp["ok"] is False and fp["error"]
    assert ls.lost(fp, {"ok": True, "session": {"seller_temp": True}, "mallid": "1"}) == [], \
        "有一侧读不到就必须拒绝下结论"


def test_fingerprint_without_port():
    fp = ls.fingerprint(0, fetch=lambda p, *a: FULL)
    assert fp["ok"] is False and fp["error"] == "no-port"


def test_fingerprint_filters_foreign_hosts():
    mixed = FULL + [_ck("sid", "s", domain="example.com"), _ck("x", "y", domain="google.com")]
    fp = ls.fingerprint(1234, fetch=lambda p, *a: mixed)
    assert fp["cookies"] == 4, "只数 temu / kuajing 域；实得 %s" % fp["cookies"]


def test_fingerprint_tolerates_fetch_with_one_arg():
    """真实 `_default_fetch(port, timeout)`；测试注入点常只收一个参数，不能因此炸。"""
    fp = ls.fingerprint(1234, fetch=lambda p: FULL)
    assert fp["ok"] is True


# ---------------------------------------------------------------- ② lost()
def test_lost_detects_missing_cookie():
    a = ls.fingerprint(1, fetch=lambda p, *x: FULL)
    b = ls.fingerprint(1, fetch=lambda p, *x: [_ck("mallid", "123456")])
    gone = ls.lost(a, b)
    assert "seller_temp" in gone and "sessionType" in gone


def test_lost_detects_store_switch_even_when_all_cookies_present():
    """最阴的一种：cookie 一个不少，但 `mallid` 换了 ⇒ 已经换店登录了。"""
    a = ls.fingerprint(1, fetch=lambda p, *x: FULL)
    b = ls.fingerprint(1, fetch=lambda p, *x: [
        _ck("seller_temp", "t2"), _ck("mallid", "999999"), _ck("sessionType", "1")])
    gone = ls.lost(a, b)
    assert any(x.startswith("mallid:") for x in gone), "换店必须被抓到：%s" % gone
    assert "seller_temp" not in gone, "值变了不等于没了"


def test_lost_is_silent_when_nothing_changed():
    a = ls.fingerprint(1, fetch=lambda p, *x: FULL)
    b = ls.fingerprint(1, fetch=lambda p, *x: FULL)
    assert ls.lost(a, b) == []


# ---------------------------------------------------------------- ③ watch()
def test_watch_reports_loss_without_blocking():
    seq = [FULL, [_ck("mallid", "123456")]]
    msgs = []

    def _fetch(p, *a):
        return seq.pop(0)

    with ls.watch(1234, "reopen", log=msgs.append, fetch=_fetch):
        pass
    text = "\n".join(msgs)
    assert "reopen 期间丢了 seller_temp" in text, text
    assert "要重新登录" in text


def test_watch_passes_through_exceptions():
    """留证绝不能吞掉业务异常（它可能正在等上层按错误处理）。"""
    def _fetch(p, *a):
        return FULL

    with pytest.raises(ValueError):
        with ls.watch(1234, "x", log=lambda m: None, fetch=_fetch):
            raise ValueError("业务炸了")


# ---------------------------------------------------------------- ④ 切店弹窗红线
class _FakeLoc:
    def __init__(self, texts, clicked):
        self.texts = texts
        self.clicked = clicked

    def count(self):
        return 1 if self.texts else 0

    @property
    def first(self):
        """真 Playwright 里 `first` 是**属性**（`.first.click()`），照抄这个形状。"""
        return self

    def click(self, timeout=0):
        self.clicked.append(self.texts)


class _FakePg:
    """最小页面壳：只实现 evaluate / get_by_text / wait_for_timeout。"""

    def __init__(self, dialog_text, buttons, clicked):
        self.dialog_text = dialog_text
        self.buttons = buttons
        self.clicked = clicked
        self.waited = []

    def evaluate(self, js, arg=None):
        if not self.dialog_text:
            return '{"dialog": false}'
        import json
        return json.dumps({"dialog": True, "buttons": self.buttons, "text": self.dialog_text})

    def get_by_text(self, txt, exact=True):
        return _FakeLoc(txt if txt in self.buttons else "", self.clicked)

    def wait_for_timeout(self, ms):
        self.waited.append(ms)


def _atd():
    import auth_temu_driver
    return auth_temu_driver


def test_switch_dialog_default_does_not_switch_store(monkeypatch):
    """默认点「不切换」：同样解除遮挡，但**不换店**。"""
    monkeypatch.delenv("TEMU_ALLOW_STORE_SWITCH", raising=False)
    clicked = []
    pg = _FakePg("当前已有登录中的店铺，是否切换？", ["确认切换", "不切换"], clicked)
    assert _atd()._dismiss_switch_dialog(pg) is True
    assert clicked == ["不切换"], "默认只许点「不切换」；实点 %s" % clicked


def test_switch_dialog_falls_back_to_cancel(monkeypatch):
    monkeypatch.delenv("TEMU_ALLOW_STORE_SWITCH", raising=False)
    clicked = []
    pg = _FakePg("当前已有登录中的店铺", ["确认切换", "取消"], clicked)
    assert _atd()._dismiss_switch_dialog(pg) is True
    assert clicked == ["取消"]


def test_switch_dialog_only_switch_when_explicitly_allowed(monkeypatch):
    monkeypatch.setenv("TEMU_ALLOW_STORE_SWITCH", "1")
    clicked = []
    pg = _FakePg("当前已有登录中的店铺", ["确认切换", "不切换"], clicked)
    assert _atd()._dismiss_switch_dialog(pg) is True
    assert clicked == ["确认切换"]


def test_switch_dialog_with_no_safe_button_clicks_nothing(monkeypatch):
    """只有「确认切换」时**不许点**：那会把当前店铺踢下线（本次事故的直接成因）。"""
    monkeypatch.delenv("TEMU_ALLOW_STORE_SWITCH", raising=False)
    clicked = []
    pg = _FakePg("当前已有登录中的店铺", ["确认切换"], clicked)
    assert _atd()._dismiss_switch_dialog(pg) is False
    assert clicked == [], "没有安全按钮就必须一个都不点；实点 %s" % clicked


def test_no_dialog_no_click():
    clicked = []
    pg = _FakePg("", [], clicked)
    assert _atd()._dismiss_switch_dialog(pg) is False
    assert clicked == []


def test_allow_switch_defaults_off(monkeypatch):
    monkeypatch.delenv("TEMU_ALLOW_STORE_SWITCH", raising=False)
    assert _atd()._allow_store_switch() is False
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("TEMU_ALLOW_STORE_SWITCH", v)
        assert _atd()._allow_store_switch() is True
    monkeypatch.setenv("TEMU_ALLOW_STORE_SWITCH", "0")
    assert _atd()._allow_store_switch() is False


# ---------------------------------------------------------------- ⑤ 源码契约
def test_region_goto_uses_dismiss_helper():
    """`_click_region_goto` 不许再自己点「确认切换」——必须走 `_dismiss_switch_dialog`。"""
    src = open(os.path.join(_LIB, "auth_temu_driver.py"), encoding="utf-8").read()
    body = src.split("def _click_region_goto", 1)[1].split("\ndef ", 1)[0]
    assert "_dismiss_switch_dialog(pg)" in body, "区域点击必须走「默认不换店」的处置"
    assert 'get_by_text("确认切换"' not in body, "不许再无条件点确认切换（会踢掉当前店铺会话）"


def test_abnormal_driver_also_avoids_blind_switch():
    src = open(os.path.join(_LIB, "temu_abnormal_driver.py"), encoding="utf-8").read()
    assert "_dismiss_switch_dialog" in src, "合作仓驱动同样要守这条红线"
    assert '["确认切换", "确认"]' not in src, "旧的硬编码「确认切换」点法必须清掉"


def test_restart_path_records_login_state():
    """关窗重开前后必须留登录态证据，否则下次还是只能靠人回忆。"""
    src = open(os.path.join(_LIB, "ziniao_store.py"), encoding="utf-8").read()
    i = src.find("def restart_and_reprobe")
    seg = src[i:i + 4000]
    assert "login_state" in seg and "fingerprint" in seg, "重开前后没有登录态留证"
    assert "lost(" in seg, "重开后没有对账「掉了什么」"


# ---------------------------------------------------------------- ⑥ 会话票自然到期
def test_hours_left_and_expiring():
    """会话票 24 小时硬到期：读得到剩余寿命，快到期必须提前喊。"""
    import time
    now = time.time()

    def _fp(hours):
        return {"ok": True, "expires": {"seller_temp": now + hours * 3600}}

    assert 4.9 < ls.hours_left(_fp(5)) < 5.1
    assert ls.expiring(_fp(5)) == "", "还早，不告警"
    assert "只剩" in ls.expiring(_fp(1)), "1 小时内到期必须告警"
    assert ls.expiring(_fp(-1) if False else {"ok": True, "expires": {"seller_temp": now - 60}}), \
        "已过期也算要告警"
    assert ls.expiring({"ok": False}) == "", "读不到不告警（避免假警）"
    assert ls.hours_left({"ok": True, "expires": {}}) == -1.0


def test_describe_shows_expiry():
    import time
    fp = {"ok": True, "cookies": 3, "session": {"seller_temp": True, "mallid": True,
                                                "sessionType": True},
          "expires": {"seller_temp": time.time() + 3600}, "digest": "abcd"}
    assert "到 " in ls.describe(fp)


def test_auth_guard_logs_fingerprint_and_expiry():
    """兜底入口必须每次都留指纹 + 到期告警（这是"什么时候丢的"唯一线索）。"""
    src = open(os.path.join(_LIB, "auth_guard.py"), encoding="utf-8").read()
    assert "login_state" in src and "fingerprint" in src
    assert "expiring(" in src, "会话票 24h 到期这条主因没写进日志"


# ---------------------------------------------------------------- ⑦ 另外两份实现也得守
_BEL = os.path.join(os.path.dirname(os.path.dirname(_LIB)), "bel_goods")


def test_flux_auth_has_safe_switch_dialog():
    """流量加速域是**独立的第二份授权实现**，同样不许盲点「确认切换」。"""
    src = open(os.path.join(_BEL, "flux_auth.py"), encoding="utf-8").read()
    assert "def dismiss_switch_dialog" in src and "def allow_store_switch" in src
    assert "dismiss_switch_dialog(ws_auth)" in src, "认证页分支没有处置切店弹窗"
    i = src.find("def dismiss_switch_dialog")
    seg = src[i:i + 2200]
    assert "allow_store_switch()" in seg, "没有按开关决定点哪颗"


def test_click_region_js_defaults_to_no_switch():
    """`click_region.js` 旧实现是「优先点确认切换」——必须改成默认「不切换」。"""
    src = open(os.path.join(_BEL, "click_region.js"), encoding="utf-8").read()
    assert "__TEMU_ALLOW_STORE_SWITCH" in src, "没有换店开关"
    assert "switch-dialog-unsafe" in src, "只有「确认切换」时没有「什么都不点」的分支"
    # 旧的那句 `find(...'确认切换') || dlgBtns[0]` 是无条件换店，必须消失
    assert ".find(e => (e.innerText || '').trim() === '确认切换') || dlgBtns[0]" not in src


# ---------------------------------------------------------------- ⑧ 导出后默认不关窗
def test_sales_export_keeps_window_by_default():
    """导出成功后**默认不关窗**（关窗 = 用户所有标签页消失 + 下次要重新登录）。

    用源码契约而不是 import：`run_temu_sales` 在**模块级**就
    `sys.stdout = TextIOWrapper(sys.stdout.buffer)`，在 pytest 捕获下 import 会炸；
    为了测一个早退分支去动生产代码不值得。判据看三件事：
      · 有显式开关；
      · 开关未开时**先**打印"保留窗口"并 return，排在 `ziniao-cli store close` 之前；
      · 开关打开才真的执行 close。
    """
    path = os.path.join(os.path.dirname(_LIB), "run_temu_sales.py")
    src = open(path, encoding="utf-8").read()
    i = src.find("def _close_store_window")
    assert i > 0
    body = src[i:src.find("\ndef ", i + 10)]

    assert "TEMU_CLOSE_STORE_AFTER_EXPORT" in body, "没有「默认不关窗」的开关"
    skip_at = body.find("保留窗口")
    close_at = body.find("store close")
    assert 0 < skip_at < close_at, "「保留窗口」的早退必须排在真正 close 之前"
    assert "not in (" in body and "return" in body, "开关未开时必须早退"
