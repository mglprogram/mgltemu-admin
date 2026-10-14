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

from pw_order_geo import (CODE, aggregate, parse_detail_products,        # noqa: E402
                          parse_order_info, parse_rows, split_city_state, zip_state)

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


# 2026-10-14 从 deliver.php?act=order_info&type=view&id=4689502 抓的真实片段（**一单两品**）
DETAIL_PRODUCTS_HTML = """
<div class="table-box"><div id="table" class="table">
<table id="table_0">
<tr><th>序号</th><th>Fnsku</th><th>产品名称</th><th>英文名称</th><th>平台SKU</th>
    <th>数量</th><th>货值</th><th>图片</th></tr>
<tr><td>1</td><td>2HEGDN10941967927</td><td>BS-202504-5SetA-M</td><td>BS-202504-5SetA-M</td>
    <td></td><td>1</td><td>200.00</td>
    <td class="manage_look_05"><img src="a.jpg"></td></tr>
<tr><td>2</td><td>2GLPX2H0941868457</td><td>BS-202504-5SetB-M</td><td>BS-202504-5SetB-M</td>
    <td></td><td>1</td><td>200.00</td>
    <td class="manage_look_05"><img src="b.jpg"></td></tr>
</table></div></div>
"""


def test_parse_detail_products_multi():
    items = parse_detail_products(DETAIL_PRODUCTS_HTML)
    assert len(items) == 2
    assert items[0]["product"] == "BS-202504-5SetA-M"
    assert items[0]["fnsku"] == "2HEGDN10941967927"
    assert items[0]["qty"] == 1
    assert items[1]["product"] == "BS-202504-5SetB-M"
    # 这些 TEMU 单的「平台SKU」实测全为空
    assert all(it["platform_sku"] == "" for it in items)


def test_parse_detail_products_empty_when_no_type_view():
    """没有产品表的页面（例如漏了 type=view 的那种）→ 返回空，不能瞎猜。"""
    assert parse_detail_products("<html><body>没有产品表</body></html>") == []


def test_style_of():
    import importlib.util
    runfn = os.path.join(os.path.dirname(HERE), "run_pw_order_geo.py")
    spec = importlib.util.spec_from_file_location("run_pw_order_geo2", runfn)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.style_of("BS2-202504-5SetB-S") == "BS2-202504-5SetB"
    assert mod.style_of("BH-102105-3BHL-XXL") == "BH-102105-3BHL"
    assert mod.style_of("BS-202504-5SetA-M") == "BS-202504-5SetA"
    # 没有尺码后缀 → 原样返回
    assert mod.style_of("BH-103903-2B") == "BH-103903-2B"
    assert mod.style_of("") == ""


def _load_run_module(tag="x"):
    import importlib.util
    runfn = os.path.join(os.path.dirname(HERE), "run_pw_order_geo.py")
    spec = importlib.util.spec_from_file_location("run_pw_order_geo_%s" % tag, runfn)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_attach_products_and_top_products():
    """面板/大屏用的「各州最畅销款」和「款榜单（含集中度 lift）」。"""
    mod = _load_run_module("attach")
    rows = [
        # TX 第 1 单：同款两个尺码 → 款 BH-102105-3BHL 只算 1 单
        {"state": "Texas", "shop": "BEL", "items": [
            {"product": "BH-102105-3BHL-L", "qty": 1},
            {"product": "BH-102105-3BHL-M", "qty": 1}]},
        # TX 第 2 单
        {"state": "Texas", "shop": "BEL", "items": [{"product": "BH-102105-3BHL-L", "qty": 1}]},
        {"state": "Texas", "shop": "BEL", "items": [{"product": "BLA-102104-5SetA-M", "qty": 1}]},
        # FL 1 单
        {"state": "Florida", "shop": "BHY", "items": [{"product": "BH-102105-3BHL-L", "qty": 1}]},
        # PR 2 单
        {"state": "Puerto Rico", "shop": "BEL", "items": [{"product": "BLA-102104-5SetA-M", "qty": 2}]},
        {"state": "Puerto Rico", "shop": "BEL", "items": [{"product": "BLA-102104-5SetA-L", "qty": 1}]},
    ]
    # 大盘：TX 3 / FL 1 / PR 2（共 6 单）
    states = [{"name": "Texas", "code": "TX", "orders": 3},
              {"name": "Florida", "code": "FL", "orders": 1},
              {"name": "Puerto Rico", "code": "PR", "orders": 2}]
    mod.attach_products(states, rows, top_n=3)
    tx = states[0]["topProducts"]
    assert tx[0]["name"] == "BH-102105-3BHL" and tx[0]["orders"] == 2
    assert {p["name"] for p in tx} == {"BH-102105-3BHL", "BLA-102104-5SetA"}

    tops = mod.build_top_products(rows, limit=10)
    by = {p["name"]: p for p in tops}
    bla = by["BLA-102104-5SetA"]
    # BLA 共 3 单：PR 2 / TX 1 → 头号州 PR 占 2/3；大盘 PR = 2/6 = 1/3 → lift = 2.0
    # ⚠ topStateShare / lift 在产物里是**四舍五入到 4 位 / 2 位**的展示值（为了压 JSON 体积），
    #   所以断言用 1e-3 / 0.01 的容差，不要用 1e-9。
    assert bla["orders"] == 3 and bla["topState"] == "Puerto Rico"
    assert abs(bla["topStateShare"] - 2 / 3) < 1e-3
    assert abs(bla["lift"] - 2.0) < 0.01
    bhl = by["BH-102105-3BHL"]
    # BHL 共 3 单：TX 2 / FL 1 → 头号州 TX 占 2/3；大盘 TX = 3/6 = 1/2 → lift = 4/3
    assert bhl["orders"] == 3 and bhl["topState"] == "Texas"
    assert abs(bhl["lift"] - 4 / 3) < 0.01
    assert [p["orders"] for p in tops] == sorted([p["orders"] for p in tops], reverse=True)


