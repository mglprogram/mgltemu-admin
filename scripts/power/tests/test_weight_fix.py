# -*- coding: utf-8 -*-
"""weight_fix 的离线判据测试（2026-10-09 用户口径落地）。

来历：用户 2026-10-09 明确「填充完重量之后，先找到出现重量不匹配的订单，修改完之后，
再去填充物流」。判据＝多件单重量必须 = 基准单件重 × 件数（行内 ×N 之和）。

纯离线：不连浏览器，用假 driver 替掉 _js/shot/log，只验 `weight_fix()` 的判定与动作分支。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import temu_buylabel_driver as tbd  # noqa: E402


class FakeDrv(tbd.BuylabelDriver):
    """把页面交互换成可控假数据；`as_str` 模拟 CDP 原样返回 JSON 字符串的真实形态。"""

    def __init__(self, rows_seq, cfg=None, set_result="ok:2.60"):
        self.cfg = cfg or {"weight_per_unit_lb": 1.3, "weight_decimals": 2,
                           "weight_mismatch": {"enabled": True}}
        self._rows_seq = list(rows_seq)
        self._set_result = set_result
        self.as_str = True
        self.set_calls = []
        self.shots = []
        self.logs = []
        self._last = None

    def _js(self, page, js, *a, **kw):
        if js is tbd._JS_ROWS_QTY:
            v = self._rows_seq.pop(0) if self._rows_seq else (
                self._last if self._last is not None else [])
            self._last = v
            return json.dumps(v) if self.as_str else v
        if js is tbd._JS_SET_ROW_WEIGHT:
            arg = a[0] if a and isinstance(a[0], (list, tuple)) else None
            self.set_calls.append(tuple(arg) if arg else a)
            if arg and len(arg) == 2:
                ord_, val = arg
                for r in (self._last or []):
                    if isinstance(r, dict) and r.get("ord") == ord_:
                        r["weights"] = [val] * max(1, len(r.get("weights") or [1]))
            return self._set_result
        return None

    def shot(self, page, name):
        self.shots.append(name)
        return name

    def log(self, m):
        self.logs.append(str(m))


def mk(ord_, qty, weight):
    return {"ord": ord_, "qty": qty, "weights": [weight], "mismatchHint": False}


def mk_red(ord_, qty, weight):
    """行内**已报红字**（「差异过大」）的行——用于验「1 件也超标 → 固定值」那条规则。"""
    return {"ord": ord_, "qty": qty, "weights": [weight], "mismatchHint": True}


def test_multi_qty_mismatch_is_fixed():
    """① 多件单重量没翻倍 → 改对，并把目标值算成 基准×件数。"""
    d = FakeDrv([[mk("PO-1", 1, "1.30"), mk("PO-2", 2, "1.30")],
                 [mk("PO-1", 1, "1.30"), mk("PO-2", 2, "2.60")]])
    out = d.weight_fix(object())
    assert out["mismatched"] == 1 and out["fixed"] == 1
    assert d.set_calls == [("PO-2", "2.60")]


def test_all_correct_touches_nothing():
    """② 全部正确 → 一个输入框都不碰。"""
    d = FakeDrv([[mk("PO-1", 1, "1.30"), mk("PO-2", 2, "2.60")]])
    out = d.weight_fix(object())
    assert out["mismatched"] == 0 and d.set_calls == []


def test_three_qty_is_tripled():
    """③ 三件单 → 基准×3。"""
    d = FakeDrv([[mk("PO-B", 3, "1.30")], [mk("PO-B", 3, "3.90")]])
    out = d.weight_fix(object())
    assert d.set_calls == [("PO-B", "3.90")] and out["fixed"] == 1


def test_disabled_skips():
    """④ enabled=false → 整段跳过，不做任何写操作。"""
    d = FakeDrv([[mk("PO-2", 2, "1.30")]],
                cfg={"weight_per_unit_lb": 1.3, "weight_decimals": 2,
                     "weight_mismatch": {"enabled": False}})
    out = d.weight_fix(object())
    assert out.get("skipped") is True and d.set_calls == []


def test_empty_rows_marks_read_empty():
    """⑤ 一行都读不到 → 不崩、如实标记（而不是静默当作"全对"）。"""
    d = FakeDrv([[]])
    out = d.weight_fix(object())
    assert out.get("read_empty") is True and out["checked"] == 0


def test_unfixed_default_does_not_raise_but_logs():
    """⑥ 用户口径：默认**不加闸门**——改不动也继续，但必须留痕（不静默吞）。"""
    d = FakeDrv([[mk("PO-2", 2, "1.30")], [mk("PO-2", 2, "1.30")]], set_result="no-group")
    out = d.weight_fix(object())
    assert out["unfixed"] and out["unfixed"][0]["ord"] == "PO-2"
    assert any("未改对" in x for x in d.logs)


def test_stop_on_unfixed_raises():
    """⑦ 显式打开闸门时才 HARD STOP。"""
    d = FakeDrv([[mk("PO-2", 2, "1.30")], [mk("PO-2", 2, "1.30")]], set_result="no-group")
    try:
        d.weight_fix(object(), stop_on_unfixed=True)
    except RuntimeError:
        return
    raise AssertionError("stop_on_unfixed=True 时应抛错")


def test_weight_per_unit_override():
    """⑧ --weight-per-unit 覆盖基准（本次调用不改口径文件）。"""
    d = FakeDrv([[mk("PO-2", 2, "1.30")], [mk("PO-2", 2, "2.20")]])
    d.weight_fix(object(), weight_per_unit=1.1)
    assert d.set_calls == [("PO-2", "2.20")]


def test_native_list_payload():
    """⑨ CDP 也可能原样回 list（非字符串）——两种形态都要能吃。"""
    d = FakeDrv([[mk("PO-2", 2, "1.30")], [mk("PO-2", 2, "2.60")]])
    d.as_str = False
    out = d.weight_fix(object())
    assert out["mismatched"] == 1 and d.set_calls == [("PO-2", "2.60")]


def test_non_numeric_weight_recorded_not_guessed():
    """⑩ 重量值非数字 → 记账不猜值、不崩。"""
    d = FakeDrv([[{"ord": "PO-X", "qty": 2, "weights": ["abc"]}]])
    out = d.weight_fix(object())
    assert out["unfixed"] and "非数字" in out["unfixed"][0]["err"]


# ---- ★ 2026-10-09 核心修复：件数一律以「派送表数量」(plan) 为准，不信页面 ×N ----
# 实证：BEL 94 单里 25 单页面显示 ×1，而表格数量是 2 或 3。
# 例 PO-211-07859559854712400：表=2（应 2.20），页面=×1 —— 旧逻辑按 ×1 把它改成 1.10 ⇒ 平台报「差异过大」。
# 下面用**生产口径 cfg（基准 1.1）**，与 data/temu_buylabel.json 一致。
CFG_11 = {"weight_per_unit_lb": 1.1, "weight_decimals": 2,
          "weight_mismatch": {"enabled": True}}


def test_plan_qty_wins_over_page_qty():
    """⑪ plan 数量=2、页面 ×1 → 必须按 2 算（2.20），不能按页面 ×1 算成 1.10。"""
    d = FakeDrv([[mk("PO-A", 1, "1.10")], [mk("PO-A", 1, "2.20")]], cfg=dict(CFG_11))
    plan = [{"order_no": "PO-A", "qty": "2"}]
    out = d.weight_fix(object(), plan=plan)
    assert d.set_calls == [("PO-A", "2.20")], d.set_calls
    assert out["mismatched"] == 1 and out["fixed"] == 1


def test_plan_qty_three():
    """⑫ plan 数量=3、页面 ×1 → 3.30。"""
    d = FakeDrv([[mk("PO-B", 1, "1.10")], [mk("PO-B", 1, "3.30")]], cfg=dict(CFG_11))
    d.weight_fix(object(), plan=[{"order_no": "PO-B", "qty": "3"}])
    assert d.set_calls == [("PO-B", "3.30")]


def test_page_qty_used_only_when_absent_from_plan():
    """⑬ 表里没有该单时才回退用页面 ×N（兜底路径仍可用）。"""
    d = FakeDrv([[mk("PO-C", 2, "1.10")], [mk("PO-C", 2, "2.20")]], cfg=dict(CFG_11))
    d.weight_fix(object(), plan=[{"order_no": "PO-OTHER", "qty": "1"}])
    assert d.set_calls == [("PO-C", "2.20")]     # 用页面 ×2


def test_plan_qty_matches_page_no_write():
    """⑭ 表与页面一致且重量已对 → 不写任何输入框。"""
    d = FakeDrv([[mk("PO-D", 2, "2.20")]], cfg=dict(CFG_11))
    out = d.weight_fix(object(), plan=[{"order_no": "PO-D", "qty": "2"}])
    assert out["mismatched"] == 0 and d.set_calls == []


# ---- ★ 2026-10-09 第二条规则（用户口径）：件数=1 但仍报「差异过大」→ 固定值 1.4 ----
# 场景：1 件单其实是大件/多件套（例 BH-102503-5SetA-L 是 5件套，表数量 1、计费重量 1.80），
# 按 基准×1=1.10 填会被平台判「差异过大（2.06 磅）」——此时件数倍数公式不适用。
CFG_11_OVR = dict(CFG_11, weight_single_override={
    "enabled": True, "when_qty": 1, "value": 1.4})


def test_single_qty_with_red_hint_uses_1_4():
    """⑮ 件数=1 且已报红字 → 重量改成固定 1.4（而不是 基准×1=1.10）。"""
    d = FakeDrv([[mk_red("PO-S", 1, "1.10")], [mk_red("PO-S", 1, "1.40")]],
                cfg=dict(CFG_11_OVR))
    out = d.weight_fix(object(), plan=[{"order_no": "PO-S", "qty": "1"}])
    assert d.set_calls == [("PO-S", "1.40")], d.set_calls
    assert out["fixed"] == 1


def test_single_qty_without_red_hint_keeps_basis():
    """⑯ 件数=1 且**没有**红字 → 维持 基准×1（1.10），不误改 1.4。"""
    d = FakeDrv([[mk("PO-T", 1, "1.10")]], cfg=dict(CFG_11_OVR))
    out = d.weight_fix(object(), plan=[{"order_no": "PO-T", "qty": "1"}])
    assert out["mismatched"] == 0 and d.set_calls == []


def test_multi_qty_with_red_hint_still_multiplies():
    """⑰ 件数>1 且报红字 → 仍走 基准×件数，**不**套用 1.4。"""
    d = FakeDrv([[mk_red("PO-U", 2, "1.10")], [mk_red("PO-U", 2, "2.20")]],
                cfg=dict(CFG_11_OVR))
    d.weight_fix(object(), plan=[{"order_no": "PO-U", "qty": "2"}])
    assert d.set_calls == [("PO-U", "2.20")], d.set_calls


def test_single_override_disabled_falls_back():
    """⑱ 规则可被 enabled=false 关掉 → 回到 基准×1。"""
    cfg = dict(CFG_11, weight_single_override={
        "enabled": False, "when_qty": 1, "value": 1.4})
    d = FakeDrv([[mk_red("PO-V", 1, "1.10")]], cfg=cfg)
    out = d.weight_fix(object(), plan=[{"order_no": "PO-V", "qty": "1"}])
    assert out["mismatched"] == 0 and d.set_calls == []
