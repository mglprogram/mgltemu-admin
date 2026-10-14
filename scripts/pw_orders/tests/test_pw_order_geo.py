# -*- coding: utf-8 -*-
"""scripts/pw_orders 的离线单测（不碰网络）。

跑：python -m pytest scripts/pw_orders/tests -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))

from pw_order_geo import (CODE, aggregate, parse_order_info, parse_rows,   # noqa: E402
                          split_city_state, zip_state)

# 2026-10-14 从帕沃尔 deliver.php 真实抓下来的片段（订单 5484949）
ROW_HTML = """
<tr class="tr_html_5484949   ">
  <td rowspan="1"><input type="checkbox" name="checkboxes[]" value="5484949" class="checkbox" />&nbsp;1</td>
  <td rowspan="1" class="waybill_5484949">GFUS01078143266562</td>
  <td rowspan="1"><p class="deliver_no_5484949">PO-211-19706774477432635</p></td>
  <td rowspan="1">2.00</td><td>D14450NOVBI</td><td>BS2-202504-5SetB-S</td><td>2</td>
  <td><img src="http://x/y.jpg"></td>
  <td rowspan="1"><p>temu官方面单</p></td>
  <td rowspan="1">加州3仓</td><td rowspan="1">BHY</td><td rowspan="1">TEMU</td>
  <td rowspan="1">待打包</td><td rowspan="1"><p>2026-10-14 14:56:48</p></td>
  <td rowspan="1"><a onclick="view_order(5484949)">[详情]</a>
      <a href="./deliver.php?act=printf4&ids=5484949">[面单]</a></td>
</tr>
<tr class="tr_html_5484948   ">
  <td rowspan="1"><input type="checkbox" name="checkboxes[]" value="5484948" class="checkbox" />&nbsp;2</td>
  <td rowspan="1" class="waybill_5484948">SPXRDU008892610140020092</td>
  <td rowspan="1"><p class="deliver_no_5484948">PO-211-19367840077430623</p></td>
</tr>
"""

DETAIL_HTML = """
<div class="groupa"><div class="title">交货信息</div><div class="content">
  <div class="rows">收件国家：美国</div>
  <div class="rows">收件名称：******</div>
  <!-- <div class="rows">收件地址：美国 Texas DALLAS ******</div> -->
  <div class="rows">收件地址：******</div>
  <div class="rows" style="padding-left: 63px;line-height: 22px;">  DALLAS Texas  </div>
  <div class="rows">收件邮编：75217-2625</div>
