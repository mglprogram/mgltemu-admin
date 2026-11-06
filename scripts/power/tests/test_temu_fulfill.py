# -*- coding: utf-8 -*-
"""test_temu_fulfill.py — TEMU 发货整链的**编排逻辑**测试（纯函数，不碰浏览器）。

钉住的都是会静默出错的地方：
  · `select_steps` 写错步骤名必须**抛错**，不能静默跳过（否则"跑完了"其实没跑）
  · 写操作步骤没有 `--confirm` 必须**跳过**而不是假装跑过
  · `summarize` 遇到失败要**停在第一步失败处**，且 ok=False
  · pipeline json 的步骤名必须都有实现（否则跑到那步才炸）
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
sys.path.insert(0, _POWER)

import run_temu_fulfill as F  # noqa: E402


STEPS = [
    {"name": "refresh_stock"},
    {"name": "split"},
    {"name": "label_pw"},
    {"name": "label_hx"},
    {"name": "pw_receive"},
    {"name": "pw_track"},
    {"name": "hx_ship"},
    {"name": "temu_confirm"},
]


# ---------------------------------------------------------------- select_steps


def test_select_all_by_default():
    got = [s["name"] for s in F.select_steps(STEPS)]
    assert got == [s["name"] for s in STEPS]


def test_select_from_to_inclusive():
    got = [s["name"] for s in F.select_steps(STEPS, frm="label_pw", to="pw_track")]
    assert got == ["label_pw", "label_hx", "pw_receive", "pw_track"]


def test_select_only_keeps_definition_order():
    """--only 的顺序按定义走，不按用户输入顺序（避免"先跑确认再跑买面单"）。"""
    got = [s["name"] for s in F.select_steps(STEPS, only="temu_confirm,split")]
    assert got == ["split", "temu_confirm"]


def test_select_unknown_only_raises():
    try:
        F.select_steps(STEPS, only="split,nope")
    except ValueError as ex:
        assert "nope" in str(ex)
    else:
        raise AssertionError("写错步骤名必须抛错，不能静默跳过")


def test_select_unknown_from_raises():
    try:
        F.select_steps(STEPS, frm="nope")
    except ValueError:
        pass
    else:
        raise AssertionError("--from 写错必须抛错")


def test_select_reversed_range_raises():
    try:
        F.select_steps(STEPS, frm="pw_track", to="split")
    except ValueError:
        pass
    else:
        raise AssertionError("from 在 to 之后必须抛错")


# ---------------------------------------------------------------- gate_write


def test_write_steps_need_confirm():
    for n in ("label_pw", "label_hx", "pw_receive", "pw_track", "hx_ship", "temu_confirm"):
        assert F.gate_write(n, confirm=False) == "skip", n
        assert F.gate_write(n, confirm=True) == "run", n


def test_readonly_step_runs_without_confirm():
    """拆仓只读，不需要 --confirm。"""
    assert F.gate_write("split", confirm=False) == "run"


# ---------------------------------------------------------------- summarize


def test_summarize_all_ok():
    s = F.summarize([{"name": "split", "status": "ok"},
                     {"name": "label_pw", "status": "ok"}])
    assert s["ok"] is True
    assert s["ran"] == ["split", "label_pw"]
    assert not s["failed"]


def test_summarize_records_first_failure_and_stops():
    s = F.summarize([{"name": "split", "status": "ok"},
                     {"name": "label_pw", "status": "fail", "why": "x"},
                     {"name": "label_hx", "status": "skip"}])
    assert s["ok"] is False
    assert s["stopped_at"] == "label_pw"
    assert s["failed"] == ["label_pw"]
    assert s["skipped"] == ["label_hx"]


def test_summarize_skips_do_not_fail_the_run():
    """没给 --confirm 时写操作被跳过 —— 那是**有意**跳过，不算失败。"""
    s = F.summarize([{"name": "split", "status": "ok"},
                     {"name": "label_pw", "status": "skip"}])
    assert s["ok"] is True


# ---------------------------------------------------------------- pipeline json


def _pipeline():
    p = os.path.join(_POWER, "data", "temu_fulfill_pipeline.json")
    return json.load(open(p, encoding="utf-8"))


def test_pipeline_file_exists_and_has_steps():
    d = _pipeline()
    names = [s["name"] for s in d.get("steps") or []]
    assert names, "整链 json 必须有 steps"


def test_every_pipeline_step_has_impl():
    """步骤名必须都有实现，否则跑到那步才炸（PL-002 同义）。"""
    d = _pipeline()
    for s in d["steps"]:
        assert s["name"] in F.STEPS, "步骤 %s 没有实现" % s["name"]


def test_pipeline_order_is_the_real_one():
    """顺序是当天真机跑通的顺序，改动要同步现场记录。"""
    names = [s["name"] for s in _pipeline()["steps"]]
    assert names == ["refresh_stock", "split", "label_pw", "label_hx",
                     "pw_receive", "pw_track", "hx_ship", "temu_confirm"]


def test_pipeline_write_steps_declared():
    """json 里声明的写操作步骤，代码里的 WRITE_STEPS 必须覆盖到。"""
    d = _pipeline()
    for s in d["steps"]:
        if s["name"] in ("refresh_stock", "split"):
            continue
        assert s["name"] in F.WRITE_STEPS, "%s 是写操作，必须进 WRITE_STEPS" % s["name"]


def test_split_before_any_labeling():
    """拆仓必须在任何买面单之前 —— 否则会拿全批去买，两个仓的白买。"""
    names = [s["name"] for s in _pipeline()["steps"]]
    assert names.index("split") < names.index("label_pw")
    assert names.index("split") < names.index("label_hx")


def test_confirm_comes_last():
    """确认发货必须在最后：它要求前面所有单都已有运单号。"""
    names = [s["name"] for s in _pipeline()["steps"]]
    assert names[-1] == "temu_confirm"


# ---------------------------------------------------------------- 买面单必须同进程


def test_label_uses_single_process_pipeline():
    """买面单必须在**一个进程**里跑完 fill_dims→fill_logistics→place_only。

    ★ 2026-11-06 差点真跑炸：批量页只在同进程内共享（`_opened_targets` 是进程内状态）。
      原来把三段拆成三个子进程，`--reuse-page` 找不到页就会新开空页 ⇒
      `fill_logistics` / `place_only` 在空页上操作或直接失败。
      正解＝写临时 steps json + `--action pipeline`，单进程串跑。
    """
    import inspect  # noqa: PLC0415
    src = inspect.getsource(F._label_batch)
    assert '"--action", "pipeline"' in src or "'--action', 'pipeline'" in src, \
        "买面单必须走 --action pipeline（同进程），不能拆成多个子进程"
    assert "fill_dims" in src and "fill_logistics" in src and "place_only" in src


def test_label_steps_are_three_and_ordered():
    """三段顺序固定：先填四维（否则物流算不出来），再填物流，最后提交。"""
    import inspect  # noqa: PLC0415
    src = inspect.getsource(F._label_batch)
    i_dims = src.index("fill_dims")
    i_log = src.index("fill_logistics")
    i_place = src.index("place_only")
    assert i_dims < i_log < i_place


def test_label_pw_does_not_include_yanwen():
    """帕沃尔那份不含燕文；含燕文的是恒信那份（7 家）。"""
    import inspect  # noqa: PLC0415
    src = inspect.getsource(F.step_label_pw)
    assert "hx_carriers" not in src, "帕沃尔那份不能用恒信的物流商清单"
    src_hx = inspect.getsource(F.step_label_hx)
    assert "hx_carriers" in src_hx


# ---------------------------------------------------------------- 库存前置


def test_refresh_stock_is_first():
    """拉最新库存必须是第一步 —— 拆仓依赖它，晚了就按旧库存拆。"""
    names = [s["name"] for s in _pipeline()["steps"]]
    assert names[0] == "refresh_stock"


def test_refresh_stock_is_readonly():
    """抓库存只读，不需要 --confirm。"""
    assert F.gate_write("refresh_stock", confirm=False) == "run"


def test_refresh_stock_covers_both_sources():
    """两个库存源都要抓：帕沃尔 + 恒信领星。缺一个就会按旧数拆仓。"""
    import inspect  # noqa: PLC0415
    src = inspect.getsource(F.step_refresh_stock)
    assert "fetch_pw_stock" in src, "必须抓帕沃尔库存"
    assert "fetch_linxing_stock" in src, "必须抓恒信领星库存"


def test_refresh_stock_updates_stock_track():
    """★ 2026-11-06 用户追问「发货的第一步，更新系统的跟踪库存」。

    只抓两个源**不算**更新跟踪库存：面板看的 `stock-track.json` 由
    `fetch_stock_track.py` 聚合生成，漏跑它跟踪页就还是旧数（第一版就是这个漏）。
    """
    import inspect  # noqa: PLC0415
    src = inspect.getsource(F.step_refresh_stock)
    assert "fetch_stock_track" in src, "必须跑 fetch_stock_track.py 聚合到库存跟踪"


def test_stock_track_runs_after_sources():
    """聚合必须在两个源之后：否则拿旧源聚合，等于白跑。

    只比对 **`srcs` 列表里**的顺序（docstring 里也会出现这些文件名，
    直接 `str.index` 会命中注释、测出假结果——第一次就是这样误报的）。
    """
    import inspect  # noqa: PLC0415
    src = inspect.getsource(F.step_refresh_stock)
    i_list = src.index("srcs = [")
    i_end = src.index("]", i_list)
    block = src[i_list:i_end]
    i_pw = block.index("fetch_pw_stock")
    i_lx = block.index("fetch_linxing_stock")
    i_tr = block.index("fetch_stock_track")
    assert i_pw < i_tr, "帕沃尔源必须在聚合之前"
    assert i_lx < i_tr, "恒信源必须在聚合之前"


# ---------------------------------------------------------------- 仓名映射


def test_map_warehouse_bel_keeps_digit_three():
    """BEL 下拉真值就是「加州3仓」（数字 3），原样返回。"""
    cfg = {"stores": {"BEL": {"warehouse_default": "加州3仓"}}}
    assert F.map_warehouse("BEL", "加州3仓", cfg) == "加州3仓"


def test_map_warehouse_bhy_maps_to_chinese_three():
    """★ 2026-11-06 用户两次纠正：BHY 是「加州三仓」（中文三）。

    帕沃尔库存源两个店都写「加州3仓」，BHY 页面下拉只认「加州三仓」；
    不映射就 no-option:加州3仓（实测）。
    """
    cfg = {"stores": {"BHY": {"warehouse_map": {"加州3仓": "加州三仓"}}}}
    assert F.map_warehouse("BHY", "加州3仓", cfg) == "加州三仓"


def test_map_warehouse_unmapped_passthrough():
    """没配映射就原样返回，绝不猜。"""
    assert F.map_warehouse("BHY", "kansas仓", {"stores": {"BHY": {}}}) == "kansas仓"
    assert F.map_warehouse("BEL", "kansas仓", {}) == "kansas仓"


def test_bel_and_bhy_warehouse_names_are_different_in_config():
    """两店仓名写法确实不同，配置里必须各自写清（防有人统一成一个）。"""
    p = os.path.join(_POWER, "data", "temu_buylabel.json")
    d = json.load(open(p, encoding="utf-8"))
    bel = d["stores"]["BEL"].get("warehouse_default")
    bhy = d["stores"]["BHY"].get("warehouse_default")
    assert bel == "加州3仓", "BEL 应是数字 3 的「加州3仓」"
    assert bhy == "加州三仓", "BHY 应是中文三的「加州三仓」"
    assert bel != bhy, "两店仓名写法必须区分"


def test_bhy_has_warehouse_map_from_stock_source():
    """BHY 必须有「加州3仓→加州三仓」映射，否则拆仓结果进不了下拉。"""
    p = os.path.join(_POWER, "data", "temu_buylabel.json")
    d = json.load(open(p, encoding="utf-8"))
    mp = d["stores"]["BHY"].get("warehouse_map") or {}
    assert mp.get("加州3仓") == "加州三仓"


def test_bhy_kansas_drops_the_cang_suffix():
    """★ 2026-11-06 用户贴图：BHY 下拉里 kansas 仓写「kansas」，没有「仓」字。"""
    cfg = {"stores": {"BHY": {"warehouse_map": {"kansas仓": "kansas"}}}}
    assert F.map_warehouse("BHY", "kansas仓", cfg) == "kansas"


def test_bel_kansas_keeps_cang_suffix():
    """BEL 下拉与库存源同名，带「仓」字。"""
    assert F.map_warehouse("BEL", "kansas仓", {}) == "kansas仓"


def test_bhy_map_covers_both_warehouses():
    """BHY 两个发货仓都必须有映射，漏一个就 no-option。"""
    p = os.path.join(_POWER, "data", "temu_buylabel.json")
    d = json.load(open(p, encoding="utf-8"))
    mp = d["stores"]["BHY"].get("warehouse_map") or {}
    assert mp.get("加州3仓") == "加州三仓", "加州3仓 → 加州三仓"
    assert mp.get("kansas仓") == "kansas", "kansas仓 → kansas"


# ---------------------------------------------------------------- 取件日期：各框各选最早


def test_pick_earliest_date_js_sorts_and_filters_greyed():
    """★ 2026-11-06 用户口径（两次纠正）：

        「不需要时间统一，只要有更早的时间，就选更早的时间」

    ⇒ 取件日期 JS 必须：① 过滤「非营业日」/ disabled；② 按文本排序后取**最早**。
    绝不允许"统一成一个日期"或"取最后一个"。
    """
    import inspect  # noqa: PLC0415
    import re as _re  # noqa: PLC0415
    src = inspect.getsource(__import__("temu_buylabel_driver"))
    i = src.index("_JS_PICK_EARLIEST_DATE")
    block = src[i:i + 2500]
    assert "非营业日" in block, "必须过滤非营业日"
    assert "disab" in block, "必须过滤 disabled 项"
    assert "sort" in block, "必须排序"
    assert "cands[0]" in block, "必须取最早那一个（下标 0），不能取最后一个"


def test_pickup_date_loop_fills_every_blank():
    """每个空白日期框都要各自填一次（不是只填第一个，也不是统一成一个值）。"""
    import inspect  # noqa: PLC0415
    src = inspect.getsource(__import__("temu_buylabel_driver"))
    i = src.index("_JS_PICKUP_REMAIN")
    # 用「填到没有空白为止」的循环
    # 用「填到没空白为止」的循环：driver 里是
    #   for i in range(1, len(plan) + 3):
    #       pos = _JS_FIND_PICKUP_DATE_FIELD → 点开 → _JS_PICK_EARLIEST_DATE
    j = src.index("filled_n = 0")
    block = src[j:j + 1800]
    assert "for i in range" in block, "必须循环填完所有空白框"
    assert "_JS_FIND_PICKUP_DATE_FIELD" in block, "每轮都要重查下一个空白框"
    assert "_JS_PICK_EARLIEST_DATE" in block, "每次都要取最早可用日期"
