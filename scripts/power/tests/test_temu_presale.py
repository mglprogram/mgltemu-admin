# -*- coding: utf-8 -*-
"""预售判定单测（纯离线：不连窗口、不读飞书、不下载看板）。

跑法: python -m pytest scripts/power/tests/test_temu_presale.py -q
"""
import importlib.util
import os
import sys

import pytest

ROOT = r"D:\AI-workplace\TEMU"
sys.path.insert(0, os.path.join(ROOT, "scripts", "power", "lib"))

import temu_stock_topup as T  # noqa: E402

# 直接按文件加载导出器（它不是 lib，是 scripts/bel_goods 下的入口脚本）
_spec = importlib.util.spec_from_file_location(
    "build_presale_data", os.path.join(ROOT, "scripts", "bel_goods", "build_presale_data.py"))
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)

CFG_T = {
    "变体换算": {"10SetA": {"看板变体": "5SetA", "factor": 2}},
    "店铺前缀正则": r"^[A-Za-z]{2,4}\d?-",
    "看板": {"url": "", "缓存秒": 0},
}
CFG_P = {"预售判定": {"预测字段": "forecast7", "现货可售天数阈值": 7}}

# 看板口径：现货 204 件（5Set 单位）= 102 个 TEMU 单位
AGG = {"202504-5SetA-L": {"代发库存": 204, "代发途中": 511, "近7天日均": 44.285714}}


def _row(**kw):
    r = {"sku_code": "BHY-202504-10SetA-L", "sku_id": "1", "size": "L", "title": "",
         "status": "即将售罄", "forecast7": 110, "forecast14": 186, "available": 22}
    r.update(kw)
    return r


def test_可开预售_现货不够且有到货日():
    fs = {"到货计划": {"202504-5SetA-L": [
        {"到货日": "2026-11-01", "件数": 200, "尺码件数": 141, "是否入仓": False}]},
        "覆盖": {}, "上架": {}}
    out = B.judge(_row(), AGG, fs, CFG_T, CFG_P)
    assert out["factor"] == 2
    assert out["probeStockRaw"] == 204.0
    assert out["probeStock"] == 102.0            # 204 / 2
    assert out["stockDays"] == pytest.approx(6.49, abs=0.01)   # 102 / (110/7)
    assert out["transitRaw"] == 141.0
    assert out["transit"] == 70.5                # 141 / 2
    assert out["arrivalDate"] == "2026-11-01"
    assert out["canPresale"] is True
    assert out["canRestock"] is True             # 仓里有货 → 也能直接加库存
    assert out["actions"] == ["开预售", "加库存"]


def test_现货够卖就不列动作():
    fs = {"到货计划": {"202504-5SetA-L": [
        {"到货日": "2026-11-01", "件数": 200, "尺码件数": 141, "是否入仓": False}]},
        "覆盖": {}, "上架": {}}
    out = B.judge(_row(forecast7=7, available=500), AGG, fs, CFG_T, CFG_P)
    assert out["verdict"] == "不用开"
    assert out["canPresale"] is False and out["canRestock"] is False
    assert "够卖" in out["reason"]


def test_即将售罄_仓库货很多但TEMU可售只够几天_也要加库存():
    """2026-10-13 实例 BHY-204203-4SetA-S：仓库 364 件（够 127 天），
    但 TEMU 只申报了 14 件可售（预计未来7日 20 ⇒ 只够 4.9 天），平台已标「即将售罄」。
    旧口径按仓库算会漏报，新口径按 TEMU 可售算才对。"""
    agg = {"202504-5SetA-L": {"代发库存": 728, "代发途中": 0, "近7天日均": 40}}
    fs = {"到货计划": {}, "覆盖": {}, "上架": {}}
    out = B.judge(_row(forecast7=20, available=14), agg, fs, CFG_T, CFG_P)
    assert out["probeStockRaw"] == 728.0
    assert out["stockDays"] == pytest.approx(127.4, abs=0.5)     # 折 TEMU 364 件 ⇒ 仓库够卖 127 天
    assert out["availDays"] == pytest.approx(4.9, abs=0.05)      # TEMU 可售只够 4.9 天
    assert out["canRestock"] is True
    assert "TEMU 可售 14 件只够 4.9 天" in out["reason"]


def test_TEMU可售也够_就不动():
    agg = {"202504-5SetA-L": {"代发库存": 728, "代发途中": 0, "近7天日均": 40}}
    fs = {"到货计划": {}, "覆盖": {}, "上架": {}}
    out = B.judge(_row(forecast7=7, available=500), agg, fs, CFG_T, CFG_P)
    assert out["availDays"] is not None and out["availDays"] >= 7
    assert out["canRestock"] is False and out["canPresale"] is False
    assert out["verdict"] == "不用开"


def test_已售罄但仓里有货_动作是加库存():
    fs = {"到货计划": {}, "覆盖": {}, "上架": {}}
    out = B.judge(_row(status="已售罄", forecast7=7), AGG, fs, CFG_T, CFG_P)
    assert out["verdict"] == "先加库存"
    assert out["canRestock"] is True
    assert out["canPresale"] is False            # 没在途，开不了预售


