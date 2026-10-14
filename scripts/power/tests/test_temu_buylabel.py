# -*- coding: utf-8 -*-
"""test_temu_buylabel.py — TEMU 购买面单 driver 纯逻辑单元验证（离线，不开浏览器）。

验证点:
    - 口径 json 完整性（尺寸/单件重量/仓库映射/取件策略/异常分支）
    - parse_delivery_orders：按列名定位、丢掉「总计」行、数量/仓库取得到
    - build_plan：重量 = 单件重 × 数量；出库仓库 → 在线下单页选项文本
    - 未知仓库走默认（不静默丢单）
    - 写操作闸门常量（入口脚本用）
运行: python -m pytest scripts/power/tests/test_temu_buylabel.py -q
"""
import json
import os
import sys
import tempfile

import pandas as pd
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "lib"))
sys.path.insert(0, os.path.join(_HERE, ".."))

import temu_buylabel_driver as d  # noqa: E402

#: 真登记表的 popups 段（桩默认拿它建临时表，判据与生产一致；见 _DrvStub 注释）
with open(os.path.join(_HERE, "..", "data", "temu_buylabel_popups.json"), encoding="utf-8") as _f:
    _REAL_POPUPS = (json.load(_f).get("popups") or [])

CFG_PATH = os.path.join(_HERE, "..", "data", "temu_buylabel.json")


@pytest.fixture
def cfg():
    return d.load_config(CFG_PATH)


@pytest.fixture
def delivery_xlsx(tmp_path):
    """造一份与派送订单同结构的表（含「总计」行）。"""
    df = pd.DataFrame([
        {"序号": "1", "参考单号": "PO-211-00418499799671568", "出库仓库": "加州3仓", "计费重量": "3.60", "SKU": "H207843288", "数量": "2"},
        {"序号": "2", "参考单号": "PO-211-00283244104312310", "出库仓库": "加州3仓", "计费重量": "1.00", "SKU": "D14450XZW12", "数量": "1"},
        {"序号": "3", "参考单号": "PO-211-00261674957432441", "出库仓库": "Kansas仓", "计费重量": "3.00", "SKU": "X1", "数量": "2"},
        {"序号": "10", "参考单号": None, "出库仓库": "总计", "计费重量": "17.54", "SKU": None, "数量": None},
    ])
    p = tmp_path / "2026091280501派送订单.xlsx"
    df.to_excel(p, index=False, sheet_name="Worksheet")
    return str(p)


def test_config_shape(cfg):
    for k in ("orders_url", "shipping_url", "dimensions_in", "weight_per_unit_lb",
              "warehouse_map", "logistics", "pickup", "no_carrier_fallback"):
        assert k in cfg, "口径缺字段 " + k
    assert cfg["dimensions_in"]["length"] and cfg["dimensions_in"]["width"] and cfg["dimensions_in"]["height"]
    assert cfg["pickup"]["date_policy"] == "earliest"
    assert cfg["no_carrier_fallback"]["warehouse"]


def test_parse_delivery_orders(delivery_xlsx):
    rows = d.parse_delivery_orders(delivery_xlsx)
    # 「总计」行（参考单号空）被丢掉
    assert [r["order_no"] for r in rows] == [
        "PO-211-00418499799671568", "PO-211-00283244104312310", "PO-211-00261674957432441"]
    assert rows[0]["qty"] == "2"
    assert rows[0]["warehouse_raw"] == "加州3仓"
    assert rows[2]["warehouse_raw"] == "Kansas仓"


def test_build_plan_weight_and_warehouse(delivery_xlsx, cfg):
    plan = d.build_plan(d.parse_delivery_orders(delivery_xlsx), cfg)
    # 重量 = 单件重(口径 weight_per_unit_lb) × 数量；口径 2026-09-24 由 1.1 改 2 ⇒ 2 件=4.00、1 件=2.00
    per = cfg["weight_per_unit_lb"]
    assert [p["weight_txt"] for p in plan] == [d.fmt_num(per * 2, 2), d.fmt_num(per, 2), d.fmt_num(per * 2, 2)]
    # 出库仓库 → 在线下单页选项文本
    assert plan[0]["warehouse"] == "加州三仓"
    assert plan[2]["warehouse"] == "kansas"


def test_unknown_warehouse_falls_back(cfg):
    rows = [{"order_no": "PO-1-1", "warehouse_raw": "火星仓", "qty": "1"}]
    plan = d.build_plan(rows, cfg)
    assert plan[0]["warehouse"] == cfg["warehouse_default"]


def test_qty_blank_defaults_one(cfg):
    plan = d.build_plan([{"order_no": "PO-1-2", "warehouse_raw": "加州3仓", "qty": ""}], cfg)
    assert plan[0]["weight_txt"] == d.fmt_num(cfg["weight_per_unit_lb"], cfg["weight_decimals"])


def test_plan_summary_contains_orders(delivery_xlsx, cfg):
    s = d.plan_summary(d.build_plan(d.parse_delivery_orders(delivery_xlsx), cfg))
    assert "PO-211-00418499799671568" in s
    assert "共 3 单" in s


def test_write_action_gate_defined():
    """入口脚本的写操作闸门：place/place_only/confirm/pipeline/reorder 必须 --confirm。

    2026-09-16 加 `pipeline`（整链含 place_only+confirm，属写操作），断言随改。
    2026-09-21 加 `reorder`（失败单重下；`--check-only` 时只出计划不点键）。
    2026-09-24 常量由 `MONEY_ACTIONS` 改名 `WRITE_ACTIONS`（用户令：口径里不要"花钱"这套描述）。
    2026-09-30 加 `overdue`（待办「即将逾期未发货」→ 逐单确认发货，属写操作），断言随改。
    """
    import run_temu_buylabel as r
    assert set(r.WRITE_ACTIONS) == {"place", "place_only", "all", "confirm", "pickup", "pipeline",
                                    "reorder", "overdue"}
    assert not hasattr(r, "MONEY_ACTIONS"), "旧名已废弃，别再留（口径里不许出现这套措辞）"


def test_split_overdue_rows_three_ways():
    """待办「即将逾期未发货」分流：能确认的 / 缺面单的 / 其它，必须分得清（别混着点）。"""
    rows = [
        {"ord": "PO-1", "track": "GFUS1", "manual": True},    # 有号+有按钮 → 确认
        {"ord": "PO-2", "track": "", "manual": False},        # 没号 → 先买面单
        {"ord": "PO-3", "track": "1Z2", "manual": False},     # 有号但没按钮 → 其它，不硬点
        {"ord": "", "track": "X", "manual": True},            # 空行 → 丢
    ]
    s = d.split_overdue_rows(rows)
    assert s["confirm"] == ["PO-1"]
    assert s["need_label"] == ["PO-2"]
    assert s["other"] == ["PO-3"]
    assert s["counts"] == {"confirm": 1, "need_label": 1, "other": 1}
    assert d.split_overdue_rows([])["counts"] == {"confirm": 0, "need_label": 0, "other": 0}


