# -*- coding: utf-8 -*-
"""批量确认发货（2026-11-03 用户新口径）的离线单测。

用户口径原文：
    「拉到一页 500 条记录（可能存在不止 500 条记录，可能存在第二页）
      -> 点击批量确认发货，弹出抽屉
      -> 然后去 LM739 校验（现阶段只有一个账号校验）
      -> 校验完成去点击确认发货，抽屉可以存在多页，出现多页需要翻页去确认」

为什么换掉逐单点「去手动确认」：
    逐单点每单 3 次 JS + 等按钮消失，实测 400+ 单要 118 分钟，CDP 每 ~100 单崩一次。
    抽屉是平台自己的批量入口，默认全选，按 PO 勾选后一次提交。

真机结构（2026-11-03 BHY 实测）：
    · 入口 `<button>批量确认发货 14</button>` —— 数字会变，**不能全等匹配**
    · 抽屉 `[class*=Drawer_content]`，每行 = 一个包裹 `PK-…` + 关联运单 + 订单号 `PO-211-…`
      ⇒ **一单可能多包**（拆包），勾选要按 PO 找行
    · 表头 checkbox 是**全选**且默认已勾 ⇒ 只发一部分必须先取消全选
    · 底部 `确认发货 14` + `取消`；分页器 `[class*=pager]`，页码文本 "1"/"2"

跑：python -m pytest scripts/power/tests/test_buylabel_batch_confirm.py -q
"""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
for _p in (os.path.join(_POWER, "lib"), _POWER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import temu_buylabel_driver as d  # noqa: E402


def _row(pk, po, checked=True, has_cb=True):
    return {"pk": pk, "po": po, "checked": checked, "hasCb": has_cb}


def _drawer(rows, page=1, pages=1, all_checked=True):
    return {"open": True, "page": page, "pages": pages, "rows": rows,
            "footer": "确认发货 %d" % len(rows), "allChecked": all_checked}


# ---------------------------------------------------------------- 计划（纯函数）
def test_plan_checks_only_approved_and_unchecks_the_rest():
    """抽屉默认**全选**；只发校验通过的那几单，其余必须显式取消。"""
    dr = _drawer([_row("PK-1", "PO-211-A"), _row("PK-2", "PO-211-B"),
                  _row("PK-3", "PO-211-C")])
    plan = d.batch_drawer_plan(dr, ["PO-211-A"])
    assert plan["check"] == ["PO-211-A"]
    assert set(plan["uncheck"]) == {"PO-211-B", "PO-211-C"}
    assert plan["should_submit"] is True


def test_plan_handles_multi_package_rows():
    """一单拆多包 ⇒ 该 PO 的**所有** PK 行都要算进 pk_count，不能只算 1。"""
    dr = _drawer([_row("PK-1", "PO-211-A"), _row("PK-2", "PO-211-A"),
                  _row("PK-3", "PO-211-B")])
    plan = d.batch_drawer_plan(dr, ["PO-211-A"])
    assert plan["check"] == ["PO-211-A"], "同一个 PO 只应出现一次"
    assert plan["pk_count"] == 2, "拆包时两行都要勾"


def test_plan_does_not_submit_when_nothing_approved():
    """本页没有校验通过的单 ⇒ **绝不能提交**（提交等于发 0 单，还占一次操作）。"""
    dr = _drawer([_row("PK-1", "PO-211-A")])
    plan = d.batch_drawer_plan(dr, ["PO-211-OTHER"])
    assert plan["should_submit"] is False
    assert plan["check"] == []
    assert plan["uncheck"] == ["PO-211-A"]


def test_plan_with_empty_approval_list():
    """校验一个都没通过（空清单）⇒ 同样不提交。"""
    assert d.batch_drawer_plan(_drawer([_row("PK-1", "PO-211-A")]), [])["should_submit"] is False
    assert d.batch_drawer_plan(_drawer([_row("PK-1", "PO-211-A")]), None)["should_submit"] is False


def test_plan_reports_unknown_orders():
    """抽屉里有但没通过校验的 PO 要如实报出来（这些是待处理/异常，需人工）。"""
    dr = _drawer([_row("PK-1", "PO-211-A"), _row("PK-2", "PO-211-B")])
    plan = d.batch_drawer_plan(dr, ["PO-211-A"])
    assert plan["unknown"] == ["PO-211-B"]


def test_plan_ignores_rows_without_checkbox():
    """没有 checkbox 的行（例如提示行）不算可勾。"""
    dr = _drawer([_row("PK-1", "PO-211-A", has_cb=False), _row("PK-2", "PO-211-B")])
    plan = d.batch_drawer_plan(dr, ["PO-211-A", "PO-211-B"])
    assert plan["check"] == ["PO-211-B"]
    assert plan["rows"] == 1


def test_plan_carries_pagination():
    dr = _drawer([_row("PK-1", "PO-211-A")], page=2, pages=3)
    plan = d.batch_drawer_plan(dr, ["PO-211-A"])
    assert plan["page"] == 2 and plan["pages"] == 3


# ---------------------------------------------------------------- JS 常量契约
def test_open_button_matches_by_prefix_not_equality():
    """按钮文本是 `批量确认发货 14`（数字会变）⇒ 必须前缀匹配，不能全等。"""
    js = d._JS_OPEN_BATCH_DRAWER
    assert "indexOf(want) === 0" in js, "要用前缀匹配，否则数字一变就找不到按钮"
    assert "\\u6279\\u91cf\\u786e\\u8ba4\\u53d1\\u8d27" in js, "按钮文案必须是「批量确认发货」"


def test_submit_button_also_prefix_matches():
    """底部「确认发货 14」同理。"""
    js = d._JS_CLICK_BATCH_SUBMIT
    assert "^\\u786e\\u8ba4\\u53d1\\u8d27" in js or "确认发货" in js


def test_uncheck_all_targets_the_header_checkbox():
    """取消全选必须点**表头**那个 checkbox（行内那些是逐单的）。"""
    js = d._JS_UNCHECK_ALL_BATCH
    assert "thead input[type=checkbox]" in js


def test_drawer_reader_is_scoped_to_the_drawer():
    """读行必须限定在抽屉里，否则会把**页面表格**的行也读进来（两份数据混一起）。"""
    js = d._JS_READ_BATCH_DRAWER
    assert "Drawer_content" in js
    assert "dw.querySelectorAll" in js, "所有查询都要走抽屉根节点"


# ---------------------------------------------------------------- 行为契约
def test_batch_confirm_refuses_when_nothing_approved(monkeypatch):
    """approved 为空 ⇒ 直接返回，**绝不去点任何按钮**（尤其不能点提交）。"""
    drv = d.BuylabelDriver.__new__(d.BuylabelDriver)
    calls = []
    drv.log = lambda m: None
    drv._orders_page_ready = lambda: calls.append("page") or object()
    drv._js = lambda *a, **kw: calls.append("js") or "ok"
    drv.cfg = {}
    out = drv.batch_confirm_orders([])
    assert out["stop"] == "no_approved"
    assert out["submitted"] == []
    assert calls == [], "没有任何 approved 时不许碰页面"


def test_batch_scan_never_submits(monkeypatch):
    """扫描阶段只读：即使调用方传 do_confirm=True 也**不勾不发**。"""
    drv = d.BuylabelDriver.__new__(d.BuylabelDriver)
    submitted = []
    drv.log = lambda m: None
    drv.shot = lambda *a, **kw: None
    drv.cfg = {"pending_confirm": {"tab": "待发货"}}
    drv._orders_page_ready = lambda: object()
    drv._js = lambda page, script, *a, **kw: "ok"
    drv.set_list_page_size = lambda page, cap: "already:500"
    drv.open_batch_drawer = lambda page, **kw: _drawer(
        [_row("PK-1", "PO-211-A"), _row("PK-2", "PO-211-B")])
    drv.read_batch_drawer = lambda page: _drawer(
        [_row("PK-1", "PO-211-A"), _row("PK-2", "PO-211-B")])
    drv.close_batch_drawer = lambda page: None
    drv.batch_submit = lambda *a, **kw: submitted.append(1) or "ok"
    out = drv.batch_scan_orders(do_confirm=True)
    assert out["po"] == ["PO-211-A", "PO-211-B"]
    assert submitted == [], "batch_scan 绝不能提交"


def test_batch_scan_walks_every_drawer_page():
    """「抽屉可以存在多页，出现多页需要翻页」⇒ 每一页的 PO 都要收集。"""
    drv = d.BuylabelDriver.__new__(d.BuylabelDriver)
    drv.log = lambda m: None
    drv.shot = lambda *a, **kw: None
    drv.cfg = {"pending_confirm": {"tab": "待发货"}}
    drv._orders_page_ready = lambda: object()
    drv._js = lambda page, script, *a, **kw: "ok"
    drv.set_list_page_size = lambda page, cap: "already:500"
    drv.open_batch_drawer = lambda page, **kw: _drawer(
        [_row("PK-1", "PO-211-A")], page=1, pages=2)
    cur = {"page": 1}

    def _read(page):
        if cur["page"] == 1:
            return _drawer([_row("PK-1", "PO-211-A")], page=1, pages=2)
        return _drawer([_row("PK-2", "PO-211-B")], page=2, pages=2)

    drv.read_batch_drawer = _read

    def _goto(page, n, **kw):
        cur["page"] = n
        return "ok"

    drv.batch_goto_page = _goto
    drv.close_batch_drawer = lambda page: None
    out = drv.batch_scan_orders()
    assert out["pages"] == 2
    assert out["po"] == ["PO-211-A", "PO-211-B"]


def test_batch_confirm_stops_on_failed_submit():
    """提交失败必须**立刻停**（继续发会重复发货）。"""
    drv = d.BuylabelDriver.__new__(d.BuylabelDriver)
    drv.log = lambda m: None
    drv.shot = lambda *a, **kw: None
    drv.cfg = {"pending_confirm": {"tab": "待发货"}}
    drv._orders_page_ready = lambda: object()
    drv._js = lambda page, script, *a, **kw: "ok"
    drv.open_batch_drawer = lambda page, **kw: _drawer(
        [_row("PK-1", "PO-211-A")], page=1, pages=1)
    drv.read_batch_drawer = lambda page: _drawer([_row("PK-1", "PO-211-A")], page=1, pages=1)
    drv.batch_uncheck_all = lambda page: "ok"
    drv.batch_check = lambda page, pos, want: len(list(pos))
    drv.close_batch_drawer = lambda page: None
    drv.batch_submit = lambda *a, **kw: "not_closed"
    out = drv.batch_confirm_orders(["PO-211-A"])
    assert out["submitted"] == [], "提交没成功就不能记成已发"
    assert out["stop"].startswith("submit_")


def test_batch_confirm_refuses_to_submit_when_check_did_not_apply():
    """勾选没生效（changed=0）时**不许点提交** —— 那等于把默认全选的其它单也发出去。"""
    drv = d.BuylabelDriver.__new__(d.BuylabelDriver)
    drv.log = lambda m: None
    drv.shot = lambda *a, **kw: None
    drv.cfg = {"pending_confirm": {"tab": "待发货"}}
    drv._orders_page_ready = lambda: object()
    drv._js = lambda page, script, *a, **kw: "ok"
    drv.open_batch_drawer = lambda page, **kw: _drawer(
        [_row("PK-1", "PO-211-A")], page=1, pages=1)
    drv.read_batch_drawer = lambda page: _drawer([_row("PK-1", "PO-211-A")], page=1, pages=1)
    drv.batch_uncheck_all = lambda page: "ok"
    drv.batch_check = lambda page, pos, want: 0          # 没生效
    submitted = []
    drv.batch_submit = lambda *a, **kw: submitted.append(1) or "ok"
    drv.close_batch_drawer = lambda page: None
    out = drv.batch_confirm_orders(["PO-211-A"])
    assert submitted == [], "勾选没生效绝不能提交"
    assert out["submitted"] == []
