# -*- coding: utf-8 -*-
"""★ 2026-10-28（用户口径）`--action pending`：处理「待发货」里**已买完面单**的订单。

用户原话：「处理待发货的手动确认订单 / 加一个类似处理逾期订单去点击：去手动确认的功能；
但不过现在是点击待发货里的**已经买完面单**的订单，默认直接**每页拉到 500 单**」。

这组用例钉死四件事：
  1. 分流判据：只有「有运单号 + 行内有『去手动确认』」才算可确认（纯函数，不碰网络）；
  2. 计划层：0 行 / 无可确认 要判成"正常收工"（noop），不是失败；
  3. 顺序铁律：**先拉满每页，再读列表**（反了只读到 20 行，静默漏单）；
  4. 与 overdue 的**本质区别**：pending **不点任何待办卡**。
"""
import inspect
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "lib"))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

import temu_buylabel_driver as d  # noqa: E402


# ---------------------------------------------------------------- 纯函数：分流
def test_split_pending_three_ways():
    """**主判据是「去手动确认」按钮**（用户口径：已买完面单的都会有这个按钮）。

    · 有按钮 + 有运单号 → confirm
    · 没按钮（＝没买面单）→ need_label
    · 有按钮但读不到运单号 → other（不硬点）
    """
    rows = [
        {"ord": "PO-211-A", "track": "1Z999", "manual": True,  "status": "待发货"},
        {"ord": "PO-211-B", "track": "",      "manual": False, "status": "待发货"},
        {"ord": "PO-211-C", "track": "",      "manual": True,  "status": "待发货"},
        {"ord": "PO-211-D", "track": "1Z888", "manual": False, "status": "待发货"},
    ]
    s = d.split_pending_rows(rows)
    assert s["confirm"] == ["PO-211-A"], s
    assert s["need_label"] == ["PO-211-B", "PO-211-D"], s
    assert s["other"] == ["PO-211-C"], s
    assert s["counts"] == {"confirm": 1, "need_label": 2, "other": 1}


def test_button_is_the_primary_signal_not_track():
    """★ 核心口径：**有按钮就能确认**，不因"运单号没解析到"就漏掉它。

    用户原话：「你直接去找已经买完面单的，都会有一个手动确认，可点击，在待发货页面」。
    ⇒ `require_track=False` 时，只有按钮、运单号读不出来的行也**必须**进 confirm；
    ⇒ 缺省（双重确认）时进 `other`，**绝不**进 `need_label`（那语义是"没面单"）。
    """
    rows = [{"ord": "PO-211-X", "track": "", "manual": True}]
    assert d.split_pending_rows(rows, require_track=False)["confirm"] == ["PO-211-X"]
    s = d.split_pending_rows(rows, require_track=True)
    assert s["confirm"] == []
    assert s["other"] == ["PO-211-X"]
    assert s["need_label"] == [], "有按钮却归到 need_label 是错的——它有面单，只是号没读到"


def test_split_pending_ignores_rows_without_order_no():
    """读不到单号的行（表头/合计行）必须跳过，不能当成一单。"""
    rows = [{"ord": "", "track": "X", "manual": True}, {"ord": "  ", "track": "Y", "manual": True}]
    s = d.split_pending_rows(rows)
    assert s["counts"] == {"confirm": 0, "need_label": 0, "other": 0}


def test_split_pending_empty():
    assert d.split_pending_rows([])["counts"] == {"confirm": 0, "need_label": 0, "other": 0}


def test_require_track_false_promotes_rows_with_button():
    """`require_track` 只控制"要不要额外校验运单号"，**不控制**按钮这个主判据。"""
    rows = [{"ord": "PO-211-B", "track": "", "manual": True}]
    assert d.split_pending_rows(rows, require_track=True)["confirm"] == []
    assert d.split_pending_rows(rows, require_track=False)["confirm"] == ["PO-211-B"]


# ---------------------------------------------------------------- 纯函数：计划
def test_plan_no_rows_is_noop():
    """整页 0 行 ⇒ should_run=False / no_rows（正常收工，别报失败）。"""
    p = d.pending_confirm_plan([], cap=500)
    assert p["should_run"] is False
    assert p["stop_reason"] == "no_rows"
    assert p["rows"] == 0


def test_plan_rows_but_nothing_confirmable():
    """有单但全都没「去手动确认」按钮（＝都还没买面单）⇒ noop，不是失败。"""
    rows = [{"ord": "PO-211-B", "track": "", "manual": False},
            {"ord": "PO-211-C", "track": "", "manual": False}]
    p = d.pending_confirm_plan(rows, cap=500)
    assert p["should_run"] is False
    assert p["stop_reason"] == "no_confirmable"
    assert p["rows"] == 2
    assert p["counts"]["need_label"] == 2


