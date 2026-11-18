# -*- coding: utf-8 -*-
"""test_report_dedupe_hxship.py — 出表两个真机 BUG 的回归钉（2026-11-18）。

用户原话：「为什么在今天的失败订单、缺货 sheet 中会有重复的订单出现，然后恒信仓下单
明明成功了，为什么有些订单也被记录到失败订单了」。

① 重复行：导出表里「同一订单 + 同一平台SKU」会出现两行（各 1 件），拆仓逐行落组 ⇒
   split.json / 三个仓 sheet / 失败 sheet 全都跟着重复（当天 BE 7 行、BS 5 行、BD 1 行）。
   修法：`bel_split_by_stock.merge_dup_lines` 合并件数；report 侧 `_dedupe_rows` 与
   `group_sheets` 再兜一道。
② 恒信假失败：`hx_ship` 自己报 38/39 成功（status=2 处理中），但领星**公开 OpenAPI**
   那一列 `accounts` 全空 ⇒ 旧逻辑整批判「OMS 查无此单」+ 数量台账「对不上」，
   当天 BD 39 单 / BAN 21 单 / KLS 38 单全变失败。
   修法：有 `hx_ship` 逐单结果时**以它为准**；公开接口整批没账号时不再逐单报失败。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_SCRIPTS = os.path.dirname(_POWER)
for p in (_POWER, os.path.join(_POWER, "lib"), _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import bel_split_by_stock as S  # noqa: E402
import gen_shortage_fail_xlsx as G  # noqa: E402


def _split(hx=None, pw=None, miss=None):
    return {"groups": {"HX": hx or [], "PW": pw or [], "MISS": miss or [],
                       "PRESALE": []}}


# ------------------------------------------------------------- ① 重复行去重
def test_merge_dup_lines_sums_qty():
    groups = {"HX": [{"ord": "PO-1", "sku": "A-L", "qty": 1, "why": "x"},
                     {"ord": "PO-1", "sku": "A-L", "qty": 1, "why": "x"},
                     {"ord": "PO-1", "sku": "A-M", "qty": 1, "why": "x"}],
              "PW": [], "MISS": [], "PRESALE": []}
    out = S.merge_dup_lines(groups)
    assert len(out["HX"]) == 2, out["HX"]
    merged = [r for r in out["HX"] if r["sku"] == "A-L"][0]
    assert merged["qty"] == 2, merged          # 同单同 SKU 两行 → 件数相加


def test_group_sheets_merges_duplicate_rows():
    split = _split(pw=[{"ord": "PO-1", "sku": "A-L", "qty": 1, "why": "", "wh": "加州3仓"},
                       {"ord": "PO-1", "sku": "A-L", "qty": 1, "why": "", "wh": "加州3仓"}])
    sheets = G.group_sheets([], split, "BEL")
    rows = sheets["BEL 帕沃尔仓"]["rows"]
    assert len(rows) == 1, rows
    assert rows[0]["件数"] == 2, rows[0]


def test_dedupe_rows_keeps_first():
    rows = [{"参考单号": "PO-1", "平台SKU": "A", "x": 1},
            {"参考单号": "PO-1", "平台SKU": "A", "x": 2},
            {"参考单号": "PO-1", "平台SKU": "B", "x": 3}]
    out = G._dedupe_rows(rows)
    assert len(out) == 2, out
    assert out[0]["x"] == 1


# --------------------------------------------------- ② 恒信「已发出」不许报失败
def _hx_rows(n=2):
    return [{"ord": "PO-%d" % i, "sku": "A-%d" % i, "qty": 1, "why": ""} for i in range(1, n + 1)]


def test_hx_ship_ok_is_not_reported_as_failure():
    """hx_ship 说成功、公开 OMS 全空 ⇒ 一条失败都不许有。"""
    hx = _hx_rows(3)
    split = _split(hx=hx)
    oms = {r["ord"]: {"accounts": []} for r in hx}       # 公开接口整批没账号
    ship = {"ok": [{"po": r["ord"], "status": 2, "statusText": "处理中", "track": ""}
                   for r in hx], "fail": []}
    res = G.build("BD", split, [], {}, hx_oms=oms, hx_ship_res=ship,
                  hx_count={"split_hx": 3, "sent_to_oms": [], "missing_in_oms": [r["ord"] for r in hx]})
    fails = res["失败订单"]
    assert fails == [], fails


def test_hx_ship_failure_is_reported_with_reason():
    """hx_ship 自己说失败的那单必须进失败 sheet，并带它的原始原因。"""
    hx = _hx_rows(2)
    split = _split(hx=hx)
    oms = {r["ord"]: {"accounts": []} for r in hx}
    ship = {"ok": [{"po": "PO-1", "status": 2, "statusText": "处理中"}],
            "fail": [{"po": "PO-2", "status": 1, "statusText": "待处理",
                      "why": "待获取平台面单"}]}
    res = G.build("BD", split, [], {}, hx_oms=oms, hx_ship_res=ship)
    fails = res["失败订单"]
    assert len(fails) == 1, fails
    assert fails[0]["参考单号"] == "PO-2"
    assert "待获取平台面单" in fails[0]["失败原因"], fails[0]


def test_hx_count_rows_skip_shipped_ok():
    """数量台账说「对不上」，但 hx_ship 已确认发成功的单不许再进失败 sheet。"""
    hx = _hx_rows(2)
    split = _split(hx=hx)
    oms = {r["ord"]: {"accounts": []} for r in hx}
    ship = {"ok": [{"po": "PO-1", "status": 3, "statusText": "已发货", "track": "YT1"}],
            "fail": []}
    cnt = {"split_hx": 2, "sent_to_oms": ["PO-1"], "checked_send": True,
           "missing_in_oms": ["PO-2"], "oms_found": []}
    res = G.build("BD", split, [], {}, hx_oms=oms, hx_ship_res=ship, hx_count=cnt)
    pos = [r["参考单号"] for r in res["失败订单"]]
    assert "PO-1" not in pos, pos        # 已发货 ⇒ 不报
    assert "PO-2" in pos, pos            # 没账号也没发结果 ⇒ 报（查无此单/对不上）