def test_parse_todo_count():
    """待办卡文案里的数字要取得到（用来判「本页读全了没」）。"""
    assert d.parse_todo_count("即将逾期未发货 11") == 11
    assert d.parse_todo_count("即将逾期未发货") == 0
    assert d.parse_todo_count("") == 0


def test_overdue_todo_card_uses_dom_click():
    """待办卡必须用 DOM click 触发——原生 CDP 合成鼠标点它页面无反应（2026-09-30 实测）。"""
    js = d._JS_OVERDUE_TODO
    assert ".click()" in js and "dispatchMouseEvent" not in js
    assert "offsetParent" in js, "必须是可见元素"
    assert "width < 120" in js, "只认卡片本体，别抓整行容器"


def test_overdue_sets_page_size_before_clicking_todo():
    """顺序不能反：**先调「每页」再点待办卡**——反了会把待办的筛选冲掉（列表弹回全店待发货）。

    2026-09-30 实测踩到：先点卡（列表筛成 11 条）再改每页 → 列表回到「共有 256 条」。
    """
    import inspect
    src = inspect.getsource(d.BuylabelDriver.overdue_orders)
    assert src.index("set_list_page_size") < src.index("_JS_OVERDUE_TODO")
    assert "20_overdue_filter_mismatch" in src, "筛选没生效必须停下取证，不许硬跑"
    assert "20_overdue_todo_missing" in src, "找不到待办卡也必须落图停下"


def test_overdue_guard_stops_when_filter_did_not_apply(monkeypatch, cfg):
    """卡上 11 单、列表却读出 256 行 ⇒ 必须抛错停下（今天真实发生过的那次）。"""
    drv = d.BuylabelDriver(cfg, log=lambda *a, **k: None)
    drv._orders_page_ready = lambda: object()
    drv.set_list_page_size = lambda page, cap: "already:500"
    drv.shot = lambda *a, **k: None
    monkeypatch.setattr(d.time, "sleep", lambda *_: None)

    def fake_js(page, script, arg=None, tries=3):
        if script is d._JS_OVERDUE_TODO:
            return "即将逾期未发货 11"
        if script is d._JS_OVERDUE_ROWS:
            return [{"ord": "PO-211-000%d" % i, "track": "GFUS%d" % i, "manual": True}
                    for i in range(256)]
        return None

    drv._js = fake_js
    with pytest.raises(RuntimeError) as e:
        drv.overdue_orders()
    assert "筛选没生效" in str(e.value)


def test_overdue_rows_to_delivery_and_xlsx_roundtrip(tmp_path):
    """缺面单行 → 派送表：列口径必须能被 `parse_delivery_orders()` 原样读回（整链就是这个输入）。"""
    rows = [
        {"ord": "PO-211-111", "track": "", "warehouse": "加州三仓", "qty": "2"},
        {"ord": "PO-211-222", "track": "GFUS1", "warehouse": "加州三仓", "qty": "1"},   # 已有号 → 不进表
        {"ord": "PO-211-333", "track": "", "warehouse": "", "qty": "1"},                # 没仓 → 不猜，进 skipped
    ]
    delivery, skipped = d.overdue_rows_to_delivery(rows)
    assert delivery == [{"参考单号": "PO-211-111", "出库仓库": "加州三仓", "数量": "2"}]
    assert [x["ord"] for x in skipped] == ["PO-211-333"]

    p = d.write_delivery_xlsx(delivery, str(tmp_path / "d.xlsx"))
    parsed = d.parse_delivery_orders(p)
    assert [(x["order_no"], x["warehouse_raw"], x["qty"]) for x in parsed] == [("PO-211-111", "加州三仓", "2")]
    assert d.write_delivery_xlsx([], str(tmp_path / "e.xlsx")) == ""
    assert not os.path.exists(str(tmp_path / "e.xlsx"))


def test_entry_pipes_overdue_need_label_into_the_same_pipeline():
    """缺面单那批必须喂给**同一条整链**（用户令「只使用 PIPELINE」），不许另写一遍下单流程。"""
    import run_temu_buylabel as r
    src = open(os.path.join(os.path.dirname(os.path.abspath(r.__file__)),
                            "run_temu_buylabel.py"), encoding="utf-8").read()
    i = src.index('action == "overdue"')
    seg = src[i:i + 1200]
    assert "--with-label" in src
    assert "run_steps(spec.get(\"steps\"), sub" in seg, "缺面单那批要走同一条 steps 整链"
    assert "steps_config" in src and "spec = json.load" in seg


def test_signals_consistent_catches_the_real_2026_09_30_false_signal():
    """2026-09-30 实测的假信号：want=11，四个计数全 0，not_found 也 0 ⇒ 自相矛盾，判不可信。

    真实后果：路线被取成 `no_orders`（"没有可买的"）＝把「列表没读出来」伪装成业务结论。
    """
    bad = {"want": 11, "pending": 0, "shipped": 0, "cancelled": 0, "skippable": 0, "not_found": 0}
    ok, why = d.signals_consistent(bad)
    assert ok is False and "自相矛盾" in why or "见着了" in why
    # 一行都没匹配到 → 也不可信
    assert d.signals_consistent({"want": 11, "not_found": 11, "pending": 0, "skippable": 0})[0] is False
    # 正常形态要放行
    assert d.signals_consistent({"want": 11, "pending": 11, "not_found": 0, "skippable": 0})[0] is True
    assert d.signals_consistent({"want": 11, "pending": 0, "skippable": 11, "not_found": 0})[0] is True
    assert d.signals_consistent({"want": 0})[0] is True


def test_is_transport_error_matches_the_real_disconnect():
    """真实那条：`server rejected WebSocket connection: HTTP 500` 必须被认成传输层错误。

    这类错误不能直接当失败（2026-09-30：报失败、平台侧 11/11 已下单成功）。
    """
    assert d.is_transport_error(RuntimeError("server rejected WebSocket connection: HTTP 500")) is True
    assert d.is_transport_error(RuntimeError("Target closed")) is True
    assert d.is_transport_error(RuntimeError("页面未就绪，拒绝确认下单：行内重量全为空")) is False


def test_screen_stops_on_unclear_reads(monkeypatch, cfg):
    """读不清时 screen 必须停下报错，**不许**按信号取路线（否则会取到 no_orders 假结论）。"""
    drv = d.BuylabelDriver(cfg, log=lambda *a, **k: None)
    drv._orders_page_ready_lenient = lambda: (object(), [])
    drv.collect_signals = lambda page, order_nos, fail=0: (
        {"want": 2, "pending": 0, "skippable": 0, "not_found": 0, "read_unclear": True,
         "unclear_why": "自相矛盾"}, {})
    drv.shot = lambda *a, **k: None
    called = {"route": False}
    monkeypatch.setattr(d, "pick_route", lambda *a, **k: called.__setitem__("route", True) or {})
    with pytest.raises(RuntimeError) as e:
        drv.screen(["PO-1", "PO-2"])
    assert "读不清" in str(e.value)
    assert called["route"] is False, "读不清就不该再取路线"


