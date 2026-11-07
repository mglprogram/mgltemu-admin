# -*- coding: utf-8 -*-
"""test_pw_fail_fix.py — 「帕沃尔下单失败自动修复」的方案判定测试（纯函数，不联网）。

钉住的都是**会真的下错单/发错货**的地方：
  · 同仓换 Fnsku 必须排除订单当前那个（换回同一个必然再失败）
  · 原仓没货时允许**跨仓**：先改仓、再换成那个仓的 Fnsku、再下单
  · 同一个 Fnsku 不能被两单同时分走（超分 ⇒ 第二单必然再失败）
  · 状态不是「下单失败/未下单」的一律不碰（`已下单` 再下单＝重复下单）
  · `--max-fix` / `--max-per-sku` 截断时要**改成 MANUAL 并写明原因**，不许静默丢单
  · pipeline 里这步必须在 `pw_receive` 之后、`pw_track` 之前（用户 2026-11-06 口径）
"""
import inspect
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
sys.path.insert(0, _POWER)

import pw_fail_fix as X  # noqa: E402
import run_temu_fulfill as F  # noqa: E402


def _row(fn, wh, avail, age="100"):
    return {"fnsku": fn, "seller": "S", "wh": wh, "avail": str(avail), "avail2": "0",
            "age": age, "in_at": ""}


def _order(po="PO-1", wh="加州3仓", status="下单失败", cur="FNOLD00001", sku="BEL-100-L"):
    return {"po": po, "id": "1", "status": status, "sku": sku, "seller_sku": sku, "wh": wh,
            "err": "-sku:%s库存不足发货" % cur if cur else ""}


# ---------------------------------------------------------------- 仓名归一 / 取当前 Fnsku


def test_norm_wh_covers_both_spellings():
    """库存写「加州3仓」、BHY 订单页写「加州三仓」；kansas 有两种写法 —— 都要归到同一个。"""
    assert X.norm_wh("加州3仓") == X.norm_wh("加州三仓") == "加州3仓"
    assert X.norm_wh("kansas仓") == X.norm_wh("Kansas") == "kansas仓"
    assert X.norm_wh("") == ""


def test_detect_cur_fn_from_error():
    assert X.detect_cur_fn("下单失败", "PO-x下单失败-sku:D14450IAA5H库存不足发货") == "D14450IAA5H"
    assert X.detect_cur_fn("下单失败", "网络异常，请稍后重试") == ""


# ---------------------------------------------------------------- 方案判定


def test_same_warehouse_picks_in_stock_different_fnsku():
    """同仓有代发库存、Fnsku 不同 ⇒ SAME_WH，且**不跨仓**。"""
    rows = [_row("FNOLD00001", "加州3仓", 0), _row("FNNEW00002", "加州3仓", 5)]
    p = X.plan_row(_order(), rows, {})
    assert p["plan"] == "SAME_WH", p
    assert p["fnsku"] == "FNNEW00002"
    assert p["to_wh"] == "加州3仓"


def test_same_warehouse_never_picks_current_fnsku():
    """该仓唯一有货的就是订单当前那个 Fnsku ⇒ 换它等于没换，必须判 MANUAL。"""
    rows = [_row("FNOLD00001", "加州3仓", 9)]
    p = X.plan_row(_order(), rows, {})
    assert p["plan"] == "MANUAL", p
    assert "换不了" in p["why"]


def test_cross_warehouse_when_original_has_nothing():
    """★ 用户口径：原仓没货、另一个仓有 ⇒ 先改仓再换那个仓的 Fnsku 再下单。"""
    rows = [_row("FNOLD00001", "加州3仓", 0), _row("FNKAN00003", "kansas仓", 6, age="88")]
    p = X.plan_row(_order(), rows, {})
    assert p["plan"] == "CROSS_WH", p
    assert p["to_wh"] == "kansas仓"
    assert p["fnsku"] == "FNKAN00003"


def test_same_warehouse_wins_over_cross_warehouse():
    """原仓也有货时不要跨仓（少动一次仓，少一个出错点）。"""
    rows = [_row("FNNEW00002", "加州3仓", 3, age="10"),
            _row("FNKAN00003", "kansas仓", 9, age="300")]
    p = X.plan_row(_order(), rows, {})
    assert p["plan"] == "SAME_WH" and p["to_wh"] == "加州3仓", p


def test_higher_age_preferred():
    """用户口径：库龄天数最高优先（先进先出），不是库存最多优先。"""
    rows = [_row("FNA0000001", "加州3仓", 99, age="5"), _row("FNB0000002", "加州3仓", 2, age="300")]
    assert X.plan_row(_order(), rows, {})["fnsku"] == "FNB0000002"


def test_no_stock_anywhere_is_manual():
    p = X.plan_row(_order(), [_row("FNOLD00001", "加州3仓", 0), _row("FNKAN00003", "kansas仓", 0)], {})
    assert p["plan"] == "MANUAL"
    assert p["fnsku"] == ""


def test_already_placed_orders_are_never_touched():
    """`已下单` 的单再「下单代发」＝重复下单，方案必须是 MANUAL。"""
    rows = [_row("FNNEW00002", "加州3仓", 5)]
    p = X.plan_row(_order(status="已下单"), rows, {})
    assert p["plan"] == "MANUAL"
    assert "不是下单失败" in p["why"]


