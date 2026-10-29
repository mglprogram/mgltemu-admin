# -*- coding: utf-8 -*-
"""test_ziniao_store_dead_tabs.py — 「跑脚本前先清卡死页」的离线回归（不起浏览器）。

来历（用户 2026-09-24）：「残留死页把 playwright attach 拖死……这已经是第三次为它排障了——
真该在流程里加一步『跑脚本前先清卡死页』」。

2026-09-24 事故（把窗口关没）后的**硬规矩**（本文件钉死）：
  ① "页面数"只算**真实标签页** —— 紫鸟窗口恒有一个 `chrome-extension://…/offscreen` 杂页，
     算进去会让"最后一个页面"判错；
  ② **最后一个真实标签页永不关** —— 新开窗口只有 [卖家中心登录页 + 扩展杂页]，
     把登录页关掉 ⇒ 整个窗口消失 ⇒ 端口失效 ⇒ 紧接着连死端口（WinError 1225）；
  ③ 清障后自检端口是否还活（`window_alive`），调用方据此决定要不要重开窗口；
  ④ best-effort：拿不到端口/读列表失败/单页探针失败都不抛异常；
  ⑤ 三个会 attach 的入口**在连浏览器前后真的调了它**。
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_POWER, "lib"))

import ziniao_store as zs  # noqa: E402

BLANK = "https://seller.kuajingmaihuo.com/settle/seller-login?redirectUrl=x"
AUTH = "https://agentseller-us.temu.com/auth/authentication?redirectUrl=x"
LOGIN = "https://seller.kuajingmaihuo.com/login?redirectUrl=%2Fmmsos"
ORDERS = "https://agentseller-us.temu.com/mmsos/orders.html"
EXT = "chrome-extension://dkmfchocknfallppgbpegkppapkbjhml/offscreen.html"


class _FakeCdp:
    """假的目标级探针：按 URL 返回 DOM 摘要。"""

    def __init__(self, port, target):
        self.target = target

    def evaluate(self, js, arg=None):
        u = str(self.target.get("url") or "")
        if u == BLANK:
            return {"textLen": 0, "text": "", "visInputs": 0}
        if u == AUTH:
            return {"textLen": 32, "text": "Beta 中文 商家中心 中国地区 商家中心 其他地区 敬请期待", "visInputs": 1}
        return {"textLen": 500, "text": "订单列表", "visInputs": 3}


def _setup(monkeypatch, targets, list_error=None, alive=True):
    closed = []

    def _list(port):
        if list_error:
            raise RuntimeError("boom")
        return targets

    monkeypatch.setattr(zs, "list_page_targets", _list)
    monkeypatch.setattr(zs, "close_target", lambda port, tid: closed.append(tid) or True)
    monkeypatch.setattr(zs, "window_alive", lambda port: alive)
    fake = type(sys)("cdp_transport")
    fake.CdpPage = _FakeCdp
    monkeypatch.setitem(sys.modules, "cdp_transport", fake)
    return closed


def _t(tid, url):
    return {"id": tid, "url": url, "type": "page"}


# ---------------------------------------------------------------- ① 事故原型（必须钉死）
def test_last_real_tab_is_never_closed_even_with_extension_page(monkeypatch):
    """**事故原型**：窗口里只有 [卖家中心登录页 + 扩展杂页] ⇒ 登录页是"最后一个真实标签页"，不许关。"""
    targets = [_t("t-login", LOGIN), _t("t-ext", EXT)]
    closed = _setup(monkeypatch, targets)
    out = zs.clean_dead_tabs(port=1234)
    assert closed == [], "把最后的真实标签页关了＝整个窗口消失（实测 WinError 1225）；实关 %s" % closed
    assert out["real_tabs"] == 1, "扩展杂页不算真实标签页（算成 2 就会误判「不是最后一个」）"
    assert any("最后一个真实标签页" in str(r.get("text")) for r in out["kept"])


def test_extension_page_is_never_counted_or_closed(monkeypatch):
    targets = [_t("t-ext", EXT), _t("t-orders", ORDERS), _t("t-login", LOGIN)]
    closed = _setup(monkeypatch, targets)
    out = zs.clean_dead_tabs(port=1234)
    assert "t-ext" not in closed, "扩展杂页不许碰"
    assert closed == ["t-login"], "有真实业务页在时，入口页可以关；实关 %s" % closed
    assert out["real_tabs"] == 2, "真实标签页只算两个（订单页 + 登录页）"


def test_blank_page_closed_with_orders_kept(monkeypatch):
    targets = [_t("t-blank", BLANK), _t("t-orders", ORDERS), _t("t-ext", EXT)]
    closed = _setup(monkeypatch, targets)
    zs.clean_dead_tabs(port=1234)
    assert closed == ["t-blank"]


# ---------------------------------------------------------------- ③ 收尾自检
def test_reports_window_gone(monkeypatch):
    targets = [_t("t-orders", ORDERS)]
    _setup(monkeypatch, targets, alive=False)
    out = zs.clean_dead_tabs(port=1234)
    assert out["window_alive"] is False
    assert "window_gone" in out["errors"], "窗口没了必须如实记，调用方据此重开而不是连死端口"


# ---------------------------------------------------------------- ④ best-effort
def test_best_effort_never_raises(monkeypatch):
    _setup(monkeypatch, [], list_error=True)
    out = zs.clean_dead_tabs(port=1234)
    assert out["errors"] and out["closed"] == []
    out2 = zs.clean_dead_tabs(port=0, window_name="")
    assert "no-port" in " ".join(out2["errors"]), "拿不到端口要如实记，不抛"


def test_dry_run_only_reports(monkeypatch):
    targets = [_t("t-blank", BLANK), _t("t-orders", ORDERS)]
    closed = _setup(monkeypatch, targets)
    out = zs.clean_dead_tabs(port=1234, dry_run=True)
    assert closed == [] and out["dead"] and not out["closed"]


def test_is_real_tab_helper():
    assert zs.is_real_tab(ORDERS) and zs.is_real_tab(LOGIN) and zs.is_real_tab("about:blank")
    assert not zs.is_real_tab(EXT) and not zs.is_real_tab("devtools://x") and not zs.is_real_tab("")


def test_config_file_is_data_driven():
    p = os.path.join(_POWER, "data", "dead_tabs.json")
    d = json.load(open(p, encoding="utf-8"))
    cfg = zs.load_dead_tab_cfg()
    assert cfg["candidate_patterns"] == d["candidate_patterns"]
    assert any("seller-login" in c for c in cfg["candidate_patterns"]), "少了那个已经被排障三次的死页特征"
    assert "硬规矩" in json.dumps(d, ensure_ascii=False), "事故口径没写进数据文件"


# ---------------------------------------------------------------- ⑤ 接线
def test_entries_call_cleanup_before_attaching():
    # 2026-10-29：`run_daily_sales.py` 在迁移中已删除（本仓只剩这两个会 attach 的入口）
    for rel in ("run_temu_auth.py", "run_temu_sales.py"):
        src = open(os.path.join(_POWER, rel), encoding="utf-8").read()
        assert "clean_dead_tabs" in src, "%s 没接清障步" % rel
        assert 0 <= src.find("json/version") < src.find("clean_dead_tabs"), "%s 的清障步要接在「端口可连」之后" % rel


def test_auth_entry_reacts_to_dead_window():
    """授权入口：清障后若窗口没了，必须**重开窗口**再连，不许接着去连死端口。"""
    src = open(os.path.join(_POWER, "run_temu_auth.py"), encoding="utf-8").read()
    i = src.find("clean_dead_tabs")
    seg = src[i:i + 1400]
    assert "window_alive" in seg and "open_store" in seg, "窗口被清没时没有重开兜底（会连死端口）"