def test_verify_only_keeps_orders_we_asked_for(monkeypatch, cfg):
    """verify 只收本批的单：筛选没生效时读到的"别人的行"不许进结果（2026-09-30 实测踩到）。"""
    drv = d.BuylabelDriver(cfg, log=lambda *a, **k: None)
    drv._orders_page_ready = lambda: object()
    drv._search_orders = lambda page, tab, want: None
    drv.shot = lambda *a, **k: None
    monkeypatch.setattr(d.time, "sleep", lambda *_: None)
    rows = [{"ord": "PO-211-OTHER", "status": "待发货", "track": "X1"},
            {"ord": "PO-211-MINE", "status": "已发货", "track": "GFUS1"}]
    drv._js = lambda page, script, arg=None, tries=3: rows if script is d._JS_LIST_ROWS else None
    out = drv.verify(["PO-211-MINE"])
    assert list(out["rows"]) == ["PO-211-MINE"]
    assert out["ok"] == ["PO-211-MINE"] and out["miss"] == []


def test_place_only_reads_back_after_transport_error():
    """place_only 必须带「提交后断连→回读平台再判」这段（否则假失败/重复下单，2026-09-30 踩到）。"""
    import inspect
    src = inspect.getsource(d.perform)
    i = src.index('if action == "place_only"')
    seg = src[i:i + 2600]
    assert "is_transport_error" in seg
    assert "drv.verify(order_nos)" in seg, "断连后必须回读"
    assert "回读确认已下单" in seg
    assert "不自动重下单" in seg, "回读不足时必须如实报、不自动重下单"


def test_config_declares_overdue_todo_text(cfg):
    """待办卡文案必须落在口径里（页面改文案＝改数据，不改代码）。"""
    assert (cfg.get("overdue") or {}).get("todo_text") == "即将逾期未发货"


def test_entry_declares_overdue_action():
    """入口要认得 `--action overdue`，且它不需要外部给单号（单子由页面待办现取）。"""
    import run_temu_buylabel as r
    assert "overdue" in r.WRITE_ACTIONS
    src = open(os.path.join(os.path.dirname(os.path.abspath(r.__file__)),
                            "run_temu_buylabel.py"), encoding="utf-8").read()
    assert '"overdue"\n' in src or '"overdue"]' in src or '"auth", "overdue"' in src
    assert '"overdue")' in src, "要挂在「不需要 --orders/--file」那条豁免里"


def test_judge_filled_gate(cfg):
    """place_only 的下单前闸门：页面没填好 / 串单 → 必须拒绝（防误提交、防下错单）。"""
    good = {"head": "1", "weights": ["1.10"], "picks": ["kansas", "UPS, Ground saver"],
            "confirm_btn": True, "freight": "$5.41"}
    url = "https://x/online-shipping.html?parent_order_sn_list=%5B%22PO-211-00591977513590598%22%5D"
    ok, why = d.judge_filled(good, ["PO-211-00591977513590598"], url)
    assert ok is True and why == []

    # 未填单（页面刷新后）→ 拒
    ok2, why2 = d.judge_filled({"head": "1", "weights": [], "picks": [], "confirm_btn": True},
                               ["PO-211-00591977513590598"], url)
    assert ok2 is False and any("重量" in w for w in why2)

    # 仓库/服务未选全 → 拒
    ok3, why3 = d.judge_filled(dict(good, picks=["kansas"]), ["PO-211-00591977513590598"], url)
    assert ok3 is False and any("物流服务" in w for w in why3)

    # 串单：URL 里没有目标单号 → 拒
    ok4, why4 = d.judge_filled(good, ["PO-211-99999999999999999"], url)
    assert ok4 is False and any("串单" in w for w in why4)

    # 页头订单数对不上 → 拒
    ok5, why5 = d.judge_filled(dict(good, head="0"), ["PO-211-00591977513590598"], url)
    assert ok5 is False and any("订单数" in w for w in why5)


def test_judge_records_total_equals_success(cfg):
    """平台侧完成判据（用户 2026-09-12 定）：在线下单记录里「下单总数 == 成功数」，不进详情。"""
    raw = ["TK-0858483830292533163 2026-09-12 20:09:09 稍后发货 1 1 0 0 全部成功 查看详情 打印物流单据",
           "TK-0858490085048373163 2026-09-12 18:31:55 稍后发货 8 8 0 0 全部成功 查看详情 打印物流单据"]
    rows = d.parse_record_rows(raw)
    assert len(rows) == 2
    assert rows[0] == {"task": "TK-0858483830292533163", "at": "2026-09-12 20:09:09", "ship_type": "稍后发货",
                       "total": 1, "success": 1, "fail": 0, "converted": 0, "status": "全部成功"}
    ok, why, checked = d.judge_records(rows)
    assert ok is True and why == [] and len(checked) == 1        # 默认只判最近一条

    # 不一致：成功数少 1
    bad = d.parse_record_rows(["TK-1 2026-09-12 20:00:00 稍后发货 9 8 1 0 部分成功"])
    ok2, why2, _ = d.judge_records(bad)
    assert ok2 is False and any("下单总数" in w for w in why2) and any("失败数" in w for w in why2)

    # 失败数为 0 但状态不是全部成功
    bad2 = d.parse_record_rows(["TK-2 2026-09-12 20:00:00 稍后发货 1 1 0 0 下单失败"])
    ok3, why3, _ = d.judge_records(bad2)
    assert ok3 is False and any("状态" in w for w in why3)

    # 空表 → 拒（不许当成功）
    ok4, why4, _ = d.judge_records([])
    assert ok4 is False and any("没读到" in w for w in why4)

    # 解析容错：非任务行/缺列一律丢掉
    assert d.parse_record_rows(["随便一行", "", None, "TK-3 缺列"]) == []


def test_failure_retry_config(cfg):
    """D8 失败单规则的**数据不变量**：口径里必须有可执行的换渠道顺序（不许写死在代码里）。"""
    fr = cfg.get("failure_retry") or {}
    assert fr.get("enabled") is True, "failure_retry 未启用"
    order = fr.get("carrier_order") or []
    assert isinstance(order, list) and len(order) >= 2, "换渠道顺序至少要 2 个候选（要能换）"
    assert all(isinstance(x, str) and x.strip() for x in order), "carrier_order 元素必须是非空字符串"
    assert int(fr.get("max_rounds") or 0) >= 1, "max_rounds 必须 ≥1"
    # 换渠道候选必须都是页面真实出现过的服务名（与物流服务下拉一致性）
    for svc in order:
        assert any(svc.split(",")[0].strip() in opt or opt in svc
                   for opt in (cfg["logistics"].get("carrier_options") or []) + ["USPS", "GOFO", "SpeedX"]), \
            "换渠道候选 %r 不在已知物流商里" % svc


