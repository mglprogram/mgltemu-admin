# -*- coding: utf-8 -*-
"""test_buylabel_routes.py — **入口（两店）** 与 **中途路线** 的离线回归测试（不起浏览器）。

来历（用户 2026-09-15）：「现在有两个店铺 BEL 和 BHY 都需要此操作，以及中途可能出现的弹窗问题，
你要增加入口以及增加路线」。当天 B机 真跑两次 `fill` 都失败：
    · BEL：`本批 64 单，列表读到 0 行`
    · BHY：`订单号筛选框填不进去：no-input`
两者**都不是**"这些单不存在"，旧代码却给同一句"先人工确认"——分不清该去授权还是核单号。
本文件把"入口"和"路线"钉死：
    ① **入口**：BEL/BHY 两店都在店映射里（窗口名 + storeId），且都能按店覆盖口径
    ② **路线**：`diagnose_missing_orders()` 按证据分三条（auth_required / page_not_ready / orders_missing）
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)                    # scripts/power
_ROOT = os.path.dirname(os.path.dirname(_POWER))
sys.path.insert(0, os.path.join(_POWER, "lib"))

import temu_buylabel_driver as D                  # noqa: E402

MAP = os.path.join(_POWER, "data", "ziniao_power_map.json")
CFG = os.path.join(_POWER, "data", "temu_buylabel.json")


def _stores():
    m = json.load(open(MAP, encoding="utf-8"))
    out = {}
    for e in (m.get("mapping") or []):
        if str(e.get("power_platform")) == "TEMU":
            out[str(e.get("export_suffix"))] = e
    return out


# ---------------------------------------------------------------- ① 入口：两店都要在册
def test_both_stores_registered_with_window_and_id():
    """BEL 与 BHY 都必须有：店后缀 → 紫鸟窗口名 + storeId（否则通道根本开不了窗）。"""
    s = _stores()
    for name in ("BEL", "BHY"):
        assert name in s, "店映射里缺 %s（入口不完整）" % name
        assert s[name].get("ziniao_window_name"), "%s 缺窗口名" % name
        assert s[name].get("ziniao_window_store_id"), "%s 缺 storeId" % name


def test_store_entries_are_named_tm_xxx_yjf():
    """窗口名形态固定为 `TM-<店>-YJF`（现取，不写死端口/窗口名到代码里）。"""
    s = _stores()
    for name in ("BEL", "BHY"):
        assert str(s[name]["ziniao_window_name"]).startswith("TM-%s" % name), \
            "%s 的窗口名不合形态：%s" % (name, s[name]["ziniao_window_name"])


# ---------------------------------------------------------------- ② 按店覆盖口径（纯函数）
def test_apply_store_overrides_per_store():
    cfg = json.load(open(CFG, encoding="utf-8"))
    c_bel, ap_bel = D.apply_store_overrides(cfg, "BEL")
    assert "_store" in c_bel and c_bel["_store"] == "BEL"
    assert ap_bel, "BEL 应有分店覆盖项（至少 warehouse_default / no_carrier_fallback）"
    c_bhy, ap_bhy = D.apply_store_overrides(cfg, "BHY")
    assert c_bhy["_store"] == "BHY" and ap_bhy, "BHY 也应有显式登记"
    # 不改动入参（纯函数）
    assert "_store" not in cfg, "apply_store_overrides 污染了入参 cfg"
    # 空 store → 用全局口径
    c0, ap0 = D.apply_store_overrides(cfg, "")
    assert ap0 == [] and "_store" not in c0


def test_store_override_values_are_warehouse_safe():
    """分店覆盖里的仓库值必须能过 `warehouse_map`（否则会走默认仓，静默选错仓）。"""
    cfg = json.load(open(CFG, encoding="utf-8"))
    wm = cfg.get("warehouse_map") or {}
    for store, over in (cfg.get("stores") or {}).items():
        if str(store).startswith("_"):
            continue
        for key in ("warehouse_default",):
            v = over.get(key)
            if v:
                assert v in wm.values() or v in wm, \
                    "%s.%s=%r 不在 warehouse_map（值域）里" % (store, key, v)
        fb = over.get("no_carrier_fallback") or {}
        if fb.get("warehouse"):
            assert fb["warehouse"] in wm.values() or fb["warehouse"] in wm, \
                "%s.no_carrier_fallback.warehouse=%r 不在 warehouse_map 值域里" % (store, fb["warehouse"])


# ---------------------------------------------------------------- ③ 路线：按证据分诊
def test_route_auth_required_names_the_store():
    """函数级行为：给它的 URL 列表里只要出现认证页就判 auth_required。

    ⚠ 契约（2026-09-17 TB-041）：**调用方只许把「读列表那一页」的 URL 传进来**。
    实测踩过：把窗口里所有 tab 的 URL 都传进来时，一个残留的 `settle/seller-login` tab
    会让「这些单不在待发货」被误诊成「登录态失效」，把人骗去反复重新授权。
    """
    urls = ["https://agentseller-us.temu.com/mmsos/orders.html",
            "https://seller.kuajingmaihuo.com/auth/authentication?redirectUrl=x"]
    d = D.diagnose_missing_orders(urls, 0, 64, "BEL")
    assert d["route"] == D.ROUTE_AUTH_REQUIRED
    assert "run_temu_auth.py --store BEL" in d["hint"], "授权提示必须点名店后缀：%s" % d["hint"]


def test_searched_page_only_evidence_orders_missing_not_auth():
    """TB-041 回归：读列表那一页是正常订单页 + 筛选已应用 + 0 行 ⇒ 这些单不在待发货（不是登录问题）。"""
    orders = "https://agentseller-us.temu.com/mmsos/orders.html?parentOrderSnList=PO-..."
    d = D.diagnose_missing_orders([orders], 0, 27, "BEL", query_applied=True)
    assert d["route"] == D.ROUTE_ORDERS_MISSING, d
    assert "不在「待发货」" in d["hint"], d["hint"]


def test_real_auth_bounce_still_detected():
    """反过来也要成立：真被弹到认证页（page.url 就是认证页）仍必须判 auth_required。"""
    auth = "https://agentseller-us.temu.com/auth/authentication?redirectUrl=...mmsos%2Forders.html"
    d = D.diagnose_missing_orders([auth], 0, 27, "BEL", query_applied=True)
    assert d["route"] == D.ROUTE_AUTH_REQUIRED, d


def test_zero_rows_without_query_is_page_not_ready():
    d = D.diagnose_missing_orders(["https://agentseller-us.temu.com/mmsos/orders.html"], 0, 64, "BHY")
    assert d["route"] == D.ROUTE_PAGE_NOT_READY, d


def test_route_orders_missing_when_list_nonempty():
    d = D.diagnose_missing_orders(["https://agentseller-us.temu.com/mmsos/orders.html"], 20, 64, "BHY")
    assert d["route"] == D.ROUTE_ORDERS_MISSING, d


def test_auth_hint_empty_when_no_auth_page():
    assert D.auth_lost_hint_from_urls(["https://agentseller-us.temu.com/mmsos/orders.html"]) == ""
    assert "run_temu_auth.py" in D.auth_lost_hint_from_urls(["auth/authentication"], "BHY")
