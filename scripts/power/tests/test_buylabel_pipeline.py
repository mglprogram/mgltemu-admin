# -*- coding: utf-8 -*-
"""test_buylabel_pipeline.py — 买面单「流水线步骤」的离线回归测试（不起浏览器、不花钱）。

来历（用户 2026-09-16）：「你有加载和 pipeline 相关的 SKILL 吗？利用它去固化」。
按 ai-python-methodology「流水线定义规定」D1：步骤顺序落 `data/temu_buylabel_pipeline.json` 的
`steps`，代码只提供 `perform(action, params)`。本文件把这条契约钉死，防两类退化：
    ① **顺序又漂回代码/正文**：steps 为空、缺 name、name 不是 driver 的 action 字面量（PL-002）
    ② **编排器乱序/吞错**：`run_steps` 必须按序执行、失败即停且带上步骤号（不许静默继续）
"""
import json
import os
import re
import sys

import pytest

_POWER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # scripts/power
sys.path.insert(0, os.path.join(_POWER, "lib"))

import temu_buylabel_driver as D                  # noqa: E402

STEP_CFG = os.path.join(_POWER, "data", "temu_buylabel_pipeline.json")
DRIVER = os.path.join(_POWER, "lib", "temu_buylabel_driver.py")


def _spec():
    with open(STEP_CFG, encoding="utf-8") as f:
        return json.load(f)