def test_pick_service_by_cost(cfg):
    """D8 换渠道规则（用户定：按成本从低到高）：报价解析 + 跳过失败渠道 + 忽略禁用项。"""
    opts = [
        {"text": "UPS, Ground saver 预估$5.41; 2-6 个工作日送达", "disabled": False},
        {"text": "FedEx, ground economy 预估$6.15; 2-7 个工作日送达", "disabled": False},
        {"text": "USPS, Priority 预估$9.99; 3-5 个工作日送达", "disabled": True},
    ]
    assert d.parse_service_quote(opts[0]["text"]) == 5.41
    assert d.parse_service_quote("没有报价的服务") is None
    # 无排除 → 最便宜
    assert d.pick_service_by_cost(opts)[0].startswith("UPS")
    # 排除刚失败的 UPS → 换 FedEx（次便宜；USPS 虽更贵且 disabled 被忽略）
    assert d.pick_service_by_cost(opts, exclude="UPS, Ground saver")[0].startswith("FedEx")
    # 全禁用 / 全无报价 → 不给选择（不许瞎选）
    assert d.pick_service_by_cost([{"text": "X 预估$1", "disabled": True}]) == (None, None)
    assert d.pick_service_by_cost([{"text": "无报价"}]) == (None, None)
    assert d.pick_service_by_cost([]) == (None, None)


def test_check_three_numbers(cfg):
    """完成指标（用户 2026-09-14 定）：任务号 + 运单号 + 包裹号 三号齐才算完成。"""
    ok, missing = d.check_three_numbers({"task": "TK-0858483830292533163",
                                         "waybill": "1Z16E50BYW67644665",
                                         "package": "PK-0858483556352133163"})
    assert ok is True and missing == []
    ok2, miss2 = d.check_three_numbers({"task": "TK-1", "waybill": "1Z1"})
    assert ok2 is False and miss2 == ["包裹号 PK-…"]
    ok3, miss3 = d.check_three_numbers({})
    assert ok3 is False and len(miss3) == 3
    # 空白串不算拿到
    assert d.check_three_numbers({"task": " ", "waybill": "1Z1", "package": "PK1"})[0] is False


def test_check_packages_per_package(cfg):
    """完成指标（用户 2026-09-14 定）：**批量也逐包裹核**——每个包裹都要有自己的运单号与包裹号。"""
    rows = ["1Z16E50BYW67644665 包裹号： PK-0858483556352133163 订单号： PO-1",
            "1ZAAAAAAAAAAAAAAAA 包裹号： PK-0858483556352133164 订单号： PO-2"]
    pk = d.parse_package_rows(rows)
    assert len(pk) == 2 and all(x["waybill"] and x["package"] for x in pk)
    ok, missing, detail = d.check_packages("TK-9", pk)
    assert ok is True and missing == [] and len(detail) == 2

    # 第 2 个包裹缺包裹号 → 整体未完成，且要指出是第几个包裹
    bad = d.parse_package_rows([rows[0], "1ZAAAAAAAAAAAAAAAA 订单号： PO-2"])
    ok2, miss2, detail2 = d.check_packages("TK-9", bad)
    assert ok2 is False and any("第 2 个包裹缺包裹号" in m for m in miss2)
    assert detail2[0]["ok"] is True and detail2[1]["ok"] is False

    # 缺任务号 → 未完成
    assert d.check_packages("", pk)[1][0] == "任务号 TK-…"
    # 一个包裹行都没读到 → 未完成（不许当"没有包裹就没事"）
    ok3, miss3, _ = d.check_packages("TK-9", [])
    assert ok3 is False and any("一个包裹行都没读到" in m for m in miss3)


def test_batch_url_contract(cfg):
    """P0 自开批量页的 URL 契约（与现场实读一致：JSON 数组 + open_type=OnlineShip）。"""
    u = d.batch_url(cfg, ["PO-211-00591977513590598"])
    assert u.startswith(cfg["shipping_url"] + "?parent_order_sn_list=")
    assert "%5B%22PO-211-00591977513590598%22%5D" in u
    assert u.endswith("&open_type=OnlineShip")
    u2 = d.batch_url(cfg, ["PO-1-1", "PO-2-2"])
    assert "%5B%22PO-1-1%22%2C%22PO-2-2%22%5D" in u2


def test_count_packages_in_url():
    """TB-033：窗口里可能同时存在**别人的**结果页 → 按 package_sn_list 的包裹数与任务行对账。"""
    u16 = ("https://agentseller-us.temu.com/mmsos/online-shipping-result.html?package_sn_list=%5B%22PK-5080640743014533163%22"
           "%2C%22PK-5080615241646213163%22%5D&open_type=OnlineShipAsync")
    assert d.count_packages_in_url(u16) == 2
    assert d.count_packages_in_url(
        "https://x/mmsos/online-shipping-result.html?package_sn_list=%5B%22PK-5080614621020293163%22%5D") == 1
    assert d.count_packages_in_url(
        "https://x/mmsos/online-shipping.html?parent_order_sn_list=%5B%22PO-1%22%2C%22PO-2%22%2C%22PO-3%22%5D") == 3
    assert d.count_packages_in_url("https://x/mmsos/orders.html") == 0
    assert d.count_packages_in_url("") == 0


def test_url_same_target_reuse_contract(cfg):
    """复用判据（TB-032）：带参数的页（批量页）必须参数一致；**不带参数的固定页按路径复用**。

    旧实现里 want 无 query 时恒 False ⇒ 每次动作都新开标签页，而本机对新页枚举有延迟 →
    record 报「后台开页后仍未枚举到该页」并堆积重复页（2026-09-15 实测）。
    """
    batch_a = cfg["shipping_url"] + "?parent_order_sn_list=%5B%22PO-A%22%5D"
    batch_b = cfg["shipping_url"] + "?parent_order_sn_list=%5B%22PO-B%22%5D"
    assert d._url_same_target(batch_a, batch_a) is True
    assert d._url_same_target(batch_a, batch_b) is False          # 不同单 → 绝不复用（防串单/空页）
    assert d._url_same_target("", cfg["shipment_url"]) is False
    assert d._url_same_target(cfg["shipment_url"] + "?is_back=1", cfg["shipment_url"]) is True
    assert d._url_same_target(cfg["orders_url"] + "?queryType=4", cfg["orders_url"]) is True
    # 路径不同 → 不复用（订单列表 ≠ 在线下单页）
    assert d._url_same_target(cfg["orders_url"], cfg["shipment_url"]) is False


