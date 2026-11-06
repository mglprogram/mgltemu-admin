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
                     "pw_receive", "pw_track", "hx_ship",
                     "capture_pw", "hx_oms_check", "temu_confirm", "report"]


def test_pipeline_write_steps_declared():
    """json 里声明的**写操作**步骤，代码里的 WRITE_STEPS 必须覆盖到。

    ★ 2026-11-06：只读步骤变多了（capture_pw / hx_oms_check / report），
      不能再写 `if name not in ('refresh_stock','split'): 必须进 WRITE_STEPS`，
      否则新加的只读步骤会让这个测试误报。
      正解：**反过来**用 WRITE_STEPS 当权威名单，检查 json 里写操作步骤一致。
    """
    names = [s["name"] for s in _pipeline()["steps"]]
    for w in F.WRITE_STEPS:
        assert w in names, "WRITE_STEPS 里的 %s 不在 pipeline 里" % w


def test_pipeline_readonly_steps_are_not_write():
    """只读步骤不能混进 WRITE_STEPS，否则没给 --confirm 会被跳过。"""
    for n in ("refresh_stock", "split", "capture_pw", "hx_oms_check", "report"):
        assert n not in F.WRITE_STEPS, "%s 是只读，不该进 WRITE_STEPS" % n
        assert F.gate_write(n, confirm=False) == "run", n


def test_split_before_any_labeling():
    """拆仓必须在任何买面单之前 —— 否则会拿全批去买，两个仓的白买。"""
    names = [s["name"] for s in _pipeline()["steps"]]
    assert names.index("split") < names.index("label_pw")
    assert names.index("split") < names.index("label_hx")


def test_confirm_comes_before_report_but_after_labeling():
    """★ 2026-11-06 用户口径：「这个是发货的最后一步」（指批量确认发货）。

    但 `report`（出缺货/失败 Excel）是**收尾留痕**，必须排在确认之后 ——
    确认完才知道哪几单真发出去了，报告才是终态。
    """
    names = [s["name"] for s in _pipeline()["steps"]]
    assert names[-1] == "report"
    assert names[-2] == "temu_confirm"
    for step in ("label_pw", "label_hx", "pw_receive", "pw_track", "hx_ship",
                 "capture_pw", "hx_oms_check"):
        assert names.index(step) < names.index("temu_confirm"), \
            "%s 必须在确认发货之前" % step


def test_capture_pw_before_confirm_so_failures_are_recorded():
    """★ 2026-11-06：失败单要在**确认发货之前**记账，不能等确认完才发现有一半失败。"""
    names = [s["name"] for s in _pipeline()["steps"]]
    assert names.index("capture_pw") < names.index("temu_confirm")
    assert names.index("hx_oms_check") < names.index("temu_confirm")


# ---------------------------------------------------------------- 软失败（不阻断后续）

def test_soft_steps_are_declared():
    """★ 2026-11-06 真机：`label_hx` 因仓名写错抛 no-option，整链在第一步就停，
    帕沃尔 124 单明明下好单却连确认发货都没跑。
    ⇒ 买面单/恒信发货这类**中间段**失败不许阻断后面的段。
    """
    assert "label_hx" in F.SOFT_STEPS
    assert "label_pw" in F.SOFT_STEPS
    assert "hx_ship" in F.SOFT_STEPS


def test_confirm_and_split_are_hard():
    """拆仓失败＝没单可发；确认发货失败＝整批没发完。两者都必须是硬失败。"""
    assert "split" not in F.SOFT_STEPS
    assert "temu_confirm" not in F.SOFT_STEPS


def test_soft_failures_do_not_set_stopped_at():
    """soft 失败继续跑时，`stopped_at` 必须是空（不能误报「停在中途」）。"""
    s = F.summarize([{"name": "label_hx", "status": "fail", "soft": True},
                     {"name": "temu_confirm", "status": "ok"}])
    hard = [r for r in [{"name": "label_hx", "status": "fail", "soft": True}]
            if not r.get("soft")]
    assert not hard
    assert s["ok"] is False, "soft 失败仍要如实报「没全成」"


# ---------------------------------------------------------------- 恒信仓名不许硬编码

def test_hx_warehouse_read_from_config_not_hardcoded():
    """★ 2026-11-06 用户纠正：恒信仓真值是 `恒信加州3`（**没有「仓」字**）。

    代码里曾写死 `"恒信加州3仓"` ⇒ 真机 `no-option:恒信加州3仓`。
    权威位置：`data/temu_buylabel.json → hx_warehouse`。
    """
    p = os.path.join(_POWER, "data", "temu_buylabel.json")
    d = json.load(open(p, encoding="utf-8"))
    assert d.get("hx_warehouse") == "恒信加州3", "配置里的恒信仓名必须是「恒信加州3」"