def test_order_without_warehouse_is_manual():
    p = X.plan_row(_order(wh=""), [_row("FNNEW00002", "加州3仓", 5)], {})
    assert p["plan"] == "MANUAL" and "发货仓" in p["why"]


def test_unknown_seller_sku_is_manual():
    p = X.plan_row(_order(), [], {})
    assert p["plan"] == "MANUAL" and "查无" in p["why"]


# ---------------------------------------------------------------- 批量：共享余量 / 截断


def test_shared_stock_across_orders_same_sku():
    """同一 Fnsku 只剩 1 件时，第二单必须改判（不许两单都指到它）。"""
    rows = [_row("FNA0000001", "加州3仓", 1)]
    orders = [_order(po="PO-1"), _order(po="PO-2")]
    plan = X.build_plan(orders, {"BEL-100-L": rows})
    assert plan[0]["plan"] == "SAME_WH"
    assert plan[1]["plan"] in ("CROSS_WH", "MANUAL"), plan[1]
    assert plan[1]["fnsku"] != plan[0]["fnsku"] or plan[1]["plan"] == "MANUAL"


def test_shared_stock_falls_back_to_second_fnsku():
    rows = [_row("FNA0000001", "加州3仓", 1, age="300"),
            _row("FNB0000002", "加州3仓", 5, age="100")]
    plan = X.build_plan([_order(po="PO-1"), _order(po="PO-2")], {"BEL-100-L": rows})
    assert [p["fnsku"] for p in plan] == ["FNA0000001", "FNB0000002"]


def test_cap_plan_marks_manual_and_says_why():
    plan = [{"po": "PO-%d" % i, "plan": "SAME_WH", "fnsku": "FN", "why": ""} for i in range(3)]
    out = X.cap_plan(plan, 1)
    assert out[0]["plan"] == "SAME_WH"
    assert out[1]["plan"] == out[2]["plan"] == "MANUAL"
    assert "上限" in out[1]["why"], "截断必须写明原因，不许静默丢单"


def test_max_per_sku_limit():
    rows = [_row("FNA0000001", "加州3仓", 50), _row("FNB0000002", "加州3仓", 50)]
    plan = X.build_plan([_order(po="PO-1"), _order(po="PO-2")], {"BEL-100-L": rows},
                        per_sku_limit=1)
    assert plan[0]["plan"] == "SAME_WH"
    assert plan[1]["plan"] == "MANUAL" and "上限" in plan[1]["why"]


def test_summarize_counts():
    plan = [{"plan": "SAME_WH"}, {"plan": "CROSS_WH"}, {"plan": "MANUAL"}]
    s = X.summarize(plan, ["PO-1", "PO-2"])
    assert s == {"total": 3, "same_wh": 1, "cross_wh": 1, "manual": 1,
                 "fixed_ok": 2, "left": 0}, s


# ---------------------------------------------------------------- 安全：只在本次帕沃尔的单上动手


def test_only_pw_group_orders_are_touched():
    """★ 恒信那份在帕沃尔页面必然「未下单」；代码必须按 groups.PW 收窄。"""
    src = inspect.getsource(X.main)
    assert '"PW"' in src, "必须只取 groups.PW"
    assert "FIXABLE" in src


def test_cross_wh_refuses_when_warehouse_readback_mismatch():
    """改仓后回读不对就必须停（不能按旧缺货仓下单）—— 纯代码判据。"""
    src = inspect.getsource(X.apply_one)
    assert "readback_wh" in src
    assert "不改 Fnsku" in src or "→" in src


# ---------------------------------------------------------------- pipeline 顺序


def _pipeline():
    return json.load(open(os.path.join(_POWER, "data", "temu_fulfill_pipeline.json"),
                          encoding="utf-8"))


def test_pipeline_has_fail_steps_between_receive_and_track():
    """★ 2026-11-07 用户定稿：hwc_shop **下单代发** → 修失败单并重下 → 确认订单 → 取号。"""
    names = [s["name"] for s in _pipeline()["steps"]]
    i_hwc = names.index("hwc_order")
    i_scan = names.index("pw_fail_scan")
    i_fix = names.index("pw_fail_fix")
    i_recv = names.index("pw_receive")
    i_track = names.index("pw_track")
    assert i_hwc < i_scan < i_fix < i_recv < i_track, names


def test_fail_steps_have_impl_and_gates():
    names = [s["name"] for s in _pipeline()["steps"]]
    for n in ("pw_fail_scan", "pw_fail_fix"):
        assert n in F.STEPS, "%s 没有实现" % n
        assert n in names
    # 扫描只读；修复是写操作（没 --confirm 必须跳过，绝不假装跑过）
    assert F.gate_write("pw_fail_scan", confirm=False) == "run"
    assert F.gate_write("pw_fail_fix", confirm=False) == "skip"
    assert "pw_fail_fix" in F.WRITE_STEPS


def test_fail_fix_is_soft_and_delegated():
    """修不动的单不能拖死整链（会进 report 的失败 sheet）；且它跑在帕沃尔窗口 ⇒ 派子代理。"""
    assert "pw_fail_fix" in F.SOFT_STEPS
    assert "pw_fail_fix" in F.DELEGATE_STEPS


def test_fail_round_supported_by_entry():
    """同一天跑第二轮要能换产物名，不能覆盖第一轮（否则查不到改了哪些单）。"""
    src = inspect.getsource(F.main)
    assert "--fail-round" in src
    assert "fail_round" in src