</div></div>
<div class="title">订单处理时效</div>
"""


def test_parse_rows():
    rows = parse_rows(ROW_HTML, "634418228526171", "BHY")
    assert len(rows) == 2
    assert rows[0]["order_id"] == "5484949"
    assert rows[0]["ref_no"] == "PO-211-19706774477432635"
    assert rows[0]["shop"] == "BHY"
    assert rows[1]["order_id"] == "5484948"


def test_split_city_state():
    assert split_city_state("DALLAS Texas") == ("DALLAS", "Texas")
    assert split_city_state("APEX North Carolina") == ("APEX", "North Carolina")
    assert split_city_state("Sp**** ***** Nevada") == ("Sp**** *****", "Nevada")
    # 格式②：州两字母码（实测约 22% 的订单长这样）
    assert split_city_state("NATIONAL CITY CA") == ("NATIONAL CITY", "California")
    assert split_city_state("CIDRA PR") == ("CIDRA", "Puerto Rico")
    assert split_city_state("WASHINGTON DC") == ("WASHINGTON", "District of Columbia")
    # 只有城市 / 空串 / 假码 → 认不出州
    assert split_city_state("DALLAS") == ("", "")
    assert split_city_state("") == ("", "")
    assert split_city_state("SPRINGFIELD ZZ") == ("", "")


def test_zip_state():
    assert zip_state("75217-2625") == "Texas"
    assert zip_state("00739-3440") == "Puerto Rico"
    assert zip_state("20003-4700") == "District of Columbia"
    assert zip_state("91950") == "California"
    assert zip_state("") == ""


def test_parse_order_info_code_format():
    """格式②：可见行是「城市 + 两字母码」，注释行是「码 + 城市」。"""
    html = """
    <div class="title">交货信息</div><div class="content">
      <!-- <div class="rows">收件地址：美国 CA NATIONAL CITY ******</div> -->
      <div class="rows">收件地址：******</div>
      <div class="rows" style="padding-left: 63px;line-height: 22px;">******</div>
      <div class="rows" style="padding-left: 63px;line-height: 22px;">  NATIONAL CITY CA  </div>
      <div class="rows">收件邮编：91950</div>
    </div>
    <div class="title">订单处理时效</div>
    """
    assert parse_order_info(html) == ("NATIONAL CITY", "California", "91950")


def test_parse_order_info_zip_fallback():
    """两种文本行都没有 → 用 ZIP 兜底。"""
    html = """
    <div class="title">交货信息</div><div class="content">
      <div class="rows">收件地址：******</div>
      <div class="rows">收件邮编：10001</div>
    </div>
    <div class="title">订单处理时效</div>
    """
    _city, state, _zip = parse_order_info(html)
    assert state == "New York"


def test_parse_order_info():
    city, state, zipc = parse_order_info(DETAIL_HTML)
    assert (city, state) == ("DALLAS", "Texas")
    assert zipc == "75217-2625"


def test_parse_order_info_fallback_uses_state_first_line():
    """没有 padding-left 那行时，兜底吃注释里的「收件地址：美国 <州> <城市> ******」。"""
    html = DETAIL_HTML.replace(
        '<div class="rows" style="padding-left: 63px;line-height: 22px;">  DALLAS Texas  </div>', "")
    city, state, _ = parse_order_info(html)
    assert state == "Texas"


def test_parse_order_info_no_address():
    assert parse_order_info("<html>没有交货信息</html>") == ("", "", "")


def test_aggregate_basic():
    recs = [
        {"state": "Texas", "city": "DALLAS", "qty": 2, "addtime": "2026-01-05 10:00:00"},
        {"state": "Texas", "city": "DALLAS", "qty": 1, "addtime": "2026-01-06 10:00:00"},
        {"state": "Nevada", "city": "Reno", "qty": 3, "addtime": "2026-02-01 10:00:00"},
        {"state": "Nowhere", "city": "X", "qty": 9, "addtime": "2026-02-01 10:00:00"},  # 非美国州 → 丢弃
    ]
    agg = aggregate(recs)
    assert agg["orders"] == 3
    assert agg["qty"] == 6
    assert [s["name"] for s in agg["states"]] == ["Texas", "Nevada"]
    assert agg["states"][0]["orders"] == 2
    assert agg["states"][0]["code"] == "TX"
    assert abs(agg["states"][0]["share"] - 2 / 3) < 1e-6
    assert agg["states"][0]["cities"][0] == {"name": "DALLAS", "orders": 2}
    assert agg["topCities"][0]["name"] == "DALLAS"
    assert [m["month"] for m in agg["months"]] == ["2026-01", "2026-02"]
    assert agg["months"][0]["topStates"] == [{"name": "Texas", "orders": 2}]


def test_aggregate_empty():
    agg = aggregate([])
    assert agg["orders"] == 0 and agg["states"] == [] and agg["months"] == []


def test_states_cover_map_names():
    """州名必须与 public/map/usa.json 的 feature name 对得上（地图才不会整片空白）。"""
    import json
    mapfn = os.path.join(os.path.dirname(HERE), "..", "..", "temu-admin", "public", "map", "usa.json")
    mapfn = os.path.abspath(mapfn)
    if not os.path.exists(mapfn):
        pytest.skip("usa.json 不在（前端资源未同步）")
    names = {f["properties"]["name"] for f in json.load(open(mapfn, encoding="utf-8"))["features"]}
    missing = [s for s in CODE if s not in names]
    assert not missing, "地图底图缺这些州：%s" % missing


def test_month_slices():
    """按月切片：全区间深分页会把服务端拖死，所以 list 步骤必须按月切。"""
    import importlib.util
    runfn = os.path.join(os.path.dirname(HERE), "run_pw_order_geo.py")
    spec = importlib.util.spec_from_file_location("run_pw_order_geo", runfn)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    got = mod.month_slices("2026-01-01", "2026-03-15")
    assert got == [("2026-01-01", "2026-01-31"),
                   ("2026-02-01", "2026-02-28"),
                   ("2026-03-01", "2026-03-15")]
    # 单月内 / 跨年
    assert mod.month_slices("2026-01-05", "2026-01-20") == [("2026-01-05", "2026-01-20")]
    assert mod.month_slices("2025-12-01", "2026-01-31") == [("2025-12-01", "2025-12-31"),
                                                            ("2026-01-01", "2026-01-31")]
