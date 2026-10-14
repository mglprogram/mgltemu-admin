# -*- coding: utf-8 -*-
"""test_buylabel_route_table.py — **路线表** 的离线回归（不起浏览器）。

来历（用户 2026-09-21）：「我需要的是路线，这些所有的错误都是可以用步骤避免的，
不要用负面提示词去规避」。所以卡点不再靠「不要重复下单 / 不要连点」这类提示词，
而是：脚本现取信号（`collect_signals`）→ `pick_route()` 从
`data/temu_buylabel_routes.json` 取出该走的那条路线 → 按 steps 走。

本文件钉死四件事：
    ① 路线表结构合法（id 唯一、steps 非空、when 非空）
    ② when 表达式语义（==N / !=N / >N / >=N / <N / <=N / true|false / 键缺失不命中）
    ③ 今天真实遇到的信号 → 命中的路线正确（含 steps 顺序）
    ④ 「用步骤消除错误」的不变量：凡是要动浏览器的路线，**widen 必须排在 fill/place_only 之前**；
       凡有已取消/已发货单，**剔除步必须排在 fill 之前**；步骤名不许悬空（是 driver 的 action 字面量）
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_POWER, "lib"))

import temu_buylabel_driver as D                  # noqa: E402

ROUTES_FILE = os.path.join(_POWER, "data", "temu_buylabel_routes.json")
CFG_FILE = os.path.join(_POWER, "data", "temu_buylabel.json")
_PIPE = os.path.join(_POWER, "data", "temu_buylabel_pipeline.json")

#: 路线 steps 允许出现的名字：driver 的 action 字面量 + 本表定义的路线内子步
_ACTION_STEPS = {"screen", "widen", "auth", "fill", "place", "place_only", "confirm",
                 "verify", "record", "record_detail", "reorder", "pickup", "all", "plan"}
_SUBSTEPS = {"exclude_cancelled", "exclude_shipped", "stop_report"}


def _routes():
    return json.load(open(ROUTES_FILE, encoding="utf-8")).get("routes") or []


def _driver_text():
    return open(os.path.join(_POWER, "lib", "temu_buylabel_driver.py"), encoding="utf-8").read()


# ---------------------------------------------------------------- ① 结构
def test_routes_file_exists_and_nonempty():
    rs = _routes()
    assert rs, "路线表为空：%s" % ROUTES_FILE
    ids = [r.get("id") for r in rs]
    assert all(ids), "有路线缺 id"
    assert len(ids) == len(set(ids)), "路线 id 重复：%s" % ids


def test_every_route_has_when_and_steps():
    for r in _routes():
        assert r.get("when"), "%s 缺 when（判据）" % r.get("id")
        assert isinstance(r.get("steps"), list) and r["steps"], "%s 缺 steps" % r.get("id")


def test_route_steps_are_known_names():
    known = _ACTION_STEPS | _SUBSTEPS
    for r in _routes():
        for s in r["steps"]:
            assert s in known, "%s 的步骤名悬空：%s" % (r.get("id"), s)


def test_action_steps_exist_in_driver():
    """凡被当作 action 的步骤名，driver 里必须能找到该字面量（否则跑到那步才炸）。"""
    text = _driver_text()
    for r in _routes():
        for s in r["steps"]:
            if s in _ACTION_STEPS:
                assert s in text, "%s 的 action 步骤 '%s' 在 driver 里找不到" % (r.get("id"), s)


# ---------------------------------------------------------------- ② when 语义
def test_when_operators():
    ok = D._when_ok
    assert ok({"n": 3}, {"n": ">0"}) is True
    assert ok({"n": 0}, {"n": ">0"}) is False
    assert ok({"n": 3}, {"n": ">=3"}) is True
    assert ok({"n": 2}, {"n": ">=3"}) is False
    assert ok({"n": 0}, {"n": "==0"}) is True
    assert ok({"n": 0}, {"n": "!=0"}) is False
    assert ok({"n": 1}, {"n": "<2"}) is True
    assert ok({"n": 2}, {"n": "<=2"}) is True
    assert ok({"b": True}, {"b": "true"}) is True
    assert ok({"b": False}, {"b": "false"}) is True
    assert ok({"b": True}, {"b": "false"}) is False


def test_when_missing_key_never_matches():
    """信号没采到 ⇒ 不命中（不猜）。这条防止「采不到就当通过」的静默放行。"""
    assert D._when_ok({"pending": 3}, {"cancelled": "0"}) is False


# ---------------------------------------------------------------- ③ 今天的真实信号 → 路线
def _pick(sig):
    return D.pick_route(sig, cfg={})


def test_signal_all_pending_routes_to_normal_chain_with_widen_first():
    r = _pick({"page_kind": "orders", "want": 30, "pending": 30, "shipped": 0,
               "cancelled": 0, "not_found": 0, "fail": 0, "modal_offscreen": False})
    assert r["id"] == "orders_ready", r
    assert r["steps"].index("widen") < r["steps"].index("fill") < r["steps"].index("place_only")


def test_signal_cancelled_present_excludes_before_buying():
    """今天 BEL/BHY 各 1 单「平台已取消」→ 必须先剔除再买（不是靠提示词提醒）。"""
    r = _pick({"page_kind": "orders", "want": 31, "pending": 30, "shipped": 0,
               "cancelled": 1, "not_found": 0, "fail": 0, "modal_offscreen": False})
    assert r["id"] == "order_cancelled", r
    assert r["steps"][0] == "exclude_cancelled"
    assert r["steps"].index("fill") > r["steps"].index("exclude_cancelled")


def test_signal_shipped_present_excludes():
    """「已发货 = 已有面单」→ 剔除（同批不重复下单的执行落点）。"""
    r = _pick({"page_kind": "orders", "want": 68, "pending": 66, "shipped": 2,
               "cancelled": 0, "not_found": 0, "fail": 0, "modal_offscreen": False})
    assert r["id"] == "order_shipped", r
    assert "exclude_shipped" in r["steps"]


def test_signal_modal_offscreen_widens_first():
    """今天 BHY 62 单下不动的真因：关闭键在视口外 ⇒ 先 widen。"""
    r = _pick({"page_kind": "orders", "want": 62, "pending": 62, "shipped": 0,
               "cancelled": 0, "not_found": 0, "fail": 0, "modal_offscreen": True})
    assert r["id"] == "modal_offscreen", r
    assert r["steps"][0] == "widen"


def test_signal_partial_failure_goes_to_reorder():
    r = _pick({"page_kind": "orders", "want": 62, "pending": 1, "shipped": 60,
               "cancelled": 0, "not_found": 0, "fail": 1, "modal_offscreen": False})
    assert r["id"] == "order_shipped", r          # 已发货优先（先剔除那一单）
    r2 = _pick({"page_kind": "orders", "want": 62, "pending": 0, "shipped": 0,
                "cancelled": 0, "not_found": 0, "fail": 1, "modal_offscreen": False})
    assert r2["id"] == "partial_failure", r2
    assert "record_detail" in r2["steps"] and "reorder" in r2["steps"]


def test_signal_auth_required_routes_to_auth():
    r = _pick({"page_kind": "authentication", "want": 29, "pending": 0, "shipped": 0,
               "cancelled": 0, "not_found": 0, "fail": 0, "modal_offscreen": False})
    assert r["id"] == "auth_required", r
    assert r["steps"][0] == "auth"


def test_signal_nothing_found_stops():
    r = _pick({"page_kind": "orders", "want": 29, "pending": 0, "shipped": 0,
               "cancelled": 0, "not_found": 29, "fail": 0, "modal_offscreen": False})
    assert r["id"] == "no_orders", r
    assert r["steps"] == ["stop_report"]


def test_signal_only_cancelled_and_shipped_nothing_to_buy():
    """今天重复投递那几批的真实信号：可买的 0 单（29 已发货 + 1 已取消）⇒ 报表并结束，不产生购买。"""
    r = _pick({"page_kind": "orders", "want": 31, "pending": 0, "shipped": 29,
               "cancelled": 1, "skippable": 30, "not_found": 1, "fail": 0,
               "modal_offscreen": False})
    assert r["id"] == "nothing_to_buy", r
    assert r["steps"][-1] == "stop_report"
    assert "place_only" not in r["steps"], "可买 0 单的路线里不该出现花钱步骤"


def test_unmatched_signals_stop_with_reason():
    r = _pick({"page_kind": "orders"})            # 键不全 ⇒ 谁都不命中
    assert r["id"] == "no_route_matched", r
    assert r["steps"] == ["stop_report"]


# ---------------------------------------------------------------- ④ 「用步骤消除错误」的不变量
def test_route_entries_have_no_negative_prompt_wording():
    """**路线条目本身**（判据/步骤/为什么）说的是「怎么走」，不写「不要…」式提示词。

    口径：只查 routes 数组里的条目（文件级 `_说明` 是来历引文，不算规则）。
    """
    for r in _routes():
        blob = json.dumps(r, ensure_ascii=False)
        for bad in ("不要", "禁止", "严禁", "不得", "避免"):
            assert bad not in blob, "路线 %s 里出现否定式提示词「%s」——应改写成步骤" % (r.get("id"), bad)


def test_routes_with_browser_writes_put_widen_before_place():
    for r in _routes():
        steps = r["steps"]
        write = "place_only" if "place_only" in steps else ("place" if "place" in steps else None)
        if write:
            assert "widen" in steps, "%s 有钱步骤却没有 widen 步" % r.get("id")
            assert steps.index("widen") < steps.index(write), \
                "%s 的 widen 没排在 %s 之前" % (r.get("id"), write)


def test_pipeline_puts_screen_and_widen_before_fill():
    """步骤链里：分诊(screen) → 拉宽(widen) **都在 fill（写操作）之前**。"""
    spec = json.load(open(_PIPE, encoding="utf-8"))
    names = [s.get("name") for s in (spec.get("steps") or [])]
    assert "screen" in names and "widen" in names, names
    assert names.index("screen") < names.index("fill"), names
    assert names.index("widen") < names.index("fill"), names


# ---------------------------------------------------------------- ⑤ 失败重下：分诊与选渠道（2026-09-21 由散文落成程序）
def _cfg():
    return json.load(open(CFG_FILE, encoding="utf-8"))


def test_failure_triage_rules_live_in_data():
    """分诊规则必须在**数据**里（`failure_retry.triage`），不在散文里。"""
    tri = _cfg().get("failure_retry", {}).get("triage")
    assert isinstance(tri, list) and tri, "failure_retry.triage 缺失"
    assert any(not (r.get("match") or []) for r in tri), "缺兜底条目（空 match 那条）"


def test_triage_address_invalid_switches_carrier():
    """2026-09-14 实测的地址类失败：必须换承运商（否则同承运商更贵的必复现）。"""
    tri = D.triage_failure("错误原因：Invalid ShipTo AddressLine2", _cfg())
    assert tri["strategy"] == "switch_carrier", tri


def test_triage_no_carrier_changes_warehouse_first():
    tri = D.triage_failure("物流商无法支持该地址", _cfg())
    assert tri["strategy"] == "warehouse_then_cost", tri


def test_triage_falls_back_to_cost_asc():
    tri = D.triage_failure("某个没见过的错误", _cfg())
    assert tri["strategy"] == "cost_asc", tri


def test_switch_carrier_skips_the_failed_brand():
    opts = [{"text": "UPS, Ground saver 预估$8.00; 2-6 个工作日送达", "disabled": False},
            {"text": "FedEx, ground economy 预估$9.90; 2-6 个工作日送达", "disabled": False}]
    ch = D.choose_reorder_service(opts, "UPS, Ground saver", "switch_carrier", _cfg())
    assert str(ch["service"]).startswith("FedEx"), ch
    assert ch["how"].startswith("switch_carrier"), ch


def test_cost_asc_takes_cheapest_available_and_skips_disabled():
    opts = [{"text": "GOFO, Standard 预估$3.10; 3-5 个工作日送达", "disabled": False},
            {"text": "SpeedX, standard 预估$2.90; 3-5 个工作日送达", "disabled": False},
            {"text": "UniUni, Standard 预估$2.50; 3-5 个工作日送达", "disabled": True}]
    ch = D.choose_reorder_service(opts, "", "cost_asc", _cfg())
    assert str(ch["service"]).startswith("SpeedX"), ch
    assert ch["price"] == 2.90, ch


def test_parse_service_from_failed_row_text():
    txt = ("PO-211-1 失败 UPS, Ground saver 预估$6.28 包裹号 PK-1 错误原因 Invalid ShipTo AddressLine2")
    assert D.parse_service_from_text(txt, _cfg()).startswith("UPS")