def test_pick_reuse_index(cfg):
    """--reuse-page 选择器：数字=第 N 个 online-shipping 页；否则 URL 子串匹配。"""
    urls = [cfg["orders_url"],
            "https://x/online-shipping.html?parent_order_sn_list=%5B%22PO-A%22%5D",
            "https://x/online-shipping.html?parent_order_sn_list=%5B%22PO-B%22%5D"]
    assert d.pick_reuse_index(urls, "1") == 1
    assert d.pick_reuse_index(urls, "2") == 2
    assert d.pick_reuse_index(urls, "3") == -1          # 只有 2 个批量页
    assert d.pick_reuse_index(urls, "PO-B") == 2
    assert d.pick_reuse_index(urls, "PO-Z") == -1
    assert d.pick_reuse_index([cfg["orders_url"]], "1") == -1
    assert d.pick_reuse_index(urls, "") == -1


def test_pick_page_size_option(cfg):
    """「每页」口径（用户 2026-09-17 TB-044：「直接拉到500，你不需要判断别的」）：
    **一律取 ≤cap 的最大选项**，不看本批多少单（旧口径"够用就好"已废止）。"""
    opts = [{"text": "5"}, {"text": "10"}, {"text": "20"}, {"text": "50"},
            {"text": "100"}, {"text": "500"}]
    assert d.pick_page_size_option(opts, 31) == "500"     # 有 500 就取 500（不再取 50）
    assert d.pick_page_size_option(opts, 20) == "500"     # 不再"刚好够用就不放大"
    assert d.pick_page_size_option(opts, 1) == "500"
    assert d.pick_page_size_option(opts, 500) == "500"
    assert d.pick_page_size_option(opts, 1000) == "500"   # 本批比 cap 还多：仍取最大值，由页大小闸门另判
    assert d.pick_page_size_option(opts, 31, cap=100) == "100"   # 上限生效
    assert d.pick_page_size_option(opts, 31, cap=30) == "20"     # 无 ≤cap 的可选项 → 取可选项最大值
    # 读不到选项 / 非数字选项 → None（不许猜、不许写死 20/500）
    assert d.pick_page_size_option([], 31) is None
    assert d.pick_page_size_option([{"text": "全部"}, {"text": ""}], 31) is None
    assert d.pick_page_size_option(None, 31) is None


def test_config_declares_batch_page_size(cfg):
    """口径必须显式声明「每页条数上限」（缺了就等于没这条规则，回退页面默认 20 → 部分下单）。"""
    assert isinstance(cfg.get("batch", {}).get("page_size"), int)
    assert cfg["batch"]["page_size"] >= 20


def test_svc_missing_placeholder_text():
    """物流服务「视为没有」的判据（用户 2026-10-09）：
    「如果弹窗是**无物流渠道**的字样，那你就要跳到对应的订单改成 Kansas 仓，
      物流也会刷新成空，你要重新选择下拉框的第一个物流」。
    ⇒ 占位文案（无物流渠道/无可用/不支持…）必须**也算没有**，否则不去做 Kansas 兜底。"""
    for v in ("", "   ", "-", "--", "无物流渠道", "无可用物流", "不支持该地址", "请选择", "暂无可用物流"):
        assert d.svc_missing(v), "应判为无服务: %r" % v
    for v in ("GOFO, Standard", "UPS, Ground saver", "UniUni, Standard",
              "DHL eCommerce, 0024, SwiftX(PICKUP)", "SwiftX, Standard", "SpeedX, standard"):
        assert not d.svc_missing(v), "正常服务名不该被判无服务: %r" % v


def test_waybill_token_judge():
    """运单号判据（TB-027，2026-09-15 实测）：**不是所有承运商都是 UPS**，旧正则只认 1Z 会误判缺号。"""
    for good in ("1Z16E50BYW81674507", "GFUS01072983618176", "SWX326200000173528347",
                 "SPXABE008892609150017649", "YWE00001541157130"):
        assert d.is_waybill_token(good) is True, good
    for bad in ("", "GOFO", "UPS", "Standard", "12345678901", "ABCDEFGHIJK",
                "PK-5080640743014533163", "PO-211-05995150561911035", "TK-5080643776020533163"):
        assert d.is_waybill_token(bad) is False, bad


def test_find_waybill_prefers_head():
    """行文本里运单号总在该行最前面 → 优先只在「包裹号」标签之前找，避免抓到行尾别的字母数字串。"""
    row = ("GFUS01072983618176 包裹号： PK-5080604504227973163 订单号： PO-211-16625656382073206 "
           "×1 重量：1.10lb 体积：12.30×9.30×3.00in 加州三仓 GOFO, Standard 预估$3.28")
    assert d.find_waybill(row, prefer_before="包裹号") == "GFUS01072983618176"
    assert d.find_waybill("1Z16E50BYW69070281 订单号： PO-2", prefer_before="包裹号") == "1Z16E50BYW69070281"
    assert d.find_waybill("无号可找", prefer_before="包裹号") == ""
    assert d.find_waybill("PK-5080640743014533163 订单号： PO-2", prefer_before="包裹号") == ""


def test_parse_package_rows_non_ups_carriers():
    """TB-027：GOFO/SwiftX/SpeedX/YANWEN 的运单号以前被判「缺运单号」（false negative）→ 完成指标误报未完成。"""
    rows = ["GFUS01072983618176 包裹号： PK-5080604504227973163 订单号： PO-211-16625656382073206 ×1",
            "SWX326200000173528347 包裹号： PK-5080625979064453163 订单号： PO-211-16704996229752927 ×1",
            "SPXABE008892609150017649 包裹号： PK-5080621952532613163 订单号： PO-211-05638023015033291 ×1",
            "YWE00001541157130 包裹号： PK-5080635374305413163 订单号： PO-211-06123479255670268 ×1"]
    pk = d.parse_package_rows(rows)
    assert [x["waybill"] for x in pk] == ["GFUS01072983618176", "SWX326200000173528347",
                                          "SPXABE008892609150017649", "YWE00001541157130"]
    assert all(x["package"].startswith("PK-") for x in pk)
    ok, missing, detail = d.check_packages("TK-5080643776020533163", pk)
    assert ok is True and missing == [] and len(detail) == 4


def test_parse_packages_from_text_real_page_shape():
    """整页文本兜底（TB-027）：实测行样『<运单号> 包裹号：PK-… 订单号：PO-…』必须**逐包对上、不许错位**。"""
    text = ("16 个包裹下单成功 物流单号 关联订单信息 子订单信息 重量及体积信息 发货仓库 物流服务 上门取件服务 "
            "1Z16E50BYW81674507 包裹号： PK-5080640743014533163 订单号： PO-211-05995150561911035 ×1 "
            "重量：1.10lb 体积：12.30×9.30×3.00in 加州三仓 UPS, Ground saver 预估$6.28; 2-6 个工作日送达 "
            "GFUS01072983618816 包裹号： PK-5080611215114373163 订单号： PO-211-06010643610231132 ×1 "
            "重量：1.10lb 加州三仓 GOFO, Standard 预估$3.28; 2-5 个工作日送达 "
            "SWX326200000173528347 包裹号： PK-5080625979064453163 订单号： PO-211-16704996229752927 ×1 "
            "重量：1.10lb 加州三仓 SwiftX, Standard 预估$2.70")
    pk = d.parse_packages_from_text(text)
    assert [(x["waybill"], x["package"], x["order_no"]) for x in pk] == [
        ("1Z16E50BYW81674507", "PK-5080640743014533163", "PO-211-05995150561911035"),
        ("GFUS01072983618816", "PK-5080611215114373163", "PO-211-06010643610231132"),
        ("SWX326200000173528347", "PK-5080625979064453163", "PO-211-16704996229752927")]
    # 表头/标题里的裸 PK 号不算包裹行（必须先有「包裹号：」标签）
    assert d.parse_packages_from_text("16 个包裹下单成功 物流单号 关联订单信息 PK-5080640743014533163") == []
    assert d.parse_packages_from_text("") == []


