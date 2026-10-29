#!/usr/bin/env python3
"""钉住「外层被杀 ≠ 业务失败」这条判据。

背景（2026-10-29，用户两次报「明明执行成功了，但是通知失败」）
------------------------------------------------------------
`manage.py apply` 把 `timeout_minutes` 写成 Windows 的 `ExecutionTimeLimit`。
到点后 Windows 杀掉**整棵进程树**并把 `LastTaskResult` 记成 `267014`
（0x41306 TERMINATED）—— 可业务脚本完全可能早就 rc=0 跑完了：

  * `TEMU-BEL-Abnormal-Daily` 08:30 那轮把 82 单**全部转出**，页面清零、
    最后一轮 rc=0，09:05 收工；但 08:40 的强杀先落了账 ⇒ 飞书/面板/health
    三处全报「运行失败」。
  * `TEMU限流监控` 07:30 那轮同一形状（重试共 25 分钟，上限 10 分钟）。

所以判定 `267014` 之前，必须回读**业务自己**留下的退出码。三级证据链：

  1. `_logs/dsh-sentinel/<task>/run.json`      —— dsh_task.py 的判定
  2. `_logs/dsh-sentinel/<task>/attempts.jsonl` —— dsh_run.py 每跑一次 fsync 一行
  3. 任务日志里的 `[END] ... exit=N`            —— 脚本自己写的最后一句

为什么需要第 3 级：Windows 杀的是 `wscript → cmd → dsh_task` 这条链，而 agent
与 `dsh_run.py` 常常作为**孤儿活下来把活干完**。那种情况下 run.json 根本没机会
写，attempts 也可能随下一轮清空，只剩日志里那行 `exit=0`。

跑法：
    pytest scripts/tests/test_terminated_rc.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROJ = Path(r"D:\AI-workplace\TEMU")
sys.path.insert(0, str(PROJ / "scripts"))
sys.path.insert(0, str(Path.home() / ".agents" / "skills" / "scheduled-tasks" / "scripts"))

import log_cause  # noqa: E402
import manage  # noqa: E402


def _make_task(tmp_path, monkeypatch, *, run_json=None, attempts=None, log=None):
    """在 tmp 下造一个「项目 + 日志目录 + 哨兵目录」的迷你布局。"""
    proj = tmp_path / "PROJ"
    logdir = proj / "_logs" / "slug"
    logdir.mkdir(parents=True)
    sent_root = proj / "_logs" / "dsh-sentinel"
    sent = sent_root / "T-1"
    sent.mkdir(parents=True)

    if run_json is not None:
        (sent / "run.json").write_text(json.dumps(run_json), encoding="utf-8")
    if attempts is not None:
        (sent / "attempts.jsonl").write_text(
            "\n".join(json.dumps(a) for a in attempts) + "\n", encoding="utf-8")
    if log is not None:
        (logdir / "2026-10-29.log").write_text(log, encoding="utf-8")

    entry = {"name": "T-1", "log": str(logdir) + os.sep}
    monkeypatch.setattr(log_cause, "registry_log_path", lambda _n: str(logdir) + os.sep)
    monkeypatch.setattr(log_cause, "SENTINEL_ROOT", sent_root)
    monkeypatch.setattr(manage, "REGISTRY", tmp_path / "tasks.json")
    return entry


class TestManageRealRc:
    """manage.real_rc —— health / list 的口径。"""

    def test_non_terminated_codes_pass_through(self, tmp_path, monkeypatch):
        entry = _make_task(tmp_path, monkeypatch, run_json={"final_rc": 0})
        for rc in (0, 1, 2, 42, 5, 6):
            assert manage.real_rc(rc, entry) == rc, "非终止码不该被回读改写"

    def test_run_json_wins(self, tmp_path, monkeypatch):
        entry = _make_task(tmp_path, monkeypatch, run_json={"final_rc": 0},
                           attempts=[{"rc": 42}])
        assert manage.real_rc(267014, entry) == 0

    def test_attempts_used_when_run_json_missing(self, tmp_path, monkeypatch):
        """Windows 杀了 dsh_task，run.json 没写，但 dsh_run.py 已落盘。"""
        entry = _make_task(tmp_path, monkeypatch, attempts=[{"rc": 42}, {"rc": 0}])
        assert manage.real_rc(267014, entry) == 0

    def test_log_end_marker_is_the_last_resort(self, tmp_path, monkeypatch):
        entry = _make_task(
            tmp_path, monkeypatch,
            log="[START] 2026-10-29  8:30:06.40 x\n"
                "[END] 2026-10-29  9:05:07.32 exit=0 \n")
        assert manage.real_rc(267014, entry) == 0

    def test_incomplete_last_run_does_not_borrow_the_previous_exit(
            self, tmp_path, monkeypatch):
        """★ 最危险的情形：本次被杀在写 [END] 之前，日志最后那条是**上一轮**的。

        直接取 `hits[-1]` 就会把「这次死了」洗成「这次成功」。
        判据：最后一个 `[START]` 之后必须出现 `[END]`。
        """
        entry = _make_task(
            tmp_path, monkeypatch,
            log="[START] 8:30\n[END] 9:05 exit=0 \n[START] 12:30\n(还在跑就被杀了)")
        assert manage.real_rc(267014, entry) == 267014

    def test_stale_sentinel_is_ignored(self, tmp_path, monkeypatch):
        """陈旧的 run.json 不能把今天这次失败洗白。"""
        entry = _make_task(tmp_path, monkeypatch, run_json={"final_rc": 0})
        old = 13 * 3600
        for p in tmp_path.rglob("*"):
            if p.is_file():
                os.utime(p, (p.stat().st_atime - old, p.stat().st_mtime - old))
        assert manage.real_rc(267014, entry) == 267014

    def test_missing_everything_keeps_the_original_code(self, tmp_path, monkeypatch):
        entry = _make_task(tmp_path, monkeypatch)
        assert manage.real_rc(267014, entry) == 267014

    def test_no_entry_is_harmless(self):
        assert manage.real_rc(267014, None) == 267014

    def test_broken_run_json_falls_through(self, tmp_path, monkeypatch):
        entry = _make_task(tmp_path, monkeypatch, attempts=[{"rc": 0}])
        (Path(entry["log"]).parent.parent / "_logs" / "dsh-sentinel" / "T-1" /
         "run.json").write_text("{oops", encoding="utf-8")
        assert manage.real_rc(267014, entry) == 0


class TestLogCause:
    """log_cause.agent_final_rc / last_exit_code —— 巡检与面板的口径。"""

    def test_reads_run_json(self, tmp_path, monkeypatch):
        _make_task(tmp_path, monkeypatch, run_json={"final_rc": 0})
        assert log_cause.agent_final_rc("T-1") == 0

    def test_falls_back_to_attempts(self, tmp_path, monkeypatch):
        _make_task(tmp_path, monkeypatch, attempts=[{"rc": 42}, {"rc": 0}])
        assert log_cause.agent_final_rc("T-1") == 0

    def test_falls_back_to_the_log(self, tmp_path, monkeypatch):
        _make_task(tmp_path, monkeypatch,
                   log="[START] 8:30\n[END] 9:05 exit=0 \n")
        assert log_cause.agent_final_rc("T-1") == 0

    def test_failure_is_reported_as_failure(self, tmp_path, monkeypatch):
        _make_task(tmp_path, monkeypatch, attempts=[{"rc": 42}])
        assert log_cause.agent_final_rc("T-1") == 42

    def test_nothing_found_returns_none(self, tmp_path, monkeypatch):
        _make_task(tmp_path, monkeypatch)
        assert log_cause.agent_final_rc("T-1") is None

    def test_last_exit_code_rejects_an_unfinished_run(self, tmp_path, monkeypatch):
        _make_task(tmp_path, monkeypatch,
                   log="[START] 8:30\n[END] 9:05 exit=0 \n[START] 12:30\nkilled")
        assert log_cause.last_exit_code("T-1") is None

    def test_watch_exit_variant_is_recognised(self, tmp_path, monkeypatch):
        """`TEMU限流监控` 写的是 `watch_exit=1`，不是 `exit=1`。"""
        _make_task(tmp_path, monkeypatch,
                   log="[START] 7:30\n[END]  7:48:27.99 watch_exit=1 \n")
        assert log_cause.last_exit_code("T-1") == 1


class TestRegistryContract:
    """注册表里这两个任务的外层上限必须够长，且 agent 预算必须更短。"""

    def test_both_tasks_have_a_workable_limit(self):
        reg = json.loads(manage.REGISTRY.read_text(encoding="utf-8-sig"))
        by = {e["name"]: e for e in reg["tasks"]}
        for name, need in (("TEMU-BEL-Abnormal-Daily", 30), ("TEMU限流监控", 20)):
            e = by[name]
            got = int(e.get("timeout_minutes") or 0)
            assert got >= need, (
                f"{name} 外层上限 {got} 分钟 < 实测恢复流程所需的 {need} 分钟；"
                "Windows 会在恢复跑完之前强杀，把成功记成 267014"
            )

    def test_agent_budget_stays_under_the_windows_limit(self):
        sys.path.insert(0, str(PROJ / "scripts"))
        import dsh_task as dt
        reg = json.loads(manage.REGISTRY.read_text(encoding="utf-8-sig"))
        for e in reg["tasks"]:
            if not (e.get("dsh") or {}).get("enabled"):
                continue
            outer = int(e.get("timeout_minutes") or 30)
            budget = dt.agent_budget_seconds(outer)
            assert budget < outer * 60, (
                f"{e['name']} 的 agent 预算 {budget}s 不小于外层 {outer} 分钟，"
                "agent 永远没机会写 verdict 就被杀"
            )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