def test_已售罄有货但没预测需求_也要能加库存():
    """已售罄的款不看预测：仓里有货就该恢复上架。"""
    fs = {"到货计划": {}, "覆盖": {}, "上架": {}}
    out = B.judge(_row(status="已售罄", forecast7=0), AGG, fs, CFG_T, CFG_P)
    assert out["canRestock"] is True
    assert "已售罄" in out["reason"]


def test_没在途且现货不够_现货那部分仍可加库存():
    fs = {"到货计划": {}, "覆盖": {}, "上架": {}}
    out = B.judge(_row(), AGG, fs, CFG_T, CFG_P)
    assert out["canPresale"] is False            # 没在途 → 不能预售
    assert out["canRestock"] is True             # 但仓里的货可以加
    assert out["transitRaw"] == 0


def test_现货为0且在途为0_只能补货():
    agg = {"202504-5SetA-L": {"代发库存": 0, "代发途中": 0, "近7天日均": 10}}
    fs = {"到货计划": {}, "覆盖": {}, "上架": {}}
    out = B.judge(_row(status="已售罄"), agg, fs, CFG_T, CFG_P)
    assert out["verdict"] == "要补货"
    assert out["canPresale"] is False and out["canRestock"] is False


def test_在途没有到货日_不能开预售():
    agg = {"202504-5SetA-L": {"代发库存": 0, "代发途中": 0, "近7天日均": 10}}
    fs = {"到货计划": {"202504-5SetA-L": [
        {"到货日": "", "件数": 100, "尺码件数": 100, "是否入仓": False}]},
        "覆盖": {}, "上架": {}}
    out = B.judge(_row(), agg, fs, CFG_T, CFG_P)
    assert out["canPresale"] is False
    assert out["canRestock"] is False            # 现货也是 0
    assert out["transitRaw"] == 100.0
    assert out["verdict"] == "要补货"
    assert "到货日" in out["reason"]


def test_无预测需求就不碰():
    fs = {"到货计划": {"202504-5SetA-L": [
        {"到货日": "2026-11-01", "件数": 200, "尺码件数": 141, "是否入仓": False}]},
        "覆盖": {}, "上架": {}}
    out = B.judge(_row(forecast7=0), AGG, fs, CFG_T, CFG_P)
    assert out["verdict"] == "不用开"
    assert out["canPresale"] is False and out["canRestock"] is False
    assert "无销量" in out["reason"]


def test_已入仓的批次不算在途():
    fs = {"到货计划": {"202504-5SetA-L": [
        {"到货日": "2026-10-01", "件数": 200, "尺码件数": 141, "是否入仓": True}]},
        "覆盖": {}, "上架": {}}
    out = B.judge(_row(), AGG, fs, CFG_T, CFG_P)
    assert out["transitRaw"] == 0
    assert out["canPresale"] is False


def test_在途优先取领星覆盖甘特_票级只用来算到货日():
    """2026-10-12 的教训：票级只有 141，领星在途是 495，必须用后者。"""
    fs = {"到货计划": {"202504-5SetA-L": [
        {"到货日": "2026-11-01", "件数": 141, "尺码件数": 141, "是否入仓": False}]},
        "覆盖": {"202504-5SetA-L": {"在途件数": 495, "下一批到货日": "2026-11-01",
                                    "可用库存": 164, "日耗": 7.857}},
        "上架": {}}
    out = B.judge(_row(status="已售罄", forecast7=123), AGG, fs, CFG_T, CFG_P)
    assert out["transitRaw"] == 495.0            # 领星口径，不是票级的 141
    assert out["transit"] == 247.5
    assert out["transitSource"] == "领星库存覆盖甘特"
    assert out["arrivalDate"] == "2026-11-01"
    assert out["arrivalTicketQty"] == 141         # 这个到货日只覆盖 141 件
    assert out["canPresale"] is True


def test_票级没有就用库存覆盖甘特的到货日兜底():
    fs = {"到货计划": {}, "覆盖": {"202504-5SetA-L": {
        "在途件数": 300, "下一批到货日": "2026-11-20", "空窗天数": 12, "缺口件数": 40,
        "可用库存": 10}}, "上架": {}}
    out = B.judge(_row(), AGG, fs, CFG_T, CFG_P)
    assert out["transitRaw"] == 300.0
    assert out["arrivalDate"] == "2026-11-20"
    assert out["lingxingGapDays"] == 12
    assert out["canPresale"] is True


def test_款号解析与换算复用看板那套():
    p = T.parse_sku_code("BHY-202504-10SetA-L", CFG_T)
    assert (p["product"], p["variant"], p["size"]) == ("202504", "10SetA", "L")
    c = T.map_variant("10SetA", CFG_T)
    assert c["看板变体"] == "5SetA" and c["factor"] == 2
