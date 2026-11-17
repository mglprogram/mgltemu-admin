# -*- coding: utf-8 -*-
"""test_pw_fail_fix_plan.py — 帕沃尔「失败单自动修」与「帕沃尔页面锁排队」的回归钉。

2026-11-17 BEL 真机（两处硬 BUG 一起暴露）：
  ① `fail_scan` 与 `fail_fix` 在整链里是**两次独立进程**，而方案只存在内存
     `ctx["fail_plan"]` ⇒ 第二段永远读到空、打印「没有可自动修的失败单」，
     整链的自动修失败单**从来没生效过**（历史日志里 fail_fix 恒为 0）。
     修法：内存没有就从磁盘读 `pw_fail_round<N>.json`。
  ② `PW-TEMU` 被 BHY 的 hwc_order 占着（一次 15~25 分钟），BEL 等满 900s 就放弃
     ⇒ 当天 26 单一个没下。修法：排队重试（`PW_LOCK_RETRIES`，默认 3 轮）。
"""
import json
import os
import sys
import types

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_SCRIPTS = os.path.dirname(_POWER)
for p in (_POWER, os.path.join(_POWER, "lib"), _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_pw_fulfill as F  # noqa: E402


# --------------------------------------------------------------- fail_fix 方案
def _fake_fail_fix_module(seen: list) -> types.ModuleType:
    """假 pw_fail_fix：只记录被要求处理哪些单，不碰浏览器。"""
    mod = types.ModuleType("pw_fail_fix")

    def _open_split(pos, tag, log=print):
        seen.append(("open", list(pos)))
        return object()

    def apply_one(pg, item, commit, log=print):
        seen.append(("apply", item["po"]))
        return {"ok": True, "why": ""}

    def place(pg, done, commit, log=print):
        seen.append(("place", list(done)))
        return {"place": "ok"}

    mod._open_split = _open_split
    mod.apply_one = apply_one
    mod.place = place
    return mod


def test_fail_fix_reads_plan_from_disk(tmp_path, monkeypatch):
    """fail_fix 单独进程跑时，方案要能从 `pw_fail_round1.json` 读回来并真去修。"""
    out = tmp_path
    plan = [{"po": "PO-1", "plan": "CROSS_WH", "to_wh": "kansas仓",
             "fnsku": "FNSKU1", "why": "加州3仓无货"},
            {"po": "PO-2", "plan": "MANUAL", "why": "两个仓都没有"}]
    (out / "pw_fail_round1.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    seen: list = []
    monkeypatch.setitem(sys.modules, "pw_fail_fix", _fake_fail_fix_module(seen))
    ctx = {"out_dir": str(out), "params": {}, "fail_plan": None, "store": "BEL"}

    res = F.step_fail_fix(ctx, log=lambda *a, **k: None)
    assert res["fail_fix"] == 1, res
    assert ("open", ["PO-1"]) in seen, seen        # 只把「可自动修」的那单带进页面
    assert ("apply", "PO-1") in seen
    assert ("place", ["PO-1"]) in seen
    assert ctx["fail_plan"] == plan                # 读回内存，后续步骤复用


def test_fail_fix_without_plan_file_does_nothing(tmp_path, monkeypatch):
    """没有方案文件时保持原行为：不动任何单、返回 0（不报错）。"""
    seen: list = []
    monkeypatch.setitem(sys.modules, "pw_fail_fix", _fake_fail_fix_module(seen))
    ctx = {"out_dir": str(tmp_path), "params": {}, "store": "BEL"}
    res = F.step_fail_fix(ctx, log=lambda *a, **k: None)
    assert res == {"fail_fix": 0}
    assert seen == []


# ------------------------------------------------------------------- 锁排队
class WindowBusy(Exception):
    """名字必须**正好**叫 WindowBusy —— 代码按类型名判（`type(ex).__name__`），
    这样测试不必 import 真正的 window_lock 模块。"""


def test_run_steps_retries_when_lock_busy(monkeypatch):
    """首次拿不到 PW 锁时要排队重试，而不是立刻把整段判 skip。"""
    calls = {"n": 0, "ran": 0}

    class _Ctx:
        def __enter__(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise WindowBusy("紫鸟窗口 PW-TEMU 上已有作业在跑")
            return None

        def __exit__(self, *a):
            return False

    monkeypatch.setenv("PW_LOCK_RETRIES", "2")
    monkeypatch.setattr(F, "_pw_lock", lambda *a, **k: _Ctx())
    monkeypatch.setattr(F.time, "sleep", lambda s: None)

    def _locked(picked, ctx, a, log=print):
        calls["ran"] += 1
        return [{"name": "hwc_order", "status": "ok"}]

    monkeypatch.setattr(F, "_run_steps_locked", _locked)

    picked = [{"name": "hwc_order"}]
    out = F._run_steps(picked, {"store": "BEL"}, types.SimpleNamespace(stage="hwc_order"),
                       log=lambda *a, **k: None)
    assert calls["ran"] == 1, "第二轮应该真的把步骤跑起来"
    assert out[0]["status"] == "ok", out


def test_run_steps_gives_up_after_all_retries(monkeypatch):
    """全部轮次都拿不到锁才判 skip（并写清重试了几轮），不再假装跑过。"""
    class _Busy:
        def __enter__(self):
            raise WindowBusy("一直被占着")

        def __exit__(self, *a):
            return False

    monkeypatch.setenv("PW_LOCK_RETRIES", "2")
    monkeypatch.setattr(F, "_pw_lock", lambda *a, **k: _Busy())
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    monkeypatch.setattr(F, "_run_steps_locked",
                        lambda *a, **k: pytest.fail("不该跑步骤"))

    out = F._run_steps([{"name": "hwc_order"}], {"store": "BEL"},
                       types.SimpleNamespace(stage="hwc_order"),
                       log=lambda *a, **k: None)
    assert out and out[0]["status"] == "skip"
    assert "2" in out[0]["why"], out[0]

