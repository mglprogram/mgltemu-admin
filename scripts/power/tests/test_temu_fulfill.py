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
    assert names == ["split", "label_pw", "label_hx", "pw_receive",
                     "pw_track", "hx_ship", "temu_confirm"]


def test_pipeline_write_steps_declared():
    """json 里声明的写操作步骤，代码里的 WRITE_STEPS 必须覆盖到。"""
    d = _pipeline()
    for s in d["steps"]:
        if s["name"] == "split":
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