def test_plan_runs_when_confirmable():
    rows = [{"ord": "PO-211-A", "track": "1Z999", "manual": True}]
    p = d.pending_confirm_plan(rows, cap=500)
    assert p["should_run"] is True
    assert p["stop_reason"] == ""
    assert p["confirm"] == ["PO-211-A"]


def test_plan_caps_confirm_list():
    """可确认数超过 cap 时只取前 cap 个，并标 over_cap（防一页读回失真时无限跑）。"""
    rows = [{"ord": "PO-211-%03d" % i, "track": "T%d" % i, "manual": True} for i in range(10)]
    p = d.pending_confirm_plan(rows, cap=4)
    assert p["over_cap"] is True
    assert len(p["confirm"]) == 4
    assert p["counts"]["confirm"] == 10, "counts 要保留真实总数，别被 cap 掩盖"


# ---------------------------------------------------------------- driver 方法契约
def test_pending_defaults_to_500_page_size():
    """用户口径「默认直接每页拉到 500 单」——口径缺省必须是 500。"""
    src = inspect.getsource(d.BuylabelDriver.pending_confirm_orders)
    assert 'get("page_size")' in src
    assert "500" in src, "缺省每页 500 要写死在回退链里"


def test_pending_sets_page_size_before_reading_rows():
    """顺序铁律：**先 set_list_page_size，再读 `_JS_OVERDUE_ROWS`**。

    反了只读到默认 20 行 ⇒ 只确认前 20 单，其余静默漏掉（历史事故：31 单只买到 20 单）。
    ⚠ 只在**代码体**里比顺序：docstring 里也提到这两个名字，直接 index 会被它干扰。
    """
    src = inspect.getsource(d.BuylabelDriver.pending_confirm_orders)
    body = src.split('"""')[-1]          # 去掉 docstring，只留代码
    i_ps = body.index("set_list_page_size")
    i_rows = body.index("_JS_OVERDUE_ROWS")
    assert i_ps < i_rows, "必须先拉满每页再读列表"


def test_pending_does_not_click_todo_card():
    """与 overdue 的**本质区别**：pending 不点任何待办卡（那是 overdue 的做法）。

    ⚠ 只查**代码**里有没有调用待办卡那两个 JS（docstring 里提到 "overdue" 是正常的说明文字）。
    """
    src = inspect.getsource(d.BuylabelDriver.pending_confirm_orders)
    body = src.split('"""')[-1]
    assert "_JS_OVERDUE_TODO" not in body, "pending 不许点待办卡——它是读整页列表"
    assert "_JS_OVERDUE_ZONE" not in body, "pending 不依赖待办区"


def test_pending_does_not_reuse_confirm_shipping():
    """★★ 2026-10-28 真机事故回归：**不许**复用 `confirm_shipping()`。

    原因：它内部 `_search_orders(...)` 把**全部单号**塞进「订单号」筛选框，而该框写死
    「**每次最多100个**」。pending 有 313 单 ⇒ 框里被截断成无效串 ⇒ 筛选失效 ⇒
    循环反复点**同一个**单（实测连点 68 次），最后浏览器 WebSocket 500 崩掉，净只确认 1 单。

    ⇒ pending 必须**不筛选、原地按单号点名**点，并用「同一单连续点中」判死。
    """
    src = inspect.getsource(d.BuylabelDriver.pending_confirm_orders)
    body = src.split('"""')[-1]
    assert "self.confirm_shipping(" not in body, \
        "confirm_shipping 会用超 100 个单号去筛选，必然截断失效——不许再复用"
    assert "_confirm_pending_in_place" in body, "要走原地逐单确认"


def test_success_means_button_cleared_not_row_gone():
    """★★ 最反直觉的一条（2026-10-28 真机查明）：确认成功后**订单仍留在「待发货」**。

    实测：点「去手动确认」→「确认」，页面弹绿色「操作成功」，该单状态仍是**待发货**
    （等仓库出库），**只是那一行的「去手动确认」按钮没了**（操作列只剩 订单详情/修改备注）。
    ⇒ 成功判据必须是「**按钮消失**」，不是「订单从列表消失」。
      第一版按"消失"判，3 单全被误记成失败，而它们其实已经确认成功。

    本用例钉死这个判据：有 `_wait_manual_cleared`，且**没有**残留"等整单消失"的旧函数名。
    """
    assert hasattr(d.BuylabelDriver, "_wait_manual_cleared")
    src = inspect.getsource(d.BuylabelDriver._wait_manual_cleared)
    assert 'get("manual")' in src, "判据必须看 manual（按钮在不在）"
    # 单笔确认逻辑现在在 _do_one_pending_confirm（为了让传输错误能被上一层重连接住）
    body = inspect.getsource(d.BuylabelDriver._do_one_pending_confirm)
    assert "_wait_manual_cleared(" in body
    assert "_wait_order_gone(" not in body, "别退回『等整单消失』那个错判据"


