# -*- coding: utf-8 -*-
"""买面单 pipeline 的**超时**与**"误以为下单成功"**闸门（2026-11-09 用户令落地）。

用户原话：「买面单的skill的pipeline流程，买面单时常异常，检查每一个环节，
防止再次超时，或者误以为下单成功」。

这份测试逐条钉住那次加固的判据（**纯离线**：不连浏览器、不点真钱）：

1. `judge_place_result`  —— 提交到底成没成，不再拿「批量页关了」当判据；
2. `judge_hint_residue`  —— 下单前还有「差异过大」红字就不许提交；
3. `step_budget_warn`    —— 某一步悄悄跑太久要当场点名；
4. `run_steps`           —— 超预算告警 / 看门狗到点带已完成步骤停下 / 不破坏既有语义；
5. `place()` 源码        —— 必须记提交证据、再判"未完成提示"；
6. `_wait_rows_filled`   —— 用"读到几行"当前馈信号，而不是固定 sleep；
7. `weight_fix`          —— 读不到行**不许**当"已匹配"（旧行为的正面修复）；
8. 入口开关             —— `--allow-hint-residue`、`--deadline-s` 真的接线到 perform。
"""
import inspect
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import pytest  # noqa: E402

import temu_buylabel_driver as tbd  # noqa: E402

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ─────────────────────────────────── ① judge_place_result ───────────────────────────
def test_place_result_ok_when_submitted_and_no_modal():
    ok, why = tbd.judge_place_result({"submitted": "submit:确认无误，继续下单(ok)"}, False)
    assert ok and why == []


def test_place_result_fails_when_no_submit_evidence():
    """核心：没点到提交钮就是没成 —— 哪怕批量页"关了"。"""
    ok, why = tbd.judge_place_result({"tab_closed": True}, True)
    assert not ok and "没有提交证据" in "；".join(why)


def test_place_result_fails_when_modal_left():
    """残留弹窗**写着未完成的话**＝没提交成。"""
    ok, why = tbd.judge_place_result(
        {"submitted": "submit:确认(ok)", "modal_left": 1, "modal_text": "请确认订单信息"}, False)
    assert not ok and "未完成" in "；".join(why)


@pytest.mark.parametrize("text", [
    "共 3 个订单，确认下单？",
    "请确认订单信息",
    "预约上门时间不能为空",
    "请选择取件时间",
])
def test_place_result_fails_on_unfinished_wording(text):
    """平台换个文案也认得出（原来只认「确认下单？」这一条精确标题）。"""
    ok, why = tbd.judge_place_result(
        {"submitted": "submit:确认(ok)", "modal_left": 1, "modal_text": text}, False)
    assert not ok, text


def test_place_result_tolerates_unrelated_modal_text():
    """无关的提示弹窗不该把一次成功的提交判成失败。"""
    ok, _ = tbd.judge_place_result(
        {"submitted": "submit:确认(ok)", "modal_left": 1, "modal_text": "物流细则：请阅读"}, False)
    assert ok


def test_place_result_never_uses_tab_closed_as_evidence():
    """`tab_closed` 两个方向都不准，不能参与判定（真值表两列都验）。"""
    for closed in (True, False):
        ok, _ = tbd.judge_place_result({"modal_left": 0}, closed)
        assert not ok


def test_place_result_counts_alone_are_not_enough():
    """只有"还有个弹窗"没有文案 → 不判失败、也不判成功（交回读）。"""
    ok, why = tbd.judge_place_result(
        {"submitted": "submit:确认(ok)", "modal_left": 2}, False)
    assert ok, why


# ─────────────────────────────────── ② judge_hint_residue ───────────────────────────
def test_hint_residue_by_rows():
    bad = tbd.judge_hint_residue({"rows": [
        {"ord": "PO-1", "mismatchHint": False},
        {"ord": "PO-2", "mismatchHint": True},
    ]})
    assert bad == ["PO-2"]


def test_hint_residue_falls_back_to_count():
    assert tbd.judge_hint_residue({"hint_count": 3}) == ["(共 3 单未列出单号)"]


def test_hint_residue_clean():
    assert tbd.judge_hint_residue({"rows": [{"ord": "PO-1"}], "hint_count": 0}) == []


def test_hint_residue_unreadable_does_not_block():
    """读不到（空视图）不拦 —— 不能因为读不到就永远下不了单。"""
    assert tbd.judge_hint_residue({}) == []
    assert tbd.judge_hint_residue(None) == []


# ─────────────────────────────────── ③ step_budget_warn ──────────────────────────────
def test_budget_warn_flags_overrun():
    msg = tbd.step_budget_warn("place_only", 900, {"place_only": 300})
    assert "超过预算" in msg and "3.0 倍" in msg