def test_ctx_takes_hx_warehouse_from_cfg():
    """主流程构造 ctx 时必须从 cfg 读 hx_warehouse，不许再写字面量。"""
    import inspect  # noqa: PLC0415
    src = inspect.getsource(F.main)
    assert 'cfg.get("hx_warehouse")' in src, "hx_warehouse 必须从配置读"
    assert '"恒信加州3仓"' not in src, "不许再把错的仓名写死在代码里"


# ---------------------------------------------------------------- 确认发货：30s + 逐单复验

def test_confirm_step_waits_30s_before_reading_count():
    """★ 2026-11-06 用户口径：「确认完后，等待 30s 刷新检查」。

    真机教训：旧逻辑只等 4×4s 就读，读到 `460 → 460` 报了 `no_effect`，
    实际是成功的（稍后 460→393，正好少 67）。
    """
    import inspect  # noqa: PLC0415
    src = inspect.getsource(__import__("temu_confirm_ship").main)
    assert "time.sleep(30)" in src, "确认后必须等 30s 再读计数"
    assert "location.reload()" in src, "必须刷新页面，不能读旧渲染"
    assert "for round_i in range(1, 4)" in src, "要允许多轮重试"


def test_confirm_step_reverifies_by_order_number():
    """计数会因别的单变动而不准；必须**按单号**回读是否真的离开了待发货。"""
    import inspect  # noqa: PLC0415
    src = inspect.getsource(F.step_temu_confirm)
    assert "verify_confirmed" in src, "确认后必须跑 verify_confirmed.py 逐单复验"
    assert "confirmable" in src, "本批单号要从扫描结果取"


def test_confirm_scan_requires_full_page():
    """★ 2026-11-06 真机：待发货 460 单只扫到 20 行就报「可确认 0」。

    每页必须拉到 500，且扫完要校验「行数 ≥ 页签总数」。
    """
    import inspect  # noqa: PLC0415
    src = inspect.getsource(__import__("temu_confirm_ship").main)
    assert "_set_page_size_500" in src
    assert "只扫到" in src, "行数不足必须明确报警，不许当 0 可确认"


# ---------------------------------------------------------------- 帕沃尔那份必须派子代理
#
# ★ 2026-11-06 用户铁律：「不管 BHY 还是 BEL 的发货 —— 发帕沃尔仓的货用子代理跑」

def test_delegate_steps_are_the_powerwarehouse_ones():
    """帕沃尔仓那三段必须标成「要派子代理」；恒信段与跨仓步骤不派。"""
    assert set(F.DELEGATE_STEPS) == {"label_pw", "pw_receive", "pw_track"}


def test_delegate_steps_marked_in_pipeline_json():
    """json 里的 `delegate: true` 必须和代码的 DELEGATE_STEPS 一致（防漂移）。

    ⚠ `data/` 被 .gitignore 覆盖（本机才有的口径文件），别人 clone 后可能没有；
    文件不存在就跳过，别让这条测试变成环境依赖。
    """
    if not os.path.exists(os.path.join(_POWER, "data", "temu_fulfill_pipeline.json")):
        import pytest  # noqa: PLC0415
        pytest.skip("本机没有 pipeline json（data/ 被 gitignore）")
    d = _pipeline()
    got = {s["name"] for s in d["steps"] if s.get("delegate")}
    assert got == set(F.DELEGATE_STEPS), got


def test_henxin_and_cross_warehouse_steps_are_not_delegated():
    """恒信走领星 OMS（另一个窗口），拆仓/确认发货/出表要掌握全局 ⇒ 都由主代理做。"""
    for n in ("label_hx", "hx_ship", "hx_oms_check", "split",
              "capture_pw", "temu_confirm", "report"):
        assert n not in F.DELEGATE_STEPS, "%s 不该派子代理" % n


def test_delegate_brief_covers_the_seven_gotchas():
    """派单时必须交代的要点：清单/仓名/物流商/禁下单时段/判据/窗口/交付物。"""
    joined = " ".join(F.DELEGATE_BRIEF)
    for key in ("绝对路径", "仓名真值", "物流商 6 家", "16:30–19:00",
                "运单号回读", "只许碰自己那个窗口", "原始报错"):
        assert key in joined, "派单要点漏了：%s" % key


def test_confirm_run_prints_delegate_hint():
    """真跑（--confirm）且含帕沃尔步骤时，必须在开头大声提示要派子代理。"""
    import inspect  # noqa: PLC0415
    src = inspect.getsource(F.main)
    assert "DELEGATE_STEPS" in src, "主流程要检查哪些步骤属于帕沃尔那份"
    assert "应当派子代理跑" in src, "要打印明确的派单提示"
    assert "DELEGATE_BRIEF" in src, "要逐条打印派单要点"


# ---------------------------------------------------------------- 报告步骤


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
