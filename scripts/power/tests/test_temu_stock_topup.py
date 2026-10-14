# -*- coding: utf-8 -*-
"""TEMU 加库存补量口径的单测（纯离线，不连浏览器、不下载看板）。

跑法: python -m pytest scripts/power/tests/test_temu_stock_topup.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import temu_stock_topup as T  # noqa: E402

CFG = {
    "目标天数": 30,
    "扣减项": ["代发库存", "TEMU可售"],
    "变体换算": {
        "10SetA": {"看板变体": "5SetA", "factor": 2},
        "10SetB": {"看板变体": "5SetB", "factor": 2},
    },
    "店铺前缀正则": r"^[A-Za-z]{2,4}\d?-",
    "输出目录": "out",
}

# 看板口径：小款号 → 代发库存 / 近7天日均（数值取自 2026-10-10 快照的 202504-5SetA-*）
AGG = {
    "202504-5SetA-L": {"小款号": "202504-5SetA-L", "代发库存": 204, "代发途中": 511, "近7天日均": 44.285714},
    "202504-5SetA-XL": {"小款号": "202504-5SetA-XL", "代发库存": 303, "代发途中": 131, "近7天日均": 40.0},
    "202504-5SetA-XXL": {"小款号": "202504-5SetA-XXL", "代发库存": 192, "代发途中": 36, "近7天日均": 31.285714},
    "102521-3BHL-XXL": {"小款号": "102521-3BHL-XXL", "代发库存": 600, "代发途中": 0, "近7天日均": 0.0},
}


# ---------------------------------------------------------------- 款号解析
def test_parse_sku_code_剥店铺前缀():
    p = T.parse_sku_code("BHY-202504-10SetA-L", CFG)
    assert p["ok"] and p["shop"] == "BHY" and p["product"] == "202504"
    assert p["variant"] == "10SetA" and p["size"] == "L"


def test_parse_sku_code_带数字前缀():
    p = T.parse_sku_code("BS2-203804-5SetA-XL", CFG)
    assert p["ok"] and p["shop"] == "BS2" and p["product"] == "203804"
    assert p["variant"] == "5SetA" and p["size"] == "XL"


def test_parse_sku_code_三段不足则判无效():
    assert T.parse_sku_code("202504-L", CFG)["ok"] is False


# ---------------------------------------------------------------- 变体换算
def test_map_variant_10SetA折两个5SetA():
    c = T.map_variant("10SetA", CFG)
    assert c["看板变体"] == "5SetA" and c["factor"] == 2


def test_map_variant_未配的原样对应():
    c = T.map_variant("5SetE", CFG)
    assert c["看板变体"] == "5SetE" and c["factor"] == 1


# ---------------------------------------------------------------- 取整
def test_round_half_up_不是banker():
    assert T.round_half_up(418.5) == 419          # 内置 round(418.5) == 418，会少 1
    assert T.round_half_up(539.2857) == 539
    assert T.round_half_up(0.4) == 0
    assert T.round_half_up(-1.6) == -2


# ---------------------------------------------------------------- 口径
def test_加库存量_扣库存与现有可售():
    it = T.plan_item("BHY-202504-10SetA-L", 23, AGG, CFG)
    assert it["看板小款号"] == "202504-5SetA-L"
    assert it["TEMU单位_代发库存"] == 102.0        # 204 / 2
    assert it["TEMU单位_近7天日均"] == pytest.approx(22.1429, abs=1e-4)   # 展示值保留 4 位小数
    assert it["TEMU单位_目标天数需求"] == pytest.approx(664.2857, abs=1e-3)
    assert it["建议增加量"] == 539                 # 664.29 − 102 − 23
    assert it["增加后TEMU可售"] == 562


def test_加库存量_半个数的四舍五入():
    it = T.plan_item("BHY-202504-10SetA-XL", 30, AGG, CFG)
    assert it["净缺口(未取整)"] == pytest.approx(418.5)
    assert it["建议增加量"] == 419                 # half-up，不是 418


def test_缺口为负记0():
    AGG2 = dict(AGG)
    AGG2["202504-5SetA-XXL"] = {"小款号": "202504-5SetA-XXL", "代发库存": 9999, "近7天日均": 1.0}
    it = T.plan_item("BHY-202504-10SetA-XXL", 57, AGG2, CFG)
    assert it["建议增加量"] == 0 and it["结论"] == "不用加"


def test_只扣代发库存时不再扣现有可售():
    cfg = dict(CFG, 扣减项=["代发库存"])
    it = T.plan_item("BHY-202504-10SetA-L", 23, AGG, cfg)
    assert it["建议增加量"] == 562                  # 664.29 − 102


def test_看板没这个款则跳过():
    it = T.plan_item("BHY-999999-10SetA-L", 10, AGG, CFG)
    assert it["结论"] == "跳过" and "看板里没有" in it["原因"]


def test_日均缺数据跳过而不是当0():
    agg = {"202504-5SetA-L": {"小款号": "202504-5SetA-L", "代发库存": 10, "近7天日均": None}}
    it = T.plan_item("BHY-202504-10SetA-L", 10, agg, CFG)
    assert it["结论"] == "跳过" and "7天日均" in it["原因"]


def test_口径要扣现有可售但没给数则跳过():
    it = T.plan_item("BHY-202504-10SetA-L", None, AGG, CFG)
    assert it["结论"] == "跳过" and "销售可用库存读不到" in it["原因"]


# ---------------------------------------------------------------- 聚合
def test_聚合按小款号跨仓相加_款级字段只取一次():
    rows = [
        {"小款号": "X-L", "货源仓": "帕沃尔·加州3仓", "代发库存": 10, "代发途中": 1,
         "10天销量": 5, "近7天日均": 2.5, "未交货订单数": 7},
        {"小款号": "X-L", "货源仓": "恒信·美西", "代发库存": 4, "代发途中": 2,
         "10天销量": 3, "近7天日均": 2.5, "未交货订单数": 7},
    ]
    a = T.aggregate_by_sku(rows)["X-L"]
    assert a["代发库存"] == 14 and a["代发途中"] == 3 and a["10天销量"] == 8
    assert a["近7天日均"] == 2.5 and a["未交货订单数"] == 7
    assert a["仓库"] == ["帕沃尔·加州3仓", "恒信·美西"]


# ---------------------------------------------------------------- 落盘报告
def test_markdown含建议值():
    items = T.build_plan([{"SKU货号": "BHY-202504-10SetA-L", "可售库存": 23}], AGG, CFG)
    md = T.render_markdown(items, CFG, {"built_at": "2026-10-10 14:02:21"}, "单测")
    assert "**539**" in md and "只读" in md