def test_budget_warn_silent_when_within():
    assert tbd.step_budget_warn("place_only", 100, {"place_only": 300}) == ""


def test_budget_warn_no_budget_configured():
    assert tbd.step_budget_warn("anything", 9999, None) == ""
    assert tbd.step_budget_warn("anything", 9999, {"other": 5}) == ""


# ─────────────────────────────────── ④ run_steps ──────────────────────────────────
class _Clock:
    def __init__(self, ticks):
        self.ticks = list(ticks)
        self.i = 0

    def __call__(self):
        v = self.ticks[min(self.i, len(self.ticks) - 1)]
        self.i += 1
        return v


def _collect():
    logs = []
    return logs, logs.append


def test_run_steps_reports_slow_step():
    logs, log = _collect()
    clock = _Clock([0, 500, 500, 501])       # plan 跑 500s
    out = tbd.run_steps([{"name": "plan"}], {}, lambda n, p: {"ok": 1},
                        log=log, clock=clock, budgets={"plan": 20})
    assert out["slow"] and "plan" in out["slow"][0]
    assert any("[耗时]" in x for x in logs)


def test_run_steps_watchdog_stops_before_hopeless_step():
    """总时长不够跑下一步时，**带着已完成步骤**停下并说明卡在哪。"""
    logs, log = _collect()
    clock = _Clock([0, 100, 100, 101])
    steps = [{"name": "plan"}, {"name": "fill_dims"}, {"name": "place_only"}]
    out = tbd.run_steps(steps, {}, lambda n, p: {}, log=log, clock=clock,
                        budgets={"plan": 10, "fill_dims": 300, "place_only": 300},
                        deadline_s=200)
    assert out.get("watchdog") is True and out.get("next_step") == "fill_dims"
    assert [s["name"] for s in out["steps"]] == ["plan"]
    assert any("[看门狗]" in x for x in logs)


def test_run_steps_watchdog_off_by_default():
    _, log = _collect()
    clock = _Clock([0, 1, 1, 2, 2, 3])
    out = tbd.run_steps([{"name": "plan"}, {"name": "verify"}], {},
                        lambda n, p: {}, log=log, clock=clock)
    assert out["count"] == 2 and "watchdog" not in out


def test_run_steps_still_merges_params_and_prefixes_errors():
    """加固不能碰既有语义：params 合并、失败带步骤号。"""
    seen = {}

    def perform(name, p):
        seen[name] = dict(p)
        if name == "boom":
            raise RuntimeError("x")
        return {}

    tbd.run_steps([{"name": "plan", "params": {"a": 2}}], {"a": 1, "b": 3}, perform,
                  log=lambda s: None)
    assert seen["plan"] == {"a": 2, "b": 3}

    with pytest.raises(RuntimeError) as ei:
        tbd.run_steps([{"name": "boom"}], {}, perform, log=lambda s: None)
    assert "步骤 1/1 boom 失败" in str(ei.value)


def test_run_steps_rejects_bad_step_params():
    with pytest.raises(RuntimeError):
        tbd.run_steps([{"name": "plan", "params": [1, 2]}], {}, lambda n, p: {},
                      log=lambda s: None)


# ─────────────────────────────────── ⑤ place() 源码闸门 ─────────────────────────────
def _place_src():
    return inspect.getsource(tbd.BuylabelDriver.place)


def test_place_records_submit_evidence():
    src = _place_src()
    assert "submitted" in src and "提交证据" in src
    # 返回体里必须有 submitted，供 judge_place_result 判
    assert '"submitted": submitted' in src, "place() 必须把提交证据返回给调用方"


def test_place_checks_modal_scan_not_only_one_title():
    src = _place_src()
    assert "_place_modal_scan" in src
    assert "_PLACE_UNFINISHED_RE" in src, "改版文案下的未完成态也要判"


def test_place_no_longer_claims_closed_means_success():
    src = _place_src()
    assert "关闭=下单成功" not in src, "旧的「关闭=下单成功」结论必须删掉"


def test_place_modal_scan_exists():
    assert hasattr(tbd.BuylabelDriver, "_place_modal_scan")


# ─────────────────────────────────── ⑥ _wait_rows_filled ────────────────────────────
class _RowDrv(tbd.BuylabelDriver):
    def __init__(self, counts):
        self.counts = list(counts)
        self.cfg = {}

    def _filled_row_count(self, page):
        return self.counts.pop(0) if self.counts else 0


def test_wait_rows_filled_returns_early_when_reached():
    d = _RowDrv([3, 47])
    assert d._wait_rows_filled(object(), 47, timeout_s=30) == 47


def test_wait_rows_filled_gives_up_at_deadline():
    d = _RowDrv([0])
    assert d._wait_rows_filled(object(), 5, timeout_s=0.05) == 0