def test_parse_packages_from_text_keeps_row_without_waybill():
    """认不出运单号也要**留下这行**（缺号如实报），不能整行丢掉装成"没有包裹"。"""
    pk = d.parse_packages_from_text("未知号 XYZ 包裹号： PK-5080640743014533163 订单号： PO-211-05995150561911035")
    assert len(pk) == 1 and pk[0]["waybill"] == "" and pk[0]["package"] == "PK-5080640743014533163"
    ok, missing, _ = d.check_packages("TK-1", pk)
    assert ok is False and any("第 1 个包裹缺运单号" in m for m in missing)


def test_config_declares_shipment_url(cfg):
    """TB-028：`record`/`record_detail` 的第二入口（「在线下单」页横幅）依赖本 URL，缺了就等于没这条退路。"""
    assert cfg.get("shipment_url", "").endswith("/mmsos/online-shipment.html")


def test_count_live_clients_dedupes_self():
    """占用守卫判据（TB-031）：一条 loopback 连接在 netstat 里是两行 → 旧实现 1 个客户端算成 2 个，
    叠加"守卫自己的探测进程也占一条" ⇒ 明明没别的爬虫也会 HARD_STOP（record 动作实测连吃两次假占用）。"""
    one = ("  TCP    127.0.0.1:10020        127.0.0.1:65132        ESTABLISHED     22640\n"
           "  TCP    127.0.0.1:65132        127.0.0.1:10020        ESTABLISHED     37176\n")
    assert d.count_live_clients(one, 10020) == 1
    two = one + ("  TCP    127.0.0.1:10020        127.0.0.1:65200        ESTABLISHED     22640\n"
                 "  TCP    127.0.0.1:65200        127.0.0.1:10020        ESTABLISHED     999\n")
    assert d.count_live_clients(two, 10020) == 2
    # 没有 ESTABLISHED（只有 LISTENING/TIME_WAIT）→ 0
    assert d.count_live_clients("  TCP    127.0.0.1:10020        0.0.0.0:0              LISTENING       22640\n", 10020) == 0
    assert d.count_live_clients("", 10020) == 0
    # 端口精确匹配：:1002 不算 :10020
    assert d.count_live_clients("  TCP    127.0.0.1:1002    127.0.0.1:1   ESTABLISHED  1\n", 10020) == 0


def test_auth_lost_hint_only_on_auth_pages():
    """TB-030：登录态失效的提示只认**认证/授权页**。

    窗口里常驻着 `seller.kuajingmaihuo.com/login` 那个无关扫码登录 tab（TLA-002/TLA-014），
    把它当"登录态失效"会给出误判提示（2026-09-15 实测踩到），比不给提示更坏。
    """
    assert "登录态失效" in d.auth_lost_hint_from_urls(
        ["https://agentseller-us.temu.com/auth/authentication?redirectUrl=https%3A%2F%2Fagentseller-us.temu.com%2Fmmsos%2Forders.html"])
    assert "登录态失效" in d.auth_lost_hint_from_urls(
        ["https://seller.kuajingmaihuo.com/settle/seller-login?redirectUrl=x"])
    assert d.auth_lost_hint_from_urls(["https://seller.kuajingmaihuo.com/login?redirectUrl=%2F"]) == ""
    assert d.auth_lost_hint_from_urls(["https://agentseller-us.temu.com/mmsos/orders.html"]) == ""
    assert d.auth_lost_hint_from_urls([]) == ""


# ── 遮挡弹窗检测（2026-09-19 用户：「你要能随时检测弹窗」）─────────────────────────
# 现场（BEL 订单页）：全屏遮罩 `.MDL_mask_123`(z-index 1000) + `[data-testid="beast-core-modal"]`，
# 内容是「以下待办任务需要尽快处理」；关闭键 `[data-testid="beast-core-icon-close"]`（**无 class**）。
# 危害：遮罩盖住页面 ⇒ 表面症状是 `no-input`／点不到按钮，极易误诊成"页面没就绪"。
def test_popup_classifier_known_vs_unknown():
    """已知提示类 → 可关；未知弹窗 → 停（绝不连点硬闯）。"""
    assert d.classify_popup("以下待办任务需要尽快处理") == "dismiss"
    assert d.classify_popup("请完成安全验证") == "unknown"
    assert d.classify_popup("") == "unknown"
    assert d.classify_popup(None) == "unknown"


def test_popup_probe_keys_on_stable_testids():
    """弹窗与关闭键必须按 **testid** 认（关闭键实测无 class，只能靠 testid）。"""
    assert 'data-testid="beast-core-modal"' in d._JS_POPUP_PROBE
    assert 'data-testid="beast-core-icon-close"' in d._JS_POPUP_PROBE
    assert "MDL_outerWrapper_" in d._JS_POPUP_PROBE     # 兜底：testid 变了还能按类名认
    assert "MDL_mask_" in d._JS_POPUP_PROBE


def test_popup_probe_returns_whole_text_not_first_line():
    """回归（2026-09-19 实测踩到）：`innerText` 取**第一行**只拿到「以下」→ 已知弹窗被判成未知 →
    起点闸门误拦。必须取**整段文本**、判据做包含匹配。"""
    import json as _json
    assert "const text = " in d._JS_POPUP_PROBE and "replace(/\\s+/g, ' ')" in d._JS_POPUP_PROBE
    # 已知弹窗：标题被截断成「以下」，但整段文本含完整标记 → 仍须可关
    probe = _json.dumps({"present": True,
                         "text": "以下待办任务需要尽快处理 超时或者不处理可能面临订单取消",
                         "title": "以下", "hasClose": True, "closeAt": [757, 296], "maskOnly": False})
    st = _DrvStub(probe)
    calls = {"n": 0}

    def js(page, script, arg=None, tries=1):
        calls["n"] += 1
        return probe if calls["n"] == 1 else _json.dumps({"present": False})

    st._js = js
    assert d.BuylabelDriver.dismiss_popups(st, None, why="t") == ["以下待办任务需要尽快处理 超时或者不处理可能面临订单取消"]
    assert st.clicked == [(757, 296)]