def test_build_product_index():
    """「产品出单查询」用的全量索引：短码 / 州 / 月度 / 城市 / Fnsku / lift。"""
    mod = _load_run_module("pidx")
    rows = [
        {"state": "Texas", "shop": "BEL", "city": "HOUSTON", "addtime": "2026-01-05 10:00:00",
         "items": [{"product": "BH-102105-3BHL-L", "fnsku": "A", "qty": 2}]},
        {"state": "Texas", "shop": "BEL", "city": "DALLAS", "addtime": "2026-02-05 10:00:00",
         "items": [{"product": "BH-102105-3BHL-M", "fnsku": "B", "qty": 1}]},
        {"state": "Puerto Rico", "shop": "BHY", "city": "SAN JUAN", "addtime": "2026-02-06 10:00:00",
         "items": [{"product": "BLA-102104-5SetA-M", "fnsku": "C", "qty": 3}]},
    ]
    idx = mod.build_product_index(rows)
    by = {p["name"]: p for p in idx}
    assert list(by) == ["BH-102105-3BHL", "BLA-102104-5SetA"]      # 按单量降序
    b = by["BH-102105-3BHL"]
    # 两个尺码两单 → 款级 2 单、件数 3；短码取数字段
    assert b["code"] == "102105"
    assert b["orders"] == 2 and b["qty"] == 3
    assert b["topState"] == "Texas" and b["topStateCode"] == "TX"
    assert [s["name"] for s in b["states"]] == ["Texas"]
    assert [m["month"] for m in b["months"]] == ["2026-01", "2026-02"]
    assert set(b["fnskus"]) == {"A", "B"}
    assert b["shops"] == {"BEL": 2}
    assert {c["name"] for c in b["cities"]} == {"HOUSTON", "DALLAS"}
    # BLA 只 1 单且全在 PR（占比 100%），大盘 PR = 1/3 → lift = 3.0
    bla = by["BLA-102104-5SetA"]
    assert bla["code"] == "102104" and bla["topStateShare"] == 1.0
    assert abs(bla["lift"] - 3.0) < 0.01


def test_product_state_agg_dedupes_style_within_one_order():
    """一单里同款不同尺码 → 该款只算 1 单；不同款 → 各算 1 单。"""
    import importlib.util
    runfn = os.path.join(os.path.dirname(HERE), "run_pw_order_geo.py")
    spec = importlib.util.spec_from_file_location("run_pw_order_geo3", runfn)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rows = [
        # 一单两品，**同款不同尺码** → 款 BS2-207202-5SetB 记 1 单，两个尺码各 1 单
        {"state": "Texas", "shop": "BEL", "items": [
            {"product": "BS2-207202-5SetB-L", "fnsku": "A", "qty": 1},
            {"product": "BS2-207202-5SetB-XL", "fnsku": "B", "qty": 1}]},
        # 一单两品，**不同款** → 两个款各 1 单
        {"state": "Texas", "shop": "BEL", "items": [
            {"product": "BS-102503-5SetA-M", "fnsku": "C", "qty": 2},
            {"product": "BS-102503-5SetB-M", "fnsku": "D", "qty": 1}]},
        # 单品
        {"state": "Florida", "shop": "BHY", "items": [
            {"product": "BS-102503-5SetA-M", "fnsku": "C", "qty": 3}]},
    ]
    agg = mod.product_state_agg(rows)
    assert agg["multiItems"] == 2
    assert agg["stateOrder"]["Texas"] == 2 and agg["stateOrder"]["Florida"] == 1
    # 款：BS2-207202-5SetB 1 单；BS-102503-5SetA 2 单；BS-102503-5SetB 1 单
    assert agg["styles"]["BS2-207202-5SetB"]["orders"] == 1
    assert agg["styles"]["BS-102503-5SetA"]["orders"] == 2
    assert agg["styles"]["BS-102503-5SetB"]["orders"] == 1
    # 尺码级：BS-102503-5SetA-M 出现在 2 单里，件数 2+3
    assert agg["products"]["BS-102503-5SetA-M"]["orders"] == 2
    assert agg["products"]["BS-102503-5SetA-M"]["qty"] == 5
    assert agg["products"]["BS-102503-5SetA-M"]["states"]["Texas"] == 1
    assert agg["products"]["BS-102503-5SetA-M"]["states"]["Florida"] == 1
