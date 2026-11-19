# -*- coding: utf-8 -*-
"""test_be_pw_only.py — BE 店「只发帕沃尔仓、跳过恒信领星」的口径。

用户 2026-11-15 令：
    「BE 店铺的库存只在帕沃尔仓，在恒信领星发货直接跳过」
    「在发货总链导表那一步加上 BE 导出待处理订单表 → BE 参与一整个发货流程」

钉住四条（都是**会被后续改动碰坏**的地方）：
  1. `bel_split_by_stock.allocate`：BE 的单只查帕沃尔 —— 哪怕恒信库存里有同名货；
  2. `gen_shortage_fail_xlsx`：BE 的表不出「恒信仓」sheet（出出来只会骗人去领星找货）；
  3. pipeline `hx_ship`：BE 在 `disabled_stores` 里（不进恒信 OMS）；
  4. 收尾推送的店清单里有 BE（否则表出了也不推群、不传云盘）。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_PROJ, "scripts", "power"))
sys.path.insert(0, os.path.join(_PROJ, "scripts", "tasks"))
sys.path.insert(0, os.path.join(_PROJ, "scripts"))

import bel_split_by_stock as S  # noqa: E402
import gen_shortage_fail_xlsx as G  # noqa: E402
import push_fulfill_report as P  # noqa: E402
import run_temu_fulfill as F  # noqa: E402


def _idx(stock: int, avail: int = 0, sku: str = "BE2-T3038-1B-L") -> dict:
    """按生产口径建库存索引（`build_index` 会登记去前缀 / 款号首字母 G 的别名）。"""
    return S.build_index([{"sku": sku, "name": "", "stock": stock,
                           "avail": avail or stock, "wh": "加州3仓"}], ("sku", "name"))


# ---------------------------------------------------------------- 拆仓

def test_be_split_never_goes_to_hx():
    """BE 的单：恒信有货也**不许**落 HX（BE 在恒信根本没有货）。"""
    orders = [{"ord": "GSU1", "sku": "BE-GT3038-1B-L", "qty": 1}]
    pw = _idx(5)
    lx = _idx(9, sku="BS-T3038-1B-L")
    got = S.allocate(orders, pw, lx, prefer="HX", store="BE")
    assert len(got["HX"]) == 0, "BE 的单被分到恒信仓了：%r" % (got["HX"],)
    assert len(got["PW"]) == 1 and got["PW"][0]["wh"] == "加州3仓"


def test_be_split_misses_instead_of_borrowing_hx():
    """帕沃尔没货、恒信有货 ⇒ BE 落**缺货**交人工，不偷偷走恒信。"""
    orders = [{"ord": "GSU2", "sku": "BE-GT3038-1B-L", "qty": 1}]
    pw = _idx(0)
    lx = _idx(9, sku="BS-T3038-1B-L")
    got = S.allocate(orders, pw, lx, prefer="HX", store="BE")
    assert not got["HX"] and not got["PW"]
    assert len(got["MISS"]) == 1
    assert "恒信可分" not in got["MISS"][0]["why"], "缺货原因里不该再提恒信"


def test_other_stores_still_prefer_hx():
    """BHY/BEL/BS/BD 的旧口径不能被这条改动带走（恒信仍优先）。"""
    orders = [{"ord": "PO1", "sku": "BS2-203311-4SetA-M", "qty": 1}]
    pw = _idx(5, sku="BS2-203311-4SetA-M")
    lx = _idx(9, sku="BS2-203311-4SetA-M")
    got = S.allocate(orders, pw, lx, prefer="HX", store="BHY")
    assert len(got["HX"]) == 1


# ---------------------------------------------------------------- 出表

def test_be_sheet_has_no_hx_sheet():
    assert G.has_hx_sheet("BE") is False
    assert G.has_hx_sheet("BAN") is True
    split = {"groups": {"HX": [{"ord": "X1", "sku": "bs-1", "qty": 1}],
                        "PW": [{"ord": "P1", "sku": "be-1", "qty": 1, "wh": "加州3仓"}],
                        "MISS": [], "PRESALE": []}}
    sheets = G.group_sheets([], split, "BE")
    assert "BE 恒信仓" not in sheets, "BE 不该出恒信仓 sheet"
    assert "BE 帕沃尔仓" in sheets and "BE 缺货" in sheets


# ---------------------------------------------------------------- pipeline / 推送

def test_pipeline_hx_ship_disabled_for_be():
    spec = F._read_json(F.DEFAULT_STEPS)
    hx = [s for s in spec["steps"] if s.get("name") == "hx_ship"][0]
    assert F.step_enabled(hx, "BE") is False
    assert F.step_enabled(hx, "BAN") is True


def test_push_lists_be():
    assert "BE" in P.STORES
    _, msg = P.build_message("2026-11-15", {}, {})
    assert "BE：未产出表格" in msg