def _driver_text():
    with open(DRIVER, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- ① 数据侧契约
def test_steps_config_exists_and_shaped():
    """PL-001：steps 必须是非空数组，元素是对象且带非空 name、params 是对象。"""
    spec = _spec()
    steps = spec.get("steps")
    assert isinstance(steps, list) and steps, "steps 必须是非空数组"
    for i, st in enumerate(steps, 1):
        assert isinstance(st, dict), "steps[%d] 不是对象" % i
        assert isinstance(st.get("name"), str) and st["name"].strip(), "steps[%d] 缺 name" % i
        assert isinstance(st.get("params", {}), dict), "steps[%d].params 不是对象" % i


def test_step_names_are_real_driver_actions():
    """PL-002：steps[].name 必须是 driver 里存在的字面量（否则跑到那步才炸）。"""
    text = _driver_text()
    for i, st in enumerate(_spec()["steps"], 1):
        name = st["name"]
        assert name in text, "steps[%d] 动作悬空：'%s' 不在 driver 里" % (i, name)


def test_no_stray_step_ordinal_in_name():
    """步骤名只能是 ASCII 动作名（防有人把「第1步」这种散文塞进 name）。"""
    for st in _spec()["steps"]:
        assert re.fullmatch(r"[a-z_]+", st["name"]), "步骤名必须是 ASCII 动作名：%r" % st["name"]


def test_pipeline_keeps_money_step_before_confirm():
    """顺序语义：下单（place_only）必须早于确认发货（confirm）与核验（verify），否则会空转。"""
    names = [s["name"] for s in _spec()["steps"]]
    assert "place_only" in names and "confirm" in names and "verify" in names
    assert names.index("place_only") < names.index("confirm") < names.index("verify")


# ---------------------------------------------------------------- ③ 登录：正常点击，不许走捷径
def test_pipeline_logs_in_before_triage():
    """整链必须**自己把登录点完**再分诊（用户 2026-09-24：「要么直接关掉，要么要正常的点击登录」）。

    原来整链没有 auth 步 ⇒ 遇登录/认证页只能在下游报 `no-input`，把活推给人/上游。
    """
    names = [s["name"] for s in _spec()["steps"]]
    assert "auth" in names, "整链缺「正常点击登录」这一步"
    assert names.index("auth") < names.index("screen"), "登录必须在分诊之前（没登录分诊读不到东西）"


def test_auth_step_uses_the_proven_target_level_clicks():
    """auth 步必须走**目标级 CDP 的正常点击登录**（复用合作仓域 `ensure_auth` 的同一套实现）。

    为什么（2026-09-24 实测）：Playwright browser-level attach 在本域窗口上会握手卡死
    （`connect_over_cdp: Timeout 15000ms exceeded`，RL-013）⇒「点不过去」的真因之一。
    目标级那条已现场跑通：区域页点「商家中心」→ 勾条款 → 点「授权登录」→ 落到 orders.html（3 轮 ok）。
    """
    text = _driver_text()
    i = text.find('if action == "auth"')
    assert i > 0, "driver 没有 auth 动作"
    seg = text[i:i + 2000]
    assert "temu_abnormal_driver" in seg and "ensure_auth" in seg, "auth 步没复用已跑通的目标级点击实现"
    assert ".connect_over_cdp(" not in seg, "auth 步里出现了 browser-level attach 调用"


def test_no_login_shortcuts():
    """**反捷径闸门**（用户 2026-09-24 明确「我怀疑你现在走了捷径」）：本域不许出现任何
    "伪造登录态"的**调用**——注入 cookie / 搬 storage_state / 改 URL 骗过登录。

    合法做法只有两条：① 完全空白页（0 字、0 输入框）直接关掉（`ziniao_store.clean_dead_tabs`）；
    ② 真实的登录/认证页走正常点击登录（`run_temu_auth.py`）。
    判据只看**调用形态**（`xxx(` / `xxx=`），避免把"我们不这么做"这类说明性文字误判。
    """
    pats = ("add_cookies(", "set_cookie(", "network.setcookie", "storage_state=", "storage_state:")
    for rel in (os.path.join("lib", "temu_buylabel_driver.py"), os.path.join("lib", "ziniao_store.py")):
        low = open(os.path.join(_POWER, rel), encoding="utf-8").read().lower()
        for bad in pats:
            assert bad not in low, "%s 出现疑似伪造登录态的调用：%s" % (rel, bad)


# ---------------------------------------------------------------- ② 编排器行为
def test_shipped_config_runs_end_to_end_offline():
    """**用真正要跑的那份 json** 驱动 run_steps（perform 换成假的）——证明发布配置可执行、顺序对。"""
    calls = []
    spec = _spec()
    out = D.run_steps(spec["steps"], {"store": "BEL"},
                      lambda n, p: calls.append(n) or {"ok": n}, log=lambda *_a: None)
    assert calls == [s["name"] for s in spec["steps"]]
    assert out["count"] == len(spec["steps"])


def test_run_steps_executes_in_order_and_reports():
    """run_steps 按数组顺序调用 perform_fn，并逐步留痕（i/name/seconds）。"""
    calls = []

    def fake(name, params):
        calls.append(name)
        return {"done": name}

    steps = [{"name": "plan", "params": {}}, {"name": "fill", "params": {"file": "x.xlsx"}}]
    out = D.run_steps(steps, {"store": "BEL"}, fake, log=lambda *_a: None)
    assert calls == ["plan", "fill"], "必须严格按 steps 顺序"
    assert [s["name"] for s in out["steps"]] == ["plan", "fill"]
    assert out["count"] == 2
    assert all("seconds" in s for s in out["steps"])


def test_run_steps_merges_step_params_over_base():
    """steps[].params 覆盖基础参数（AI 唯一写区），且不改动调用方传入的 base。"""
    seen = {}

    def fake(name, params):
        seen.update(params)
        return {}

    base = {"store": "BEL", "file": "a.xlsx"}
    D.run_steps([{"name": "fill", "params": {"file": "b.xlsx"}}], base, fake, log=lambda *_a: None)
    assert seen["file"] == "b.xlsx", "步骤 params 应覆盖同名基础参数"
    assert seen["store"] == "BEL", "未覆盖的基础参数要保留"
    assert base["file"] == "a.xlsx", "不许就地改调用方传进来的 base"


def test_run_steps_stops_at_first_failure_with_step_number():
    """失败即停：抛出物必须点名「步骤 k/N <name>」，且后面的步骤不得执行（防连环花钱）。"""
    calls = []

    def fake(name, params):
        calls.append(name)
        if name == "place_only":
            raise RuntimeError("原始异常：拿不到批量页")
        return {}

    steps = [{"name": "plan", "params": {}}, {"name": "place_only", "params": {}},
             {"name": "confirm", "params": {}}]
    with pytest.raises(RuntimeError) as ei:
        D.run_steps(steps, {}, fake, log=lambda *_a: None)
    msg = str(ei.value)
    assert "步骤 2/3 place_only 失败" in msg
    assert "拿不到批量页" in msg, "原异常原文必须带上（分诊要用）"
    assert calls == ["plan", "place_only"], "失败后不得继续跑后面的步骤"


def test_run_steps_rejects_bad_steps():
    """PL-001 的运行期兜底：空 steps / 缺 name / params 非对象 一律拒（不静默跳过）。"""
    for bad in ([], [{"params": {}}], [{"name": "  "}], [{"name": "plan", "params": []}]):
        with pytest.raises(RuntimeError):
            D.run_steps(bad, {}, lambda n, p: {}, log=lambda *_a: None)


# ---------------------------------------------------------------- ④ 按分诊路线改道（2026-09-28）
# 来历：2026-09-28 当天 47 次跑 / 24 次失败。routes.json 早把路线定好了，但 pipeline **固定九步照跑**，
# 于是「没得买」「一部分已发货」「一个都没查到」这三类全被报成 HARD STOP 失败（含 2 张失败卡）。
def _screen_out(want, pending, cancelled, shipped, not_found, ready=None, rid="orders_ready"):
    return {"signals": {"page_kind": "orders", "want": want, "pending": pending,
                        "cancelled": cancelled, "shipped": shipped,
                        "skippable": cancelled + shipped, "not_found": not_found},
            "detail": {"ready": list(ready if ready is not None else [])},
            "route": {"id": rid, "steps": []}}


def test_route_noop_when_nothing_to_buy():
    """可买 0 单（28 单全已取消那种）→ 按路线 stop_report 正常结束，**不许**报失败。"""
    rp = D.pipeline_route_plan(_screen_out(28, 0, 28, 0, 0, rid="nothing_to_buy"))
    assert rp["verdict"] == "noop", rp
    assert "没有可买的" in rp["reason"] and "28" in rp["reason"]


def test_route_noop_when_all_shipped():
    """全已发货（＝已有面单/重复投递）同样是「没得买」，不该开失败卡。"""
    assert D.pipeline_route_plan(_screen_out(10, 0, 0, 10, 0, rid="nothing_to_buy"))["verdict"] == "noop"


def test_route_stop_when_orders_not_found():
    """一个都没查到（234 单那次）→ 在分诊步当场停下并点名，别拖到 fill 才报「没查到」。"""
    rp = D.pipeline_route_plan(_screen_out(234, 0, 0, 0, 234, rid="no_orders"))
    assert rp["verdict"] == "stop", rp
    assert "234" in rp["reason"] and "停下" in rp["reason"]


def test_route_narrow_excludes_shipped():
    """一部分已发货（172 单里 72 单已发货）→ 按路线剔除，只对剩下能买的继续。"""
    rp = D.pipeline_route_plan(_screen_out(172, 100, 0, 72, 0, ready=["PO-%d" % i for i in range(100)],
                                          rid="order_shipped"))
    assert rp["verdict"] == "narrow", rp
    assert len(rp["orders"]) == 100 and "缩到 100 单" in rp["reason"]


def test_route_narrow_refused_when_counts_dont_add_up():
    """账目不自洽就不许改批量（15:33 实测 pending=172 + shipped=72 而 want=172）——宁可停下，不许静默少买。"""
    rp = D.pipeline_route_plan(_screen_out(172, 172, 0, 72, 0,
                                           ready=["PO-%d" % i for i in range(172)], rid="order_shipped"))
    assert rp["verdict"] == "continue", rp


def test_route_narrow_refused_when_ready_short():
    """ready 条数与 pending 对不上（分诊读得不完整）→ 不改批量。"""
    rp = D.pipeline_route_plan(_screen_out(10, 4, 0, 6, 0, ready=["PO-1", "PO-2"], rid="order_shipped"))
    assert rp["verdict"] == "continue", rp


def test_route_continue_when_all_buyable():
    """全在待发货（正常链）→ 原样继续，不动批量。"""
    rp = D.pipeline_route_plan(_screen_out(5, 5, 0, 0, 0, ready=["PO-%d" % i for i in range(5)]))
    assert rp["verdict"] == "continue" and rp["orders"] == []


def test_route_plan_ignores_everything_but_screen():
    """只有 screen 的输出（signals + route）才参与改道；别的步骤输出一律「照原样继续」。"""
    for out in (None, {}, {"fill": {}}, {"signals": {}}, {"route": {}}, "text"):
        assert D.pipeline_route_plan(out)["verdict"] == "continue"


def test_run_steps_noop_stops_early_and_flags_not_failed():
    """run_steps：路线说没得买 → 立刻收工，后面的 fill/place_only **不得**再跑，并标 noop。"""
    calls = []

    def fake(name, params):
        calls.append(name)
        return _screen_out(28, 0, 28, 0, 0, rid="nothing_to_buy") if name == "screen" else {}

    steps = [{"name": "screen", "params": {}}, {"name": "fill", "params": {}},
             {"name": "place_only", "params": {}}]
    out = D.run_steps(steps, {}, fake, log=lambda *_a: None)
    assert calls == ["screen"], "没得买就必须停下，不许再往下填单/下单"
    assert out["noop"] is True and out["stopped"] is True and out["route"] == "nothing_to_buy"


def test_run_steps_route_stop_raises_with_step_number():
    """路线说停（一个都没查到）→ 抛出物必须点名「按分诊路线停下（步骤 k/N <name>）」，后面的步骤不跑。"""
    calls = []

    def fake(name, params):
        calls.append(name)
        return _screen_out(234, 0, 0, 0, 234, rid="no_orders") if name == "screen" else {}

    steps = [{"name": "screen", "params": {}}, {"name": "fill", "params": {}}]
    with pytest.raises(RuntimeError) as ei:
        D.run_steps(steps, {}, fake, log=lambda *_a: None)
    assert "按分诊路线停下（步骤 1/2 screen）" in str(ei.value)
    assert calls == ["screen"]


def test_run_steps_narrow_applies_to_later_steps_without_touching_base():
    """剔除后的批量要给后面每一步用，但**不许改调用方传进来的 params**（既有契约）。"""
    seen = []

    def fake(name, params):
        seen.append((name, list(params.get("orders") or [])))
        if name == "screen":
            return _screen_out(3, 2, 0, 1, 0, ready=["A", "C"], rid="order_shipped")
        return {}

    base = {"orders": ["A", "B", "C"]}
    D.run_steps([{"name": "screen", "params": {}}, {"name": "fill", "params": {}}],
                base, fake, log=lambda *_a: None)
    assert seen[1] == ("fill", ["A", "C"]), seen
    assert base["orders"] == ["A", "B", "C"], "调用方的 params 必须原样不动"


def test_run_steps_lets_a_step_rewrite_later_params():
    """`out["_params"]` 回写后续步骤的基础参数（2026-09-28：auth 关窗重开换端口）。

    来历：BEL 那次 `--action auth` 关窗重开后端口 29738→13718，而 pipeline 后面的步骤
    仍在连老端口（登录后清障那步就 `WinError 10061 拒绝连接`）。
    """
    seen = []

    def fake(name, params):
        seen.append((name, params.get("port")))
        if name == "auth":
            return {"ok": True, "_params": {"port": 13718}}
        return {}

    base = {"port": 29738, "store": "BEL"}
    D.run_steps([{"name": "plan", "params": {}}, {"name": "auth", "params": {}},
                 {"name": "screen", "params": {}}], base, fake, log=lambda *_a: None)
    assert seen == [("plan", 29738), ("auth", 29738), ("screen", 13718)], seen
    assert base["port"] == 29738, "调用方的 params 仍不许被改"