def test_pending_failure_skips_that_order_not_whole_batch():
    """单笔失败不中断整批：失败单进 skip，继续处理其它单（309 单规模下必须如此）。"""
    src = inspect.getsource(d.BuylabelDriver._confirm_pending_in_place)
    body = src.split('"""')[-1]
    assert "skip = set()" in body
    assert "o not in skip" in body, "选下一单时要排除已失败的"
    assert "skip.add(ordno)" in body


def test_pending_reconnects_after_transport_crash():
    """★★ 2026-10-28 真机事故回归：CDP 跑到 ~100 单会崩（WebSocket HTTP 500）。

    BEL 跑到第 102 单时直接崩、整条动作判失败（其实前 102 单都已成功）；BHY 崩过一次。
    ⇒ 必须**就地重连接着干**，而不是整批失败。已确认的单按钮已消失，重跑天然幂等。
    """
    src = inspect.getsource(d.BuylabelDriver._confirm_pending_in_place)
    assert "_recover_orders_page" in src, "崩了要能自愈重连"
    assert "transport_dead" in src, "重连到上限才停"
    assert hasattr(d.BuylabelDriver, "_recover_orders_page")
    # 传输错误不能被误记成"这单点不动"然后 skip 掉
    one = inspect.getsource(d.BuylabelDriver._do_one_pending_confirm)
    assert "except" not in one, "单笔方法不许吞异常——传输错误必须冒泡给重连分支"


def test_pending_clicks_by_order_number_not_first_row():
    """必须按**单号点名**点「去手动确认」，不能用"取当前第一行"。

    "取第一行"在"点了不消失"时会原地空转，且日志里看不出问题（每次都是同一个单号）。
    """
    src = inspect.getsource(d.BuylabelDriver._do_one_pending_confirm)
    assert "_JS_CLICK_MANUAL_BY_ORD" in src
    assert "_JS_CLICK_MANUAL_FIRST" not in src, "回退到 FIRST 就会重现空转 bug"


def test_pending_has_stuck_guard():
    """护栏：同一单连续被点中且没消失 ⇒ 达上限即停下报错，绝不无限点。"""
    src = inspect.getsource(d.BuylabelDriver._confirm_pending_in_place)
    assert "max_strikes" in src
    assert 'stop_reason = "stuck:' in src
    assert "21_pending_stuck" in src, "判死要落图留证"


def test_pending_in_place_rereads_list_each_round():
    """每轮都要重读列表（确认一单单就少一行），不能启动时算一次用到底。"""
    src = inspect.getsource(d.BuylabelDriver._confirm_pending_in_place)
    body = src.split('"""')[-1]
    # 循环体内必须有读列表 + 重新算计划的动作
    assert "_JS_OVERDUE_ROWS" in body
    assert "pending_confirm_plan(" in body


def test_pending_switches_to_daifahuo_tab():
    """必须显式切到「待发货」页签，并处理切不到的情况（落图 + 报错）。"""
    src = inspect.getsource(d.BuylabelDriver.pending_confirm_orders)
    assert "_JS_CLICK_TAB" in src
    assert "21_pending_no_tab" in src, "切不到页签要落图，别静默"


def test_pending_reports_noop_route():
    """正常收工要有 noop/route/reason（与 overdue 同口径，上层据此不开失败卡）。"""
    src = inspect.getsource(d.BuylabelDriver.pending_confirm_orders)
    assert '"noop": True' in src
    assert '"route": "pending_none"' in src


# ---------------------------------------------------------------- 口径
def test_config_declares_pending_confirm():
    """口径文件要声明 `pending_confirm`（用 driver 自己的 `_DATA_DIR`，别手拼路径）。"""
    import json
    p = os.path.join(d._DATA_DIR, "temu_buylabel.json")
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    pc = cfg.get("pending_confirm") or {}
    assert pc.get("tab") == "待发货"
    assert int(pc.get("page_size")) == 500, "用户口径：默认每页 500"
    assert pc.get("require_track") is True, "只处理已买完面单的"


def test_perform_routes_pending():
    """perform() 要认得 action=pending（不能落到「需要 --file」那条）。"""
    src = inspect.getsource(d.perform)
    assert 'action == "pending"' in src
    assert "pending_confirm_orders" in src
