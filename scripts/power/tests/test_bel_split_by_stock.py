# -*- coding: utf-8 -*-
"""test_bel_split_by_stock.py — BEL 拆分规则（帕沃尔优先 + 防超卖）的纯函数测试。

钉住的都是真机踩过的坑：
  · 匹配必须去品牌前缀（BH/BK/BS/BS2/BLA/BEL），否则 `BEL-` 永远匹配不上 BH-；
  · 帕沃尔只认发货仓（加州3仓/kansas仓），Temu自动分仓不算可用；
  · 同款多单要按总需求分配（204203-4SetC-L 只 2 件 / 本批 5 单 → 只能发 2 单）；
  · 缺货的不能混进 A/B（超卖风险）；
  · 落档原因要说清「分完了」vs「没这个 SKU」。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import bel_split_by_stock as B  # noqa: E402


# ---------------------------------------------------------------- norm_sku / build_index


def test_norm_strips_store_and_brand_prefixes():
    assert B.norm_sku("BEL-203304-4SetA-S") == "203304-4SETA-S"
    assert B.norm_sku("BH-102104A-3GHL-L") == "102104A-3GHL-L"
    assert B.norm_sku("BS2-204605-3BHP-M") == "204605-3BHP-M"
    assert B.norm_sku("BLA-102303-6SetA-M") == "102303-6SETA-M"


def test_norm_strips_bhy_store_prefix():
    """BHY 店自己的前缀也要去掉。

    ★ 2026-11-06 真机事故：白名单里只有 BEL 没有 BHY ⇒ BHY 店的
      `BHY-203311-4SetA-M` 归一成 `BHY-203311-4SETA-M`，而库存里叫
      `BS2-203311-4SetA-M` ⇒ 两边都查不到 ⇒ **438 单全被判缺货**。
    """
    assert B.norm_sku("BHY-203311-4SetA-M") == "203311-4SETA-M"
    assert B.norm_sku("BHY-204207-4SetA-S") == "204207-4SETA-S"


def test_norm_bel_and_bhy_converge():
    """同一款在两个店的前缀不同，归一后必须是同一个键（否则跨店同款对不上）。"""
    assert B.norm_sku("BEL-203311-4SetA-M") == B.norm_sku("BHY-203311-4SetA-M")


def test_norm_bhy_converges_with_warehouse_brand():
    """BHY 店铺前缀 vs 仓侧品牌前缀（BS2/BK/BH）归一后也要一致。"""
    assert B.norm_sku("BHY-203311-4SetA-M") == B.norm_sku("BS2-203311-4SetA-M")
    assert B.norm_sku("BHY-203311-4SetA-M") == B.norm_sku("BK-203311-4SetA-M")


def test_norm_keeps_version_dot_and_trailing_letter():
    """`6SetA1.1` 的点、`102104A` 的尾字母都不能丢（库存 JSON 的拆字段会丢）。"""
    assert B.norm_sku("BEL-102303-6SetA1.1-XL") == "102303-6SETA1.1-XL"
    assert B.norm_sku("BEL-102104A-3GHL-L") == "102104A-3GHL-L"


def test_build_index_filters_by_warehouse():
    items = [
        {"sku": "BS2-204605-3BHP-M", "wh": "加州3仓", "stock": 166},
        {"sku": "BH-204605-3BHP-M", "wh": "kansas仓", "stock": 1},
        {"sku": "BH-204605-3BHP-M", "wh": "Temu自动分仓", "stock": 999},
    ]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH)
    got = idx["204605-3BHP-M"]
    assert len(got) == 2, "Temu自动分仓不该计入可用"
    assert sum(x["stock"] for x in got) == 167


def test_build_index_indexes_name_field_too():
    items = [{"sku": "BK-104102-1B-L", "name": "BH-104102-1B-L", "wh": "加州3仓", "stock": 33}]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH)
    assert "104102-1B-L" in idx


# ---------------------------------------------------------------- allocate


def _pw(rows):
    return {B.norm_sku(x["sku"]): [x] for x in rows}


def _lx(rows):
    return {B.norm_sku(x["sku"]): [x] for x in rows}


def test_prefers_hx_by_default_since_20261106():
    """★ 2026-11-06 用户改口径：「优先恒信可用库存，其次再是帕沃尔仓库存」。

    两边都有货时默认归**恒信**（与 11-05 的「帕沃尔优先」相反）。
    """
    pw = _pw([{"sku": "BEL-203802-4SetB-M", "wh": "加州3仓", "stock": 30}])
    lx = _lx([{"sku": "BS2-203802-4SetB-M", "avail": 92}])
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-203802-4SetB-M", "qty": 1}], pw, lx)
    assert len(g["HX"]) == 1 and not g["PW"], g
    assert g["HX"][0]["wh"] == B.HX_WAREHOUSE


def test_prefer_pw_restores_old_order():
    """`prefer="PW"` 时恢复 11-05 的帕沃尔优先（留作回滚/对照）。"""
    pw = _pw([{"sku": "BEL-203802-4SetB-M", "wh": "加州3仓", "stock": 30}])
    lx = _lx([{"sku": "BS2-203802-4SetB-M", "avail": 92}])
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-203802-4SetB-M", "qty": 1}], pw, lx,
                   prefer="PW")
    assert len(g["PW"]) == 1 and not g["HX"]


def test_prefer_rejects_unknown_value():
    import pytest
    with pytest.raises(ValueError):
        B.allocate([], {}, {}, prefer="BEL")


def test_falls_back_to_pw_when_hx_empty():
    """恒信没货 → 落到帕沃尔（反转后帕沃尔成了兜底）。"""
    pw = _pw([{"sku": "BH-203805-3BHL-M", "wh": "加州3仓", "stock": 7}])
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-203805-3BHL-M", "qty": 1}], pw, {})
    assert len(g["PW"]) == 1 and not g["HX"]


def test_falls_back_to_hx_when_pw_empty():
    pw = _pw([])
    lx = _lx([{"sku": "BH-203805-3BHL-M", "avail": 250}])
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-203805-3BHL-M", "qty": 1}], pw, lx)
    assert len(g["HX"]) == 1 and g["HX"][0]["wh"] == "ZOO WAREHOUSE ⑥美西"


def test_no_stock_goes_to_miss_not_pw():
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-104102-1B2-L", "qty": 1}], {}, {})
    assert len(g["MISS"]) == 1 and not g["PW"] and not g["HX"]
    assert "无此 SKU" in g["MISS"][0]["why"]


def test_oversell_guard_allocates_only_available_units():
    """真机案例：204203-4SetC-L 帕沃尔只有 2 件、本批 5 单 → 只能放 2 单。"""
    pw = _pw([{"sku": "BH-204203-4SetC-L", "wh": "kansas仓", "stock": 2}])
    orders = [{"ord": "PO-%d" % i, "sku": "BEL-204203-4SetC-L", "qty": 1} for i in range(5)]
    g = B.allocate(orders, pw, {})
    assert len(g["PW"]) == 2, g
    assert len(g["MISS"]) == 3, g


def test_miss_reason_distinguishes_exhausted_from_absent():
    """被前面单分完 ≠ 没这个 SKU —— 排查时必须能分清。"""
    pw = _pw([{"sku": "BH-204203-4SetC-L", "wh": "kansas仓", "stock": 1}])
    orders = [{"ord": "PO-1", "sku": "BEL-204203-4SetC-L", "qty": 1},
              {"ord": "PO-2", "sku": "BEL-204203-4SetC-L", "qty": 1}]
    g = B.allocate(orders, pw, {})
    assert len(g["PW"]) == 1
    assert "分完" in g["MISS"][0]["why"], g["MISS"][0]["why"]


def test_qty_greater_than_one_respects_units():
    pw = _pw([{"sku": "BH-104102-1B-M", "wh": "加州3仓", "stock": 1}])
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-104102-1B-M", "qty": 2}], pw, {})
    assert len(g["MISS"]) == 1, "需 2 件但只有 1 件 → 不许拆开发"


def test_every_order_lands_exactly_once():
    pw = _pw([{"sku": "BH-104102-1B-M", "wh": "加州3仓", "stock": 5}])
    lx = _lx([{"sku": "BH-203805-3BHL-S", "avail": 9}])
    orders = [{"ord": "PO-1", "sku": "BEL-104102-1B-M", "qty": 1},
              {"ord": "PO-2", "sku": "BEL-203805-3BHL-S", "qty": 1},
              {"ord": "PO-3", "sku": "BEL-999999-9X-XL", "qty": 1}]
    g = B.allocate(orders, pw, lx)
    got = [r["ord"] for side in ("PW", "HX", "MISS") for r in g[side]]
    assert sorted(got) == ["PO-1", "PO-2", "PO-3"]


# ---------------------------------------------------------------- 2026-11-12 一单只进一个仓


def test_multi_sku_order_takes_the_source_that_covers_all_lines():
    """一单两行、**同一个帕沃尔仓**两行都有货 ⇒ 整单落帕沃尔（不许一行恒信一行帕沃尔）。

    ★ 2026-11-12 用户令：「拆完把恒信仓、帕沃尔仓、缺货三个 sheet 去重，
      不能一个订单重复出现多个 sheet」。一单只有一张面单、只能从一个仓发。
    ★ 2026-11-13 用户令：「帕沃尔仓时优先选加州3仓的库存，没货选 Kansas 仓」——
      所以「两行分别在不同仓」不算整单可发（见下一个用例）。
    """
    pw = _pw([{"sku": "BH-104102-1B-M", "wh": "kansas仓", "stock": 5},
              {"sku": "BH-203805-3BHL-S", "wh": "kansas仓", "stock": 5}])
    lx = _lx([{"sku": "BS2-203805-3BHL-S", "avail": 9}])
    orders = [{"ord": "PO-1", "sku": "BEL-104102-1B-M", "qty": 1},
              {"ord": "PO-1", "sku": "BEL-203805-3BHL-S", "qty": 1}]
    g = B.allocate(orders, pw, lx)
    assert [r["ord"] for r in g["PW"]] == ["PO-1", "PO-1"], g["PW"]
    assert {r["wh"] for r in g["PW"]} == {"kansas仓"}, g["PW"]
    assert not g["HX"] and not g["MISS"], g


def test_pw_prefers_california_until_its_stock_runs_out():
    """加州3仓优先；加州3仓不够整单才退 kansas 仓（余量按仓递减，不重复分配）。"""
    key = B.norm_sku("BEL-104102-1B-M")
    # 同一个 SKU 在帕沃尔有**两行**（两个仓），要用原样的库存形状（_pw 助手是一 SKU 一行）
    pw = {key: [{"sku": "BH-104102-1B-M", "wh": "加州3仓", "stock": 2},
                {"sku": "BH-104102-1B-M", "wh": "kansas仓", "stock": 9}]}
    orders = [{"ord": "PO-%d" % i, "sku": "BEL-104102-1B-M", "qty": 1} for i in range(4)]
    g = B.allocate(orders, pw, {})
    assert [(r["ord"], r["wh"]) for r in g["PW"]] == [
        ("PO-0", "加州3仓"), ("PO-1", "加州3仓"),
        ("PO-2", "kansas仓"), ("PO-3", "kansas仓")], g["PW"]


def test_order_needing_more_than_one_warehouse_goes_miss():
    """整单量超过任一单仓库存、但两仓合计够 ⇒ 整单缺货（一单不能跨仓拼）。"""
    key = B.norm_sku("BEL-104102-1B-M")
    pw = {key: [{"sku": "BH-104102-1B-M", "wh": "加州3仓", "stock": 1},
                {"sku": "BH-104102-1B-M", "wh": "kansas仓", "stock": 1}]}
    orders = [{"ord": "PO-1", "sku": "BEL-104102-1B-M", "qty": 2}]
    g = B.allocate(orders, pw, {})
    assert not g["PW"], g["PW"]
    assert [r["ord"] for r in g["MISS"]] == ["PO-1"], g["MISS"]


def test_multi_sku_order_is_never_split_across_warehouses():
    """一单两行分别只有不同仓有货 ⇒ **整单缺货**（不拆成半个单发一半）。"""
    pw = _pw([{"sku": "BH-104102-1B-M", "wh": "加州3仓", "stock": 5}])
    lx = _lx([{"sku": "BS2-203805-3BHL-S", "avail": 9}])
    orders = [{"ord": "PO-1", "sku": "BEL-104102-1B-M", "qty": 1},
              {"ord": "PO-1", "sku": "BEL-203805-3BHL-S", "qty": 1}]
    g = B.allocate(orders, pw, lx)
    assert not g["PW"] and not g["HX"], g
    assert len(g["MISS"]) == 2, g["MISS"]
    assert all("无法全部落在同一个仓" in r["why"] for r in g["MISS"]), g["MISS"]


def test_no_order_appears_in_two_groups():
    """回归闸门：本批任意订单只许出现在一个组里（等价于三个 sheet 不重复）。"""
    pw = _pw([{"sku": "BH-104102-1B-M", "wh": "加州3仓", "stock": 2}])
    lx = _lx([{"sku": "BS2-203805-3BHL-S", "avail": 9}])
    orders = [{"ord": "PO-1", "sku": "BEL-104102-1B-M", "qty": 1},
              {"ord": "PO-1", "sku": "BEL-203805-3BHL-S", "qty": 1},
              {"ord": "PO-2", "sku": "BEL-104102-1B-M", "qty": 3},
              {"ord": "PO-2", "sku": "BEL-203805-3BHL-S", "qty": 1}]
    g = B.allocate(orders, pw, lx)
    seen = {}
    for side in ("PW", "HX", "MISS"):
        for r in g[side]:
            seen.setdefault(r["ord"], set()).add(side)
    assert all(len(v) == 1 for v in seen.values()), seen


def test_dedupe_groups_converges_an_order_to_one_group():
    """兜底闸门：同一单同时出现在恒信与帕沃尔 ⇒ 收敛成一组（缺货 > 恒信 > 帕沃尔）。"""
    groups = {"HX": [{"ord": "PO-1", "sku": "a"}],
              "PW": [{"ord": "PO-1", "sku": "a"}, {"ord": "PO-2", "sku": "b"}],
              "MISS": [{"ord": "PO-3", "sku": "c"}]}
    g, conflicts = B.dedupe_groups(groups)
    assert [r["ord"] for r in g["HX"]] == ["PO-1"]
    assert [r["ord"] for r in g["PW"]] == ["PO-2"]
    assert len(conflicts) == 1 and conflicts[0]["kept"] == "HX", conflicts


def test_dedupe_groups_keeps_multi_row_orders_inside_one_group():
    """同组里一单多行是**正常**的（一单多 SKU），不能被当成重复删掉。"""
    groups = {"PW": [{"ord": "PO-1", "sku": "a"}, {"ord": "PO-1", "sku": "b"}],
              "HX": [], "MISS": []}
    g, conflicts = B.dedupe_groups(groups)
    assert len(g["PW"]) == 2 and not conflicts, (g, conflicts)


def test_dedupe_groups_prefers_miss_over_warehouses():
    """缺货最保守：同一单既在缺货又在恒信 ⇒ 留在缺货（不许照表去买面单）。"""
    groups = {"MISS": [{"ord": "PO-1", "sku": "a"}],
              "HX": [{"ord": "PO-1", "sku": "a"}], "PW": []}
    g, conflicts = B.dedupe_groups(groups)
    assert [r["ord"] for r in g["MISS"]] == ["PO-1"] and not g["HX"], g
    assert conflicts and conflicts[0]["dropped"] == "HX", conflicts


# ---------------------------------------------------------------- 2026-11-05 真机回归


def test_same_row_sku_and_name_are_not_double_counted():
    """真机案：`sku` 与 `name` 是同一串，旧实现两字段都收 ⇒ 库存算成双倍。

    实测 `203304-4SetA-S` 帕沃尔只有 4 件（BS 1 + BH 3），却报 8 件。
    """
    items = [
        {"sku": "BS-203304-4SetA-S", "name": "BS-203304-4SetA-S", "wh": "kansas仓", "stock": 1},
        {"sku": "BH-203304-4SetA-S", "name": "BH-203304-4SetA-S", "wh": "kansas仓", "stock": 3},
    ]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH)
    rows = idx["203304-4SETA-S"]
    assert len(rows) == 2, rows
    assert sum(int(x["stock"]) for x in rows) == 4, "不许双计"


def test_warehouse_is_the_one_that_has_stock_not_hardcoded():
    """真机案：`203304-4SetA-S` 加州3仓 stock=0（在途 69 不算）、kansas 才有 4 件。

    旧实现把 `wh` 写死「加州3仓」⇒ 下单会因仓里没货失败。
    """
    items = [{"sku": "BS-203304-4SetA-S", "name": "BS-203304-4SetA-S",
              "wh": "加州3仓", "stock": 0, "transit": 69},
             {"sku": "BH-203304-4SetA-S", "name": "BH-203304-4SetA-S",
              "wh": "kansas仓", "stock": 4, "transit": 0}]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH)
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-203304-4SetA-S", "qty": 1}], idx, {})
    assert len(g["PW"]) == 1
    assert g["PW"][0]["wh"] == "kansas仓", g["PW"][0]


def test_warehouse_prefers_jiazhou3_when_both_have_stock():
    items = [{"sku": "BH-204605-3BHP-M", "name": "BH-204605-3BHP-M", "wh": "加州3仓", "stock": 166},
             {"sku": "BK-204605-3BHP-M", "name": "BK-204605-3BHP-M", "wh": "kansas仓", "stock": 1}]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH)
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-204605-3BHP-M", "qty": 1}], idx, {})
    assert g["PW"][0]["wh"] == "加州3仓"


# ---------------------------------------------------------------- 变体带数字（2026-11-06 用户口径）


def test_relax_sku_drops_only_tail_digits():
    """用户原话：「104102-1B1 / 1091-1B1 这种变体带 1、2、1.1，查库存时把数字去掉查」。

    只削变体**末尾**的纯数字段，`1B` / `6SetA` / `10SetA` 里的数字不动。
    """
    assert B.relax_sku("104102-1B2-L") == "104102-1B-L"
    assert B.relax_sku("1091-1B1-M") == "1091-1B-M"
    assert B.relax_sku("102303-6SETA1.1-XL") == "102303-6SETA-XL"
    # 不带尾数字的原样返回
    assert B.relax_sku("104102-1B-L") == "104102-1B-L"
    assert B.relax_sku("N102503-10SETA-XL") == "N102503-10SETA-XL"
    assert B.relax_sku("203304-4SETA-S") == "203304-4SETA-S"


def test_relax_sku_never_empties_variant():
    """削完变体变空就原样返回 —— 绝不猜成 `CODE--SIZE`。"""
    assert B.relax_sku("123-1-S") == "123--S" or B.relax_sku("123-1-S") == "123-1-S"


def test_relaxed_matching_recovers_1b2():
    """真机案：`104102-1B2-L` 库存里叫 `104102-1B-L`（加州3仓 80 件）。"""
    items = [{"sku": "BH-104102-1B-L", "name": "BH-104102-1B-L", "wh": "加州3仓", "stock": 80}]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH, relaxed=True)
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-104102-1B2-L", "qty": 1}], idx, {})
    assert len(g["PW"]) == 1, g
    assert g["PW"][0].get("matched_key") == "104102-1B-L"


def test_relaxed_matching_recovers_versioned_variant():
    """真机案：`102303-6SetA1.1-XL` 库存里叫 `102303-6SetA-XL`。"""
    items = [{"sku": "BK-102303-6SetA-XL", "name": "BK-102303-6SetA-XL",
              "wh": "加州3仓", "stock": 41}]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH, relaxed=True)
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-102303-6SetA1.1-XL", "qty": 1}], idx, {})
    assert len(g["PW"]) == 1, g
    assert g["PW"][0].get("matched_key") == "102303-6SETA-XL"


def test_exact_match_wins_over_relaxed():
    """精确命中必须优先 —— 不能让 `1B2` 抢了 `1B` 的库存导致重复计数。"""
    items = [{"sku": "BH-104102-1B-L", "name": "BH-104102-1B-L", "wh": "加州3仓", "stock": 80},
             {"sku": "BH-104102-1B2-L", "name": "BH-104102-1B2-L", "wh": "加州3仓", "stock": 3}]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH, relaxed=True)
    g = B.allocate([{"ord": "PO-1", "sku": "BEL-104102-1B2-L", "qty": 1}], idx, {})
    assert g["PW"][0].get("matched_key") is None, "精确命中不该标 matched_key"


def test_relaxed_off_by_default():
    """默认不开退化匹配（老调用点行为不变）。"""
    items = [{"sku": "BH-104102-1B-L", "name": "BH-104102-1B-L", "wh": "加州3仓", "stock": 80}]
    idx = B.build_index(items, ("sku", "name"), wh_filter=B.PW_SHIP_WH)
    assert "104102-1B2-L" not in idx

# ---------------------------------------------------------------- variant_missing


def test_variant_missing_detects_naming_error():
    """变体在库存里根本不存在，但同款同码有别的变体 ⇒ 疑似命名错。"""
    keys = {"202504-5SETA-XL", "202504-5SETB-XL", "202504-5SETE-XL"}
    hint = B.variant_missing("202504-10SETA-XL", keys)
    assert hint, "应当识别出变体不存在"
    assert "10SETA" in hint
    assert "5SETA" in hint


def test_variant_missing_returns_empty_for_stranger_sku():
    """全库没有这个款 ⇒ 不给命名提示（免得误导）。"""
    keys = {"100000-1A-L", "200000-2B-M"}
    assert B.variant_missing("999999-9Z-XL", keys) == ""


def test_variant_missing_ignores_different_size():
    """同款同变体但不同尺码，不能算作"同码有别的变体"。"""
    keys = {"202504-5SETA-M", "202504-5SETA-L"}
    assert B.variant_missing("202504-10SETA-XL", keys) == ""


def test_variant_missing_only_other_variant():
    """同款同码同变体（就是它自己）不算兄弟。"""
    keys = {"202504-5SETA-XL"}
    assert B.variant_missing("202504-5SETA-XL", keys) == ""


def test_variant_missing_handles_short_key():
    """不是三段式的 key 不猜。"""
    assert B.variant_missing("ABC", {"X-1-L"}) == ""
