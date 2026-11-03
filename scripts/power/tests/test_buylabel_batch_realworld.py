# -*- coding: utf-8 -*-
"""批量确认发货的「真机约束」回归测试（2026-11-03 实跑踩到的两个坑）。

两个坑都是"看着成功、其实没发"那一类，必须钉死：

① **原生 `input.click()` 对行内复选框无效**
   同一页面实测：
     · 点表头 checkbox            → checked 14 → 0     ✅ 有效
     · 点行内 input[type=checkbox] → checked 仍是 0     ❌ 无效（React 受控组件）
   ⇒ 「取消全选 → 逐单勾」的结果是 0 行被勾上，提交按钮一直显示「确认发货 0」且 disabled。
   修法：点 checkbox 的**外层 label / 可点容器**，并且**回读 selected 才认**。

② **提交按钮会有瞬时 disabled**
   取消全选→重新勾选后，React 按新勾选数重算，按钮 1~2 秒是 disabled。
   旧写法 `if (e.disabled) return;` 直接跳过 → `no-submit`，14 单勾好了却没发出去。
   ⇒ 匹配到就点 + 重试 ≤4 次，并把 `clicked-disabled` 如实回报。

跑：python -m pytest scripts/power/tests/test_buylabel_batch_confirm.py -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
for _p in (os.path.join(_POWER, "lib"), _POWER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import temu_buylabel_driver as d  # noqa: E402


# ---------------------------------------------------------------- 坑① 复选框
def test_set_checkbox_clicks_label_not_the_raw_input():
    """必须点外层 label/容器，不能只点原生 input（React 不认）。"""
    js = d._JS_SET_CHECKBOX_BY_PO
    assert "closest('label')" in js, "要先找 label"
    assert "CBX|checkbox|Checkbox" in js, "找不到 label 时要退到勾选框的包裹元素"
    assert "el.click()" in js, "点的是包裹元素，不是 cb.click()"


def test_set_checkbox_does_not_click_raw_input_directly():
    """禁止退回 `cb.click()` —— 那正是实测无效的写法。"""
    js = d._JS_SET_CHECKBOX_BY_PO
    assert "cb.click()" not in js, "原生 input.click() 对 React 受控 checkbox 无效（实测）"


def test_check_result_is_read_back_not_trusted():
    """`batch_check` 必须**回读** selected 才算数，不能信 click 的返回值。"""
    drv = d.BuylabelDriver.__new__(d.BuylabelDriver)
    drv.log = lambda m: None
    drv._js = lambda *a, **kw: '{"changed": 3}'        # 声称改了 3 行
    # 但回读只有 1 行真的勾上
    drv.batch_checked = lambda page: {"selected": ["PO-211-A"], "checked": 1}
    n = drv.batch_check(object(), ["PO-211-A", "PO-211-B", "PO-211-C"], True)
    assert n == 1, "返回的必须是**回读确认**的数量，不是 click 声称的数量"


def test_check_reads_back_for_uncheck_too():
    drv = d.BuylabelDriver.__new__(d.BuylabelDriver)
    drv.log = lambda m: None
    drv._js = lambda *a, **kw: '{"changed": 2}'
    drv.batch_checked = lambda page: {"selected": ["PO-211-B"], "checked": 1}
    n = drv.batch_check(object(), ["PO-211-A", "PO-211-B"], False)
    assert n == 1, "取消勾选也要回读：A 已取消、B 仍勾着 ⇒ 只成功 1 个"


# ---------------------------------------------------------------- 坑② 提交按钮
def test_submit_js_returns_disabled_state_instead_of_skipping():
    """匹配到就点，并把 disabled 状态带回来 —— 不能因为 disabled 就静默返回 no-submit。"""
    js = d._JS_CLICK_BATCH_SUBMIT
    assert "clicked-disabled" in js, "要把「当时是 disabled」如实回报"
    assert "if (e.disabled) return;" not in js, "不能因为 disabled 就跳过（实测会一直 disabled 几秒）"


def test_submit_js_diagnoses_when_not_found():
    """找不到按钮时要把抽屉里的按钮文案带回来（否则只知道"没找到"）。"""
    js = d._JS_CLICK_BATCH_SUBMIT
    assert "no-submit:" in js, "没匹配到时要带诊断信息"


def test_batch_submit_retries_through_disabled():
    """disabled 时应当重试而不是立刻放弃；重试后成功要算成功。"""
    drv = d.BuylabelDriver.__new__(d.BuylabelDriver)
    drv.log = lambda m: None
    drv.shot = lambda *a, **kw: None
    states = [{"open": True}, {"open": True}, {"open": False}]
    drv.read_batch_drawer = lambda page: states.pop(0) if states else {"open": False}
    seq = ["clicked-disabled", "ok"]
    drv._js = lambda page, script, *a, **kw: (
        seq.pop(0) if seq else "ok")

    def _sleep(_s):
        return None

    d.time.sleep = _sleep
    try:
        r = drv.batch_submit(object())
    finally:
        pass
    assert r == "ok", "第二次点击成功 + 抽屉关闭 ⇒ 应判 ok"


def test_batch_submit_gives_up_after_retries():
    """一直 disabled 就如实失败 —— 绝不假装成功。"""
    drv = d.BuylabelDriver.__new__(d.BuylabelDriver)
    drv.log = lambda m: None
    drv.shot = lambda *a, **kw: None
    drv.read_batch_drawer = lambda page: {"open": True}
    drv._js = lambda *a, **kw: "clicked-disabled"
    d.time.sleep = lambda _s: None
    r = drv.batch_submit(object())
    assert r == "clicked-disabled", "重试用尽要返回真实原因"
    assert r != "ok"


# ---------------------------------------------------------------- 端到端顺序
def test_confirm_order_is_uncheck_all_then_check_then_submit():
    """顺序硬约束：**先取消全选**（抽屉默认全选）→ 再勾通过的 → 才提交。

    顺序反了（先勾再取消全选）会把刚勾的也清掉 ⇒ 发 0 单。
    """
    drv = d.BuylabelDriver.__new__(d.BuylabelDriver)
    seq = []
    drv.log = lambda m: None
    drv.shot = lambda *a, **kw: None
    drv.cfg = {"pending_confirm": {"tab": "待发货"}}
    drv._orders_page_ready = lambda: object()
    drv._js = lambda page, script, *a, **kw: "ok"
    rows = [{"pk": "PK-1", "po": "PO-211-A", "checked": True, "hasCb": True}]
    drv.open_batch_drawer = lambda page, **kw: {
        "open": True, "page": 1, "pages": 1, "rows": rows, "allChecked": True}
    drv.read_batch_drawer = lambda page: {
        "open": True, "page": 1, "pages": 1, "rows": rows, "allChecked": True}
    drv.batch_uncheck_all = lambda page: seq.append("uncheck") or "ok"
    drv.batch_check = lambda page, pos, want: seq.append("check") or len(list(pos))
    drv.batch_submit = lambda *a, **kw: seq.append("submit") or "ok"
    drv.close_batch_drawer = lambda page: None
    out = drv.batch_confirm_orders(["PO-211-A"])
    assert seq[:3] == ["uncheck", "check", "submit"], \
        "顺序必须是 取消全选 → 勾选 → 提交，收到：%r" % (seq,)
    assert out["submitted"] == ["PO-211-A"]