class _DrvStub:
    """只给 dismiss_popups 用的最小桩（不连浏览器）。

    2026-09-21 两处补：
      · `cfg`：弹窗动作已改成**读数据表**（`data/temu_buylabel_popups.json`），driver 要靠
        `self.cfg` 找到那张表，所以桩必须提供这个钩子。
      · **默认指向临时登记表**：未登记弹窗会被 driver 自动登记写回表里，如果桩用域内缺省表，
        跑一次测试就会往**生产登记表**里塞测试弹窗（真踩过：把「批量填充物流服务」
        「请完成安全验证」写进了生产 unknown[]）。临时表内容＝真表的 popups 段（判据不变）。
    """

    def __init__(self, probe, cfg=None):
        self._probe = probe
        self.clicked = []
        self.shots = []
        if cfg is None:
            fd, p = tempfile.mkstemp(suffix=".json", prefix="popups_test_")
            os.close(fd)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"popups": _REAL_POPUPS, "unknown": []}, f, ensure_ascii=False)
            cfg = {"popups_file": p}
        self.cfg = cfg

    def _js(self, page, script, arg=None, tries=1):
        return self._probe

    def shot(self, page, name):
        self.shots.append(name)

    def log(self, msg):
        pass

    def cdp_click(self, page, x, y):
        self.clicked.append((x, y))

    def wait(self, key, factor=1.0):
        pass


def test_dismiss_popups_known_clicks_close():
    """已知提示 → 按关闭键坐标点一下；返回关掉的标题。"""
    import json as _json
    probe = _json.dumps({"present": True, "title": "以下待办任务需要尽快处理",
                         "hasClose": True, "closeAt": [757, 296], "maskOnly": False})
    st = _DrvStub(probe)
    # 第一次探测到弹窗、第二次探测不到 → 关一次后收工
    calls = {"n": 0}

    def js(page, script, arg=None, tries=1):
        calls["n"] += 1
        return probe if calls["n"] == 1 else _json.dumps({"present": False})

    st._js = js
    got = d.BuylabelDriver.dismiss_popups(st, None, why="test")
    assert got == ["以下待办任务需要尽快处理"]
    assert st.clicked == [(757, 296)], "必须点关闭键坐标"


def test_dismiss_popups_unknown_default_lets_it_pass():
    """**默认（非严格）不许拦**：流程自己会打开正常模态框（如「批量填充物流服务」），
    一律 HARD STOP 会把自己的流程打断（2026-09-19 实测踩到）。默认只记不点、不抛。"""
    import json as _json
    probe = _json.dumps({"present": True, "title": "批量填充物流服务",
                         "hasClose": False, "closeAt": None, "maskOnly": False})
    st = _DrvStub(probe)
    got = d.BuylabelDriver.dismiss_popups(st, None, why="test")
    assert st.clicked == [], "未知弹窗绝不能点"
    assert got == ["批量填充物流服务"], "只记录、放过"
    assert st.shots == ["_popup_seen_unknown"], "留痕即可"


def test_dismiss_popups_unknown_strict_hard_stops_without_clicking():
    """**严格模式**（只用于"页面刚就绪、不该有弹窗"的起点检查）→ 抛错 + 截图，且一次都不点。"""
    import json as _json
    import pytest as _pytest
    probe = _json.dumps({"present": True, "title": "请完成安全验证",
                         "hasClose": True, "closeAt": [10, 10], "maskOnly": False})
    st = _DrvStub(probe)
    with _pytest.raises(RuntimeError) as ei:
        d.BuylabelDriver.dismiss_popups(st, None, why="test", strict=True)
    assert "未知弹窗" in str(ei.value)
    assert st.clicked == [], "未知弹窗绝不能点"
    assert st.shots == ["_popup_unknown"], "必须截图取证"


# ── 下单链弹窗判据（2026-09-19：BHY 63 单白跑一轮的教训）────────────────────────────
def test_modal_present_is_rect_based_not_offsetparent():
    """`_JS_MODAL_PRESENT` **不许用 `offsetParent !== null`** 判可见。

    TEMU 模态外层是 `position: fixed`，而 fixed 元素 `offsetParent` 恒为 null（TB-039 同坑）
    ⇒ 旧判据把遮罩/外壳整层滤掉。必须用 rect 判（getClientRects / getBoundingClientRect）。
    """
    js = d._JS_MODAL_PRESENT
    assert ".offsetParent" not in js, "固定定位元素的 offsetParent 恒为 null，会假阴性（注释里可提，代码里不许用）"
    assert "getClientRects" in js and "getBoundingClientRect" in js
    # 诊断用：认不出来时要能把弹窗原文打出来
    assert hasattr(d, "_JS_MODAL_TEXT")
    assert "innerText" in d._JS_MODAL_TEXT


def test_place_has_explicit_terminal_gate():
    """`place()` 末尾必须有**显式终态闸门**：「确认下单？」框还在 ⇒ 抛错，不许静默成功。

    旧行为静默返回 `{tab_closed:false}` 让外层当成功；而 TB-026 使 `is_closed()` 恒 False
    ⇒ `tab_closed` 不可作判据 ⇒ `all` exit 0 却一单没下（2026-09-19 BHY 63 单实测）。
    """
    import inspect
    src = inspect.getsource(d.BuylabelDriver.place)
    assert "05_still_confirm_dialog" in src, "终态失败必须截图取证"
    assert "raise RuntimeError" in src, "终态失败必须抛错（不得静默）"
    assert src.find("05_still_confirm_dialog") < src.find("批量页%s"), "闸门要排在「批量页仍开着」那句之前"


# ── 找批量页的判据（2026-09-19：TEMU 改用 big_array_key 开页，URL 里没有单号）──────────
class _P:
    def __init__(self, url):
        self.url = url


class _Ctx:
    def __init__(self, pages):
        self.pages = pages


class _DrvPages:
    ctx = None


def test_find_new_batch_page_accepts_platform_bigarraykey():
    """回归：平台用 `big_array_key=…` 开页（**URL 无单号**）必须被认出来。

    旧判据要求"URL 里含目标单号" ⇒ 认不出平台开的页 ⇒ 回落自拼 URL 的页 ⇒
    那张页提交不被平台接受 ⇒ 一路"成功"却不下单（静默无效）。
    """
    drv = _DrvPages()
    drv.ctx = _Ctx([_P("https://x/mmsos/orders.html"),
                    _P("https://x/mmsos/online-shipping.html?big_array_key=abc&open_type=OnlineShip")])
    got = d.BuylabelDriver._find_new_batch_page(drv, set(), ["PO-1"], timeout_s=1)
    assert got is not None and "big_array_key" in got.url, "平台开的页必须认出来"


