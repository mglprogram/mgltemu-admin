# -*- coding: utf-8 -*-
"""加库存建议（纯计算）的测试：算多少、填哪个仓、挂回行上。"""
import importlib.util
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "power", "lib"))

spec = importlib.util.spec_from_file_location(
    "stock_topup_plan", os.path.join(ROOT, "scripts", "bel_goods", "stock_topup_plan.py"))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

CFG = {"加库存": {"目标覆盖天数": 30}}
PROBE = [
    {"小款号": "204203-4SetA-S", "店铺": "BS2", "仓库名称": "帕沃尔·加州3仓", "代发库存": 364},
    {"小款号": "1091-1K-L", "店铺": "BH", "仓库名称": "帕沃尔·kansas仓", "代发库存": 4},
]


def row(**kw):
    base = {"store": "BHY", "skuCode": "BHY-X", "status": "即将售罄", "shipMode": "卖家自发货",
            "available": 0, "forecast7": 0, "probeStock": 0, "probeStockRaw": 0,
            "probeSku": "", "actions": ["加库存"]}
    base.update(kw)
    return base


# ---------------------------------------------------------------- 算多少
def test_实例_204203_仓库364件_可售14_预测20():
    inc, target = S.decide_increase(available=14, forecast7=20, stock_temu=364)
    assert (inc, target) == (72, 86)


def test_仓库比需求少_就按仓库封顶():
    inc, target = S.decide_increase(available=0, forecast7=6, stock_temu=3)
    assert (inc, target) == (3, 3)


def test_已经全申报了_不加_不超卖():
    inc, target = S.decide_increase(available=5, forecast7=17, stock_temu=4)
    assert inc == 0 and target == 4


def test_没有预测需求_不加():
    assert S.decide_increase(available=0, forecast7=0, stock_temu=100) == (0, 0)


def test_仓里没货_不加():
    assert S.decide_increase(available=0, forecast7=50, stock_temu=0) == (0, 0)


def test_可售读不到当0算():
    inc, _ = S.decide_increase(available=None, forecast7=7, stock_temu=100)
    assert inc == 30


def test_单次不超过上限():
    inc, _ = S.decide_increase(available=0, forecast7=70000, stock_temu=99999)
    assert inc == S.MAX_PER_ITEM


# ---------------------------------------------------------------- 填哪个仓
def test_优先填已经在卖的那个仓():
    rows = [{"warehouse": "新泽西2仓", "available": 0}, {"warehouse": "kansas", "available": 14}]
    assert S.pick_row(rows) == 1


def test_都有可售_取大的():
    rows = [{"warehouse": "a", "available": 3}, {"warehouse": "b", "available": 9}]
    assert S.pick_row(rows) == 1


def test_全为0_按看板同名且有货的仓():
    rows = [{"warehouse": "kansas", "available": 0}, {"warehouse": "新泽西2仓", "available": 0}]
    panel = {"帕沃尔·新泽西2仓": 12, "帕沃尔·kansas仓": 0}
    assert S.pick_row(rows, panel) == 1


def test_空行返回负一():
    assert S.pick_row([]) == -1


# ---------------------------------------------------------------- 计划与挂载
def test_合作仓对接发货_不给增加量并说明原因():
    rows = [row(skuCode="BEL-1", shipMode="合作仓对接发货", available=0, forecast7=6,
                probeStock=3, probeStockRaw=3, probeSku="R1026-3BHW-M")]
    plan = S.build_plan(rows, PROBE, CFG)
    assert plan[0]["increase"] == 0
    assert "合作仓" in plan[0]["skip"] and "立即加库存" in plan[0]["skip"]


def test_按看板仓库给出建议仓():
    rows = [row(skuCode="BHY-204203-4SetA-S", available=14, forecast7=20, probeStock=364,
                probeStockRaw=364, probeSku="204203-4SetA-S")]
    plan = S.build_plan(rows, PROBE, CFG)
    assert plan[0]["increase"] == 72
    assert plan[0]["warehouseSuggest"] == "帕沃尔·加州3仓"      # 看板里有货的那个仓


def test_attach_把建议挂回行上():
    rows = [row(skuCode="BHY-204203-4SetA-S", available=14, forecast7=20, probeStock=364,
                probeStockRaw=364, probeSku="204203-4SetA-S"),
            row(skuCode="BHY-1091-1K-L", available=5, forecast7=17, probeStock=4,
                probeStockRaw=4, probeSku="1091-1K-L")]
    summ = S.attach(rows, PROBE, CFG, store="BHY")
    assert summ["total"] == 72 and len(summ["items"]) == 1
    assert rows[0]["topup"]["increase"] == 72 and rows[0]["topup"]["target"] == 86
    assert rows[1]["topup"]["increase"] == 0
    assert "超卖" in rows[1]["topup"]["skip"]
    assert summ["items"][0]["store"] == "BHY"