def test_fill_batch_requires_all_rows_filled():
    """部分填上＝同样危险：必须报错，不许带着空重量往下走。"""
    src = inspect.getsource(tbd.BuylabelDriver._fill_batch)
    assert "_wait_rows_filled" in src, "不能再用固定 sleep 赌批量填充渲染完"
    assert "02b_batch_fill_partial" in src, "部分填上必须显式失败并取证"


# ─────────────────────────────────── ⑦ weight_fix 空读 ─────────────────────────────
def test_weight_fix_empty_scan_raises_not_passes():
    src = inspect.getsource(tbd.BuylabelDriver.weight_fix)
    assert "read_empty" not in src, "空读不许再被记成 read_empty 静默跳过"
    assert "03c_weight_fix_norows" in src and "raise RuntimeError" in src


def test_weight_fix_settle_requires_rows():
    """等红字稳定的循环里，「没有行」不能当成「没有红字=已匹配」。"""
    src = inspect.getsource(tbd.BuylabelDriver.weight_fix)
    assert "and rows_now" in src


# ─────────────────────────────────── ⑧ record 新旧任务 ─────────────────────────────
def test_task_at_epoch_parses_record_time():
    v = tbd._task_at_epoch("2026-11-09 11:11:20")
    assert tbd._task_at_epoch("garbage") == 0.0
    assert v > 0 and tbd._task_at_epoch("2026-11-09 11:11:21") - v == 1


def test_record_step_waits_for_new_task():
    """`record` 紧跟提交，不能把**上一批**的任务当本批成果（假一致）。"""
    src = inspect.getsource(tbd.perform)
    assert "_PIPELINE_LAST" in src
    assert "记录里还没有" in src


def test_pipeline_last_is_reset_before_place():
    src = inspect.getsource(tbd.perform)
    seg = src[src.find('if action == "place_only"'):]
    assert '_PIPELINE_LAST["at"] = time.time()' in seg


# ─────────────────────────────────── ⑨ 入口接线 ───────────────────────────────────
def test_entry_has_allow_hint_residue_flag():
    p = os.path.join(_HERE, "run_temu_buylabel.py")
    src = open(p, encoding="utf-8").read()
    assert "--allow-hint-residue" in src
    assert '"allow_hint_residue": args.allow_hint_residue' in src


def test_entry_has_deadline_flag():
    p = os.path.join(_HERE, "run_temu_buylabel.py")
    src = open(p, encoding="utf-8").read()
    assert "--deadline-s" in src
    assert "deadline_s=args.deadline_s" in src
    assert 'budgets=spec.get("step_budget_s")' in src


def test_pipeline_json_has_budget_for_every_step():
    p = os.path.join(_HERE, "data", "temu_buylabel_pipeline.json")
    if not os.path.exists(p):
        pytest.skip("pipeline json 缺失（data/ 在 .gitignore 里）")
    spec = json.load(open(p, encoding="utf-8"))
    names = [s["name"] for s in spec["steps"]]
    budgets = spec.get("step_budget_s") or {}
    assert names, "pipeline 不能没有步骤"
    missing = [n for n in names if n not in budgets]
    assert not missing, "这些步骤没有耗时预算：%s" % missing


# ─────────────────────────────────── ⑩ 传输错误白名单 ─────────────────────────────
@pytest.mark.parametrize("msg", [
    "TimeoutError()",
    "asyncio.TimeoutError",
    "Runtime.evaluate timed out",
    "Cannot find context with specified id",
    "Execution context was destroyed",
    "server rejected WebSocket connection: HTTP 500",
])
def test_transport_error_covers_timeouts(msg):
    """点完提交后的「渲染进程超时/断连」≠ 平台拒绝 —— 必须走回读，不能直接判失败。"""
    assert tbd.is_transport_error(RuntimeError(msg))


def test_transport_error_does_not_swallow_business_error():
    assert not tbd.is_transport_error(RuntimeError("这些单还没有运单号"))


# ─────────────────────────────────── ⑪ 记录抽屉不再固定等 ─────────────────────────
def test_record_drawer_uses_polling_not_fixed_sleep():
    src = inspect.getsource(tbd.BuylabelDriver._ensure_record_drawer)
    assert "_wait_records_on" in src
    assert "time.sleep(2.5)" not in src and "time.sleep(3.5)" not in src


def test_verify_retries_partial_missing():
    """部分缺号要等一轮再下结论（平台出单是异步的）。"""
    src = inspect.getsource(tbd.BuylabelDriver.verify)
    assert "平台出单是异步的" in src
    assert re.search(r"for _r in range\(2 if _miss", src), "缺号必须再读，不能一读就定论"