def test_find_new_batch_page_prefers_platform_page_and_skips_old():
    """两张新页同时在 → **平台开的（无单号/带 key）优先**；点之前就存在的页不算新页。"""
    drv = _DrvPages()
    drv.ctx = _Ctx([_P("https://x/mmsos/online-shipping.html?parent_order_sn_list=%5B%22PO-1%22%5D"),
                    _P("https://x/mmsos/online-shipping.html?big_array_key=abc&open_type=OnlineShip")])
    got = d.BuylabelDriver._find_new_batch_page(drv, set(), ["PO-1"], timeout_s=1)
    assert "big_array_key" in got.url, "平台开的页要优先于自拼 URL 的页"
    old = "https://x/mmsos/online-shipping.html?big_array_key=old"
    drv.ctx = _Ctx([_P(old)])
    assert d.BuylabelDriver._find_new_batch_page(drv, {old}, ["PO-1"], timeout_s=0) is None


def test_judge_filled_accepts_platform_bigarraykey_url():
    """闸门不得因"URL 不含单号"拒单（平台 `big_array_key` 开页时 URL 本就没单号）；
    但"URL 带了单号却不全"（真可能串单）仍必须拒。"""
    state = {"head": "63", "weights": ["1.10"], "picks": ["加州三仓", "GOFO, Standard"],
             "confirm_btn": True}
    orders = ["PO-211-1", "PO-211-2"]
    ok, why = d.judge_filled(
        state, orders, "https://x/mmsos/online-shipping.html?big_array_key=abc&open_type=OnlineShip")
    assert ok is True, why
    ok, why = d.judge_filled(
        state, orders, "https://x/mmsos/online-shipping.html?parent_order_sn_list=%5B%22PO-211-1%22%5D")
    assert ok is False and any("串单" in w for w in why), why


# ── 2026-09-24 no-input 事故：页面没就位时要点名真因 + 留图（原来只有一句 no-input）──────
class _PGoto(_P):
    """带 goto / wait_for_load_state 的假页（`_orders_page_ready` 会调它们）。"""

    def __init__(self, url, after_goto=None):
        super().__init__(url)
        self._after = after_goto
        self.goto_calls = []

    def goto(self, url, **kw):
        self.goto_calls.append(url)
        if self._after is not None:
            self.url = self._after

    def wait_for_load_state(self, *a, **kw):
        return None


def test_orders_page_ready_accepts_a_healthy_orders_page():
    """正常订单页不许被新判据误伤（不能因为"多了一层检查"就把好页也拦下）。"""
    _cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    stub = _DrvStub("x")
    stub.cfg = {"orders_url": _cfg["orders_url"], "_store": "BHY"}
    stub.dismiss_popups = lambda *a, **k: None
    called = []
    stub.close_record_drawer = lambda page, why="": called.append(why) or "absent"
    page = _PGoto(_cfg["orders_url"])
    stub.orders_page = lambda page=page: page
    assert d.BuylabelDriver._orders_page_ready(stub) is page
    assert called, "读列表前必须先关「在线下单记录」抽屉（2026-09-30 读串行真因）"
    assert stub.shots == [], "正常页不该留失败图"


def test_orders_page_ready_points_at_auth_page():
    """落到认证页 → 直接报「停在授权页 + 该跑哪条授权命令」并落图，不许放到下游去报 no-input。

    场景＝真实事故：窗口刚开、页面停在非订单页 → `_orders_page_ready` 导航到订单页 → 服务端把它
    重定向到认证页（假页用 `after_goto` 模拟这一步）。
    """
    _cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    stub = _DrvStub("x")
    stub.cfg = {"orders_url": _cfg["orders_url"], "_store": "BEL"}
    page = _PGoto("https://agentseller-us.temu.com/",
                  after_goto="https://agentseller-us.temu.com/auth/authentication?redirectUrl=x")
    stub.orders_page = lambda: page
    with pytest.raises(RuntimeError) as ei:
        d.BuylabelDriver._orders_page_ready(stub)
    msg = str(ei.value)
    assert "授权页" in msg and "auth/authentication" in msg, msg
    assert "run_temu_auth.py" in msg and "--store BEL" in msg, "要点名该跑哪条授权命令"
    assert "00_auth_required" in stub.shots, "必须在失败现场留图"


def test_search_orders_failure_keeps_url_and_evidence(monkeypatch):
    """`订单号筛选框填不进去` 这条错误必须带上页面 URL + 授权页提示，并留图（可事后定性）。"""
    monkeypatch.setattr(d.time, "sleep", lambda *_: None)     # 别为测试等 4.5s
    stub = _DrvStub("ok")
    stub.cfg = {"_store": "BEL"}
    stub.dismiss_popups = lambda page, why="", max_rounds=3, strict=False: None
    stub.clear_blockers = lambda page: None
    page = _PGoto("https://agentseller-us.temu.com/mmsos/orders.html")

    def _js(page, script, arg=None, tries=1):
        return "ok" if script is d._JS_CLICK_TAB else "no-input"

    stub._js = _js
    with pytest.raises(RuntimeError) as ei:
        d.BuylabelDriver._search_orders(stub, page, "待发货", ["PO-1"])
    msg = str(ei.value)
    assert "no-input" in msg and "url=https://agentseller-us.temu.com/mmsos/orders.html" in msg, msg
    assert "00_filter_no_input" in stub.shots, "失败时必须落图"


# ── 2026-09-24 用户令「只使用 PIPELINE」：旧整链 `all` 停用 ─────────────────────────
def _entry_src():
    return open(os.path.join(_HERE, "..", "run_temu_buylabel.py"), encoding="utf-8").read()


def test_legacy_all_route_is_blocked_in_entry():
    """`--action all` 必须在**入口**就被拦下（HARD STOP 42），并指向 pipeline。

    为什么堵在入口而不是只改文档：MCP 技能、收件箱指令、看板卡 prompt 三处都曾直接写 `--action all`，
    只在文档里写"请用 pipeline"拦不住任何一条（用户 2026-09-24 令「只使用 PIPELINE」）。
    """
    src = _entry_src()
    i = src.find('if action == "all"')
    assert i > 0, "入口没有拦 `all`"
    seg = src[i:i + 500]
    assert "HARD STOP" in seg and "preflight.HARD_STOP_CODE" in seg, "拦了但没按 HARD STOP 语义退出"
    assert "pipeline" in seg, "拦下时要告诉调用方改走 pipeline"


def test_pipeline_is_the_only_whole_chain_route():
    """整链入口只剩 pipeline；帮助文本里**不许再有可直接抄的 `--action all` 示例**。"""
    src = _entry_src()
    assert "--action pipeline" in src, "帮助文本里没有整链命令"
    txt_doc = src.split("def main", 1)[0]
    bad = [ln.strip() for ln in txt_doc.splitlines()
           if ln.strip().startswith("python ") and "--action all" in ln]
    assert not bad, "帮助文本里还留着可直接抄的旧整链示例：%s" % bad
    assert "停用" in src.split("def main", 1)[1], "入口没写明 all 已停用"
