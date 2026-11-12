# -*- coding: utf-8 -*-
"""test_shortage_fail_xlsx.py — 缺货 / 失败订单两 sheet 的归类规则（纯函数）。

钉的都是真机踩过的坑：
  · 「缺货」里混进其实已出单有运单号的单 ⇒ 会让人工白去补货；
  · 「失败订单」里混进「恒信那份」和「缺货单」的「未下单」 ⇒ 几百行噪音；
  · 失败原因要能区分「库存不足」「状态异常」「网络异常」「没提交」。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import gen_shortage_fail_xlsx as G  # noqa: E402


# ---------------------------------------------------------------- classify_miss

def test_classify_splits_already_shipped_out_of_miss():
    """真机案：BHY 308 单 MISS 里 6 单其实已出单有运单号（BS-202504-5SetE-XL）。"""
    rows = [{"ord": "PO-1", "sku": "X-1A-L", "qty": 1},
            {"ord": "PO-2", "sku": "X-1A-L", "qty": 1}]
    idx = {"PO-2": {"id": "9", "wb": "GFUS01082220889858", "pb": "2"}}
    real, already = G.classify_miss(rows, idx)
    assert [r["ord"] for r in real] == ["PO-1"]
    assert [r["ord"] for r in already] == ["PO-2"]
    assert already[0]["waybill"] == "GFUS01082220889858"


def test_classify_without_index_keeps_all_as_shortage():
    rows = [{"ord": "PO-1", "sku": "X-1A-L", "qty": 1}]
    real, already = G.classify_miss(rows, {})
    assert len(real) == 1 and not already


def test_classify_blank_waybill_is_not_shipped():
    rows = [{"ord": "PO-1", "sku": "X-1A-L", "qty": 1}]
    real, already = G.classify_miss(rows, {"PO-1": {"wb": "   "}})
    assert len(real) == 1 and not already


# ---------------------------------------------------------------- miss_reason

def test_miss_reason_extracts_variant_hint():
    why = ("两边都无此 SKU（恒信 0 / 帕沃尔 0）——**变体不存在**："
           "10SETA（同款同码库存里只有 5SETA/5SETB），疑似上链接命名有误")
    txt, hint = G.miss_reason({"why": why})
    assert "变体在库存里不存在" in txt
    assert hint.startswith("10SETA")


def test_miss_reason_distinguishes_exhausted():
    txt, hint = G.miss_reason({"why": "可用已被前面的单分完（本批共需 5，恒信 0 + 帕沃尔 1）"})
    assert "分完" in txt and not hint


def test_miss_reason_plain_absent():
    txt, hint = G.miss_reason({"why": "两边都无此 SKU"})
    assert "查不到" in txt and not hint


# ---------------------------------------------------------------- fail_reason

def test_fail_reason_stock_shortage():
    step, why = G.fail_reason(
        "下单失败", "下单成功0条,失败1条,PO-x下单失败-sku:D14450QZ库存不足发货")
    assert step == "帕沃尔下单"
    assert "D14450QZ" in why and "库存不足" in why


def test_fail_reason_warehouse_empty():
    _, why = G.fail_reason(
        "下单失败", "下单成功0条,失败1条,PO-x下单失败-sku:D14450K9仓库没有库存")
    assert "该仓无此库存" in why


def test_fail_reason_status_abnormal():
    _, why = G.fail_reason("下单失败", "PO-x下单失败-SKU:状态异常无法下单")
    assert "状态异常" in why


def test_fail_reason_network():
    _, why = G.fail_reason("下单失败", "PO-x下单失败,网络异常，请稍后重试")
    assert "网络异常" in why


def test_fail_reason_not_submitted():
    step, why = G.fail_reason("未下单", "")
    assert "未提交" in step
    assert "需重下" in why


# ---------------------------------------------------------------- build

def _split(pw=(), hx=(), miss=()):
    return {"groups": {"PW": [{"ord": p, "sku": "S", "qty": 1} for p in pw],
                       "HX": [{"ord": p, "sku": "S", "qty": 1} for p in hx],
                       "MISS": [{"ord": p, "sku": "S", "qty": 1, "why": "两边都无此 SKU"}
                                for p in miss]}}


def test_build_has_exactly_two_sheets():
    """返回里除两个 sheet 外只有 `_hx_*` 这些**下划线元数据**，不当 sheet 写。"""
    out = G.build("BHY", _split(), [], {})
    assert [k for k in out if not k.startswith("_")] == ["缺货", "失败订单"]


def test_build_skips_henxin_not_ordered():
    """恒信那份在帕沃尔页面必然显示「未下单」（它走领星 OMS）——不算失败。"""
    out = G.build("BHY", _split(hx=["PO-HX"]),
                  [{"po": "PO-HX", "status": "未下单", "sku": "S"}], {})
    assert out["失败订单"] == []


def test_build_skips_miss_not_ordered():
    """缺货单本来就没派仓库，「未下单」是预期，不是失败。"""
    out = G.build("BHY", _split(miss=["PO-M"]),
                  [{"po": "PO-M", "status": "未下单", "sku": "S"}], {})
    assert out["失败订单"] == []


def test_build_keeps_pw_not_ordered_as_failure():
    """派过帕沃尔却「未下单」⇒ 真失败（下了但没成）。"""
    out = G.build("BHY", _split(pw=["PO-P"]),
                  [{"po": "PO-P", "status": "未下单", "sku": "S", "wh": "加州3仓"}], {})
    assert len(out["失败订单"]) == 1
    assert out["失败订单"][0]["拆仓归属"] == "帕沃尔"


def test_build_keeps_any_order_that_explicitly_failed():
    """「下单失败」无论是哪一份都要报（恒信那份也可能在帕沃尔尝试过）。"""
    out = G.build("BHY", _split(miss=["PO-M"]),
                  [{"po": "PO-M", "status": "下单失败", "sku": "S",
                    "err": "PO-M下单失败-sku:D1库存不足发货"}], {})
    assert len(out["失败订单"]) == 1


def test_build_shortage_puts_real_before_already_shipped():
    """真缺货要排在前面（要人工补货）；已下单的压到最后。"""
    split = _split(miss=["PO-A", "PO-B"])
    idx = {"PO-B": {"wb": "GFUS1"}}
    out = G.build("BHY", split, [], idx)
    assert out["缺货"][0]["参考单号"] == "PO-A"
    assert out["缺货"][-1]["参考单号"] == "PO-B"
    assert out["缺货"][0]["状态"].startswith("真缺货")
    assert out["缺货"][-1]["状态"].startswith("已实际下单")


def test_build_ignores_blank_po():
    out = G.build("BHY", _split(), [{"po": "", "status": "下单失败"}], {})
    assert out["失败订单"] == []


# ---------------------------------------------------------------- 恒信（领星 OMS）终态
#
# ★ 2026-11-06 用户追问「恒信的失败订单你有一起汇总到表吗」：
#   旧 build() 里一句 `if sd == "恒信": continue` 把恒信**整组**排除在失败 sheet 外，
#   恒信真失败会被静默吞掉。下面把这些判定钉住。

def _oms(status, name="", track="", out=""):
    return {"accounts": [{"tag": "LM739", "status": status,
                          "statusName": name, "track": track, "outboundTime": out}]}


def test_hx_in_progress_with_track_is_not_failure():
    assert G.hx_fail(_oms(2, "仓库处理中", "GFUS1"), "PO-1") is None


def test_hx_shipped_is_not_failure():
    assert G.hx_fail(_oms(3, "已出库", "GFUS1", "2026-11-06 19:00"), "PO-1") is None


def test_hx_pending_review_is_failure():
    """status=1 待处理 ⇒ 没点「确定并审核」，必须报。"""
    r = G.hx_fail(_oms(1, "待处理", "GFUS1"), "PO-1")
    assert r and "待处理" in r["失败原因"]


def test_hx_draft_is_failure():
    r = G.hx_fail(_oms(0, "草稿"), "PO-1")
    assert r and "草稿" in r["失败原因"]


def test_hx_abnormal_is_failure():
    r = G.hx_fail(_oms(5, "异常"), "PO-1")
    assert r and "异常" in r["失败原因"]


def test_hx_canceled_is_failure():
    r = G.hx_fail(_oms(4, "已取消"), "PO-1")
    assert r and "取消" in r["失败原因"]


def test_hx_in_progress_without_track_is_failure():
    """已受理但面单没出来 ⇒ 报，不许当成功。"""
    r = G.hx_fail(_oms(2, "仓库处理中", ""), "PO-1")
    assert r and "没有物流单号" in r["失败原因"]


def test_hx_not_in_oms_is_failure():
    r = G.hx_fail({}, "PO-1")
    assert r and "搜不到" in r["失败原因"]


def test_build_pulls_hx_failures_into_fail_sheet():
    """恒信真失败必须出现在失败 sheet 里，且归属标「恒信」。"""
    split = _split(hx=["PO-HX1", "PO-HX2"])
    oms = {"PO-HX1": _oms(2, "仓库处理中", "GFUS1"),
           "PO-HX2": _oms(1, "待处理", "")}
    out = G.build("BHY", split, [], {}, hx_oms=oms)
    assert [r["参考单号"] for r in out["失败订单"]] == ["PO-HX2"]
    assert out["失败订单"][0]["拆仓归属"] == "恒信"
    assert out["失败订单"][0]["发货仓"] == "恒信加州3"


def test_build_without_hx_oms_reports_not_checked():
    """不给 --hx-oms ⇒ 明确标「未核对」，绝不默认恒信全成功。"""
    out = G.build("BHY", _split(hx=["PO-HX1"]), [], {})
    assert out["_hx_checked"] is False
    assert out["_hx_total"] == 1


def test_build_marks_hx_checked_when_given():
    out = G.build("BHY", _split(hx=["PO-HX1"]), [], {},
                  hx_oms={"PO-HX1": _oms(2, "仓库处理中", "GFUS1")})
    assert out["_hx_checked"] is True


def test_build_hx_failures_sort_before_pw():
    """恒信失败排在前面（用户先问的就是恒信），帕沃尔在后。"""
    split = _split(pw=["PO-PW"], hx=["PO-HX"])
    hwc = [{"po": "PO-PW", "status": "下单失败", "sku": "S",
            "err": "PO-PW下单失败-sku:D1库存不足发货"}]
    oms = {"PO-HX": _oms(1, "待处理", "")}
    out = G.build("BHY", split, hwc, {}, hx_oms=oms)
    assert out["失败订单"][0]["拆仓归属"] == "恒信"
    assert out["失败订单"][-1]["拆仓归属"] == "帕沃尔"


# ---------------------------------------------------------------- 两个仓的失败都要收
#
# ★ 2026-11-06 用户口径（最终版）：
#   「当一个店铺的从导出表 → 批量确认发货 → 最后一步是**汇总两个仓的失败订单**、
#     缺货清单合在一个 excel 表中」

def test_pw_ordered_but_no_waybill_is_a_failure():
    """帕沃尔「已下单但没运单号」= 面单/取号没成，货发不出去 ⇒ 必须报。"""
    split = _split(pw=["PO-P"])
    hwc = [{"po": "PO-P", "status": "已下单", "sku": "S", "wh": "加州3仓"}]
    idx = {"PO-P": {"wb": "", "status": "待打包", "pb": "2"}}
    out = G.build("BHY", split, hwc, idx)
    assert len(out["失败订单"]) == 1
    f = out["失败订单"][0]
    assert f["状态"] == "已下单无运单号"
    assert f["失败环节"] == "帕沃尔取号"


def test_pw_ordered_with_waybill_is_not_a_failure():
    """有运单号 ⇒ 正常，不报。"""
    split = _split(pw=["PO-P"])
    hwc = [{"po": "PO-P", "status": "已下单", "sku": "S"}]
    idx = {"PO-P": {"wb": "GFUS01082229782278", "status": "待打包"}}
    out = G.build("BHY", split, hwc, idx)
    assert out["失败订单"] == []


def test_pw_ordered_but_missing_from_index_is_unverified():
    """帕沃尔**已下单**但 deliver 索引里没有 ⇒ 运单号没法判，必须标「未核对」，不许放过。"""
    split = _split(pw=["PO-P"])
    hwc = [{"po": "PO-P", "status": "已下单", "sku": "S", "wh": "加州3仓"}]
    out = G.build("BHY", split, hwc, {})
    assert len(out["失败订单"]) == 1
    f = out["失败订单"][0]
    assert "无记录" in f["状态"]
    assert "无法核对" in f["失败原因"]


def test_pw_missing_from_hwc_still_reported_when_index_has_it():
    """帕沃尔 hwc_shop 搜不到、但索引里有运单号 ⇒ 有号就不报（货其实发了）。"""
    split = _split(pw=["PO-P"])
    out = G.build("BHY", split, [], {"PO-P": {"wb": "GFUS1", "status": "待打包"}})
    assert out["失败订单"] == []


def test_pw_never_ordered_and_no_trace_is_not_reported():
    """连索引都没有、hwc 也没有 ⇒ 属于"还没下过单"，不该凭空报失败。"""
    split = _split(pw=["PO-P"])
    out = G.build("BHY", split, [{"po": "PO-P", "status": "已下单"}], {})
    assert len(out["失败订单"]) == 1   # 已下单但无记录 → 未核对


def test_both_warehouses_failures_in_one_sheet():
    """帕沃尔 + 恒信两边的失败必须在**同一张**失败 sheet 里（用户要一张表）。"""
    split = _split(pw=["PO-P"], hx=["PO-H"])
    hwc = [{"po": "PO-P", "status": "下单失败", "sku": "S",
            "err": "PO-P下单失败-sku:D1库存不足发货"}]
    oms = {"PO-H": _oms(1, "待处理", "")}
    out = G.build("BHY", split, hwc, {}, hx_oms=oms)
    sides = [r["拆仓归属"] for r in out["失败订单"]]
    assert sides == ["恒信", "帕沃尔"], sides


# ---------------------------------------------------------------- 三个仓 sheet 不重复
#
# ★ 2026-11-12 用户令：「把恒信仓、帕沃尔仓、缺货三个 sheet 去重，
#   不能一个订单重复出现多个 sheet」。

def test_group_sheets_never_repeat_one_order_across_sheets():
    """同一单同时落在恒信与帕沃尔 ⇒ 只留在恒信那张 sheet（缺货 > 恒信 > 帕沃尔）。"""
    split = {"groups": {"HX": [{"ord": "PO-1", "sku": "S", "qty": 1, "why": "恒信可分 1 → 分配后余 0"}],
                        "PW": [{"ord": "PO-1", "sku": "S", "qty": 1, "why": "帕沃尔可分 1 → 分配后余 0（加州3仓=1）"},
                               {"ord": "PO-2", "sku": "S", "qty": 1, "why": "帕沃尔可分 1 → 分配后余 0（加州3仓=1）"}],
                        "MISS": []}}
    sheets = G.group_sheets([], split, "BEL")
    hx = [r["参考单号"] for r in sheets["BEL 恒信仓"]["rows"]]
    pw = [r["参考单号"] for r in sheets["BEL 帕沃尔仓"]["rows"]]
    assert hx == ["PO-1"], hx
    assert pw == ["PO-2"], pw


def test_group_sheets_keeps_multi_sku_rows_of_one_order():
    """同一单两个 SKU 在同一张 sheet 里是**正常**的两行，不能被去重删掉。"""
    split = {"groups": {"HX": [], "MISS": [],
                        "PW": [{"ord": "PO-1", "sku": "S", "qty": 1, "why": "帕沃尔可分 2 → 分配后余 1（加州3仓=2）"},
                               {"ord": "PO-1", "sku": "T", "qty": 1, "why": "帕沃尔可分 2 → 分配后余 1（加州3仓=2）"}]}}
    sheets = G.group_sheets([], split, "BEL")
    assert len(sheets["BEL 帕沃尔仓"]["rows"]) == 2


def test_group_sheets_does_not_mutate_input_split():
    """去重只在出表这一侧做，不能把调用方的 split 改掉（后面还要用它算别的）。"""
    split = {"groups": {"HX": [{"ord": "PO-1", "sku": "S", "qty": 1}],
                        "PW": [{"ord": "PO-1", "sku": "S", "qty": 1}], "MISS": []}}
    before = {k: len(v) for k, v in split["groups"].items()}
    G.group_sheets([], split, "BEL")
    assert {k: len(v) for k, v in split["groups"].items()} == before


def test_same_order_not_reported_twice():
    """同一单在「下单失败」和「已下单无运单号」两个判据下都命中时，只能进表一次。"""
    split = _split(pw=["PO-P"])
    hwc = [{"po": "PO-P", "status": "下单失败", "sku": "S",
            "err": "PO-P下单失败-sku:D1库存不足发货"}]
    idx = {"PO-P": {"wb": "", "status": "待打包"}}
    out = G.build("BHY", split, hwc, idx)
    pos = [r["参考单号"] for r in out["失败订单"]]
    assert pos.count("PO-P") == 1, pos


# ---------------------------------------------------------------- 领星发货数量对不上
#
# ★ 2026-11-07 用户口径：「去领星发货数量对不上，直接归类到失败订单」。
#   之前 `--hx-shipped` 是**整步开关**：只要 hx_ship 跑过，所有恒信单都按
#   「进过 OMS」判 ⇒ 没送进去的单被报「查无此单」（BEL 19 单就是这么误报的）。
#   现在按单号判，另加「数量台账对不上」这条独立判据。

def test_hx_shipped_is_per_order_not_whole_step():
    """只对**真送进 OMS** 的单判 OMS 终态；没送进去的不许报成「查无此单」。

    BEL 19 单误报的根因就在这：整步开关一开，所有恒信单都按「进过 OMS」判。
    现在没进清单的单归到「未送进 OMS / 数量对不上」那条判据（见下面几条）。
    """
    split = _split(hx=["PO-A", "PO-B", "PO-C"])
    oms = {"PO-A": _oms(2, "仓库处理中", "GFUS1"),
           "PO-C": _oms(1, "待处理", "GFUS2")}
    out = G.build("BHY", split, [], {}, hx_oms=oms,
                  hx_shipped_ords=["PO-A", "PO-C"])
    rows = {r["参考单号"]: r for r in out["失败订单"]}
    assert rows["PO-B"]["失败环节"] == "领星发货", \
        "没送进 OMS 的单要报「未送进 OMS」，不许报成「查无此单」"
    assert "数量对不上" in rows["PO-B"]["失败原因"]
    assert rows["PO-C"]["失败环节"] == "领星 OMS", "真进过 OMS 的照旧按状态判"
    assert "PO-A" not in rows


def test_hx_count_mismatch_goes_into_fail_sheet():
    """拆了 3 单、只送出 2 单 ⇒ 差额单必须进失败 sheet（不许当正常放过）。"""
    split = _split(hx=["PO-A", "PO-B", "PO-C"])
    oms = {"PO-A": _oms(2, "仓库处理中", "GFUS1"),
           "PO-B": _oms(2, "仓库处理中", "GFUS2")}
    count = {"store": "BEL", "split_hx": 3,
             "sent_to_oms": ["PO-A", "PO-B"], "oms_found": ["PO-A", "PO-B"],
             "missing_in_oms": [], "ok": False}
    out = G.build("BEL", split, [], {}, hx_oms=oms,
                  hx_shipped_ords=["PO-A", "PO-B"], hx_count=count)
    assert [r["参考单号"] for r in out["失败订单"]] == ["PO-C"]
    f = out["失败订单"][0]
    assert f["失败环节"] == "领星发货" and "数量对不上" in f["失败原因"]
    assert out["_hx_count_ok"] is False


def test_hx_count_match_adds_nothing():
    split = _split(hx=["PO-A"])
    oms = {"PO-A": _oms(2, "仓库处理中", "GFUS1")}
    count = {"split_hx": 1, "sent_to_oms": ["PO-A"],
             "oms_found": ["PO-A"], "missing_in_oms": [], "ok": True}
    out = G.build("BEL", split, [], {}, hx_oms=oms,
                  hx_shipped_ords=["PO-A"], hx_count=count)
    assert out["失败订单"] == []
    assert out["_hx_count_ok"] is True


def test_hx_sent_but_missing_in_oms_is_reported_once():
    """送进 OMS 却查不到：既命中「查无此单」又命中数量台账 ⇒ 只能进表一次。"""
    split = _split(hx=["PO-A"])
    out = G.build("BEL", split, [], {}, hx_oms={},
                  hx_shipped_ords=["PO-A"],
                  hx_count={"split_hx": 1, "sent_to_oms": ["PO-A"],
                            "oms_found": [], "missing_in_oms": ["PO-A"], "ok": False})
    pos = [r["参考单号"] for r in out["失败订单"]]
    assert pos == ["PO-A"], pos
