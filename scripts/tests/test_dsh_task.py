#!/usr/bin/env python3
"""Pin the trust rules of scripts/dsh_task.py.

The whole point of the orchestrator is that it does NOT believe the agent.
`dsh --profile headless` always exits 0, so every one of these cases would
otherwise be reported to health/watch/panel as "success".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dsh_task as dt  # noqa: E402


class DecideTest(unittest.TestCase):
    def test_clean_success(self):
        code, why = dt.decide([{"rc": 0}], {"rc": 0, "resolved": False})
        self.assertEqual(code, 0)
        self.assertIn("成功", why)

    def test_failure_passes_through(self):
        code, why = dt.decide([{"rc": 1}], {"rc": 1, "diagnosis": "导出超时"})
        self.assertEqual(code, 1)
        self.assertEqual(why, "导出超时")

    def test_skip_code_5_passes_through(self):
        """5/6 are by-design skips and must survive as-is."""
        code, _ = dt.decide([{"rc": 5}], {"rc": 5})
        self.assertEqual(code, 5)
        code, _ = dt.decide([{"rc": 6}], {"rc": 6})
        self.assertEqual(code, 6)

    def test_no_verdict_is_failure(self):
        code, why = dt.decide([{"rc": 0}], None)
        self.assertEqual(code, dt.NO_VERDICT)
        self.assertIn("没写 verdict", why)

    def test_no_verdict_and_no_attempt(self):
        code, why = dt.decide([], None)
        self.assertEqual(code, dt.NO_ATTEMPT)
        self.assertIn("没执行", why)

    def test_verdict_without_attempt_is_failure(self):
        """A verdict claiming success with no ground-truth record is a lie."""
        code, why = dt.decide([], {"rc": 0})
        self.assertEqual(code, dt.NO_ATTEMPT)
        self.assertIn("没有真实执行记录", why)

    def test_non_integer_rc_is_failure(self):
        code, _ = dt.decide([{"rc": 0}], {"rc": "zero"})
        self.assertEqual(code, dt.NO_VERDICT)
        code, _ = dt.decide([{"rc": 0}], {"rc": True})
        self.assertEqual(code, dt.NO_VERDICT)
        code, _ = dt.decide([{"rc": 0}], {})
        self.assertEqual(code, dt.NO_VERDICT)

    def test_claimed_success_over_failed_attempt_is_rejected(self):
        """THE critical rule: model says 0, the wrapper recorded 1."""
        code, why = dt.decide([{"rc": 1}], {"rc": 0, "resolved": False})
        self.assertEqual(code, dt.UNRESOLVED)
        self.assertIn("最后一次真实退出码是 1", why)

    def test_claimed_success_is_rejected_even_with_resolution_when_unrepaired(self):
        """`resolved: true` + a nice sentence does NOT substitute for a real retry.

        A genuine repair appends a new attempt via dsh_run.py. If the last record
        is still a failure, the claim is unverifiable and must not be trusted.
        """
        code, _ = dt.decide(
            [{"rc": 1}],
            {"rc": 0, "resolved": True, "resolution": "重新点了一次授权页的确认按钮"},
        )
        self.assertEqual(code, dt.UNRESOLVED)

    def test_claimed_success_after_real_repair_is_accepted(self):
        code, why = dt.decide(
            [{"rc": 1}, {"rc": 0}],
            {"rc": 0, "resolved": True, "resolution": "重新点了一次授权页的确认按钮"},
        )
        self.assertEqual(code, 0)
        self.assertIn("重跑成功", why)
        self.assertIn("授权页", why)

    def test_success_after_retry_without_note_still_counts(self):
        code, why = dt.decide([{"rc": 1}, {"rc": 0}], {"rc": 0})
        self.assertEqual(code, 0)
        self.assertIn("未说明如何解决", why)

    def test_last_attempt_failure_beats_earlier_success(self):
        """Only the LAST record matters; an earlier 0 cannot rescue a later 1."""
        code, _ = dt.decide(
            [{"rc": 0}, {"rc": 1}],
            {"rc": 0, "resolved": True, "resolution": "重试后页面恢复"},
        )
        self.assertEqual(code, dt.UNRESOLVED)

    def test_rc_string_is_coerced(self):
        code, _ = dt.decide([{"rc": 3}], {"rc": "3"})
        self.assertEqual(code, 3)

    def test_missing_last_rc_is_not_treated_as_success(self):
        """An unreadable attempt line must not be laundered into rc=0."""
        code, _ = dt.decide([{"rc": None}], {"rc": 0, "resolved": True, "resolution": "x"})
        self.assertEqual(code, dt.UNRESOLVED)

    def test_negative_rc_passes_through(self):
        code, _ = dt.decide([{"rc": -1073741510}], {"rc": -1073741510})
        self.assertEqual(code, -1073741510)


class SentinelIoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsh-sentinel-test-")
        self._orig = dt.SENTINEL_ROOT
        dt.SENTINEL_ROOT = self.tmp

    def tearDown(self):
        dt.SENTINEL_ROOT = self._orig

    def test_roundtrip_attempts(self):
        d = dt.reset_sentinel("T-1")
        with open(os.path.join(d, "attempts.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"rc": 0}) + "\n")
            fh.write(json.dumps({"rc": 2}) + "\n")
        self.assertEqual([a["rc"] for a in dt.read_attempts(d)], [0, 2])

    def test_garbage_attempt_line_does_not_crash(self):
        d = dt.reset_sentinel("T-2")
        with open(os.path.join(d, "attempts.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("not json\n")
            fh.write(json.dumps({"rc": 1}) + "\n")
        rows = dt.read_attempts(d)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["rc"])

    def test_reset_clears_previous_run(self):
        d = dt.reset_sentinel("T-3")
        with open(os.path.join(d, "verdict.json"), "w", encoding="utf-8") as fh:
            json.dump({"rc": 0}, fh)
        d2 = dt.reset_sentinel("T-3")
        self.assertEqual(d, d2)
        self.assertIsNone(dt.read_verdict(d2))

    def test_verdict_array_is_rejected(self):
        d = dt.reset_sentinel("T-4")
        with open(os.path.join(d, "verdict.json"), "w", encoding="utf-8") as fh:
            json.dump([{"rc": 0}], fh)
        self.assertIsNone(dt.read_verdict(d))

    def test_broken_verdict_is_none(self):
        d = dt.reset_sentinel("T-5")
        with open(os.path.join(d, "verdict.json"), "w", encoding="utf-8") as fh:
            fh.write("{oops")
        self.assertIsNone(dt.read_verdict(d))

    def test_verdict_with_utf8_bom_is_read(self):
        """2026-10-28 事故：agent 用 PowerShell 写 verdict，默认带 BOM。

        严格 `utf-8` 读 BOM 会抛 `Unexpected UTF-8 BOM` ⇒ 返回 None ⇒
        判成「agent 没写 verdict」⇒ 面板报 rc=70「失败」。
        实测 TEMU-Today-Sales 18:45 那轮就是这样**假失败**的（实际 rc=0）。
        ⇒ 必须用 `utf-8-sig`：对无 BOM 的文件行为完全一样。
        """
        d = dt.reset_sentinel("T-BOM")
        path = os.path.join(d, "verdict.json")
        payload = {"rc": 0, "resolved": False, "resolution": "", "diagnosis": "带 BOM 写的"}
        # 手工写 BOM（utf-8-sig 会加 EF BB BF）
        with open(path, "w", encoding="utf-8-sig") as fh:
            json.dump(payload, fh, ensure_ascii=False)

        self.assertEqual(open(path, "rb").read(3), b"\xef\xbb\xbf", "前提：文件确实带 BOM")
        self.assertEqual(dt.read_verdict(d), payload)

    def test_bom_verdict_yields_the_real_rc_not_70(self):
        """带 BOM 的 verdict 必须按其 rc 判定，而不是 70。"""
        d = dt.reset_sentinel("T-BOM2")
        with open(os.path.join(d, "verdict.json"), "w", encoding="utf-8-sig") as fh:
            json.dump({"rc": 0, "resolved": False, "diagnosis": "ok"}, fh)
        with open(os.path.join(d, "attempts.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"rc": 0}) + "\n")

        v = dt.read_verdict(d)
        self.assertIsNotNone(v)
        code, _why = dt.decide(dt.read_attempts(d), v)
        self.assertEqual(code, 0)

    def test_verdict_problem_names_the_bom(self):
        """读不出来时要能说出真因（BOM / 非法 JSON），不能只会说"没写"。"""
        d = dt.reset_sentinel("T-BOM3")
        self.assertEqual(dt.verdict_problem(d), "", "文件不存在时不该报问题")

        with open(os.path.join(d, "verdict.json"), "w", encoding="utf-8-sig") as fh:
            json.dump({"rc": 0}, fh)
        # BOM 已被 utf-8-sig 兼容，此时不报问题
        self.assertEqual(dt.verdict_problem(d), "")

        with open(os.path.join(d, "verdict.json"), "w", encoding="utf-8") as fh:
            fh.write("{oops")
        self.assertIn("不是合法 JSON", dt.verdict_problem(d))

    def test_decide_carries_the_verdict_issue_into_the_reason(self):
        """判定理由要带上真因，否则面板只能说"没写 verdict"，把 BOM 埋掉。"""
        d = dt.reset_sentinel("T-BOM4")
        with open(os.path.join(d, "verdict.json"), "w", encoding="utf-8") as fh:
            fh.write("{oops")
        with open(os.path.join(d, "attempts.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"rc": 0}) + "\n")

        v = dt.read_verdict(d)
        issue = dt.verdict_problem(d)
        self.assertIsNone(v)
        self.assertIn("不是合法 JSON", issue)

        code, why = dt.decide(dt.read_attempts(d), v, issue)
        self.assertEqual(code, dt.NO_VERDICT)
        self.assertIn("不是合法 JSON", why)

    def test_task_name_is_sanitised(self):
        d = dt.sentinel_dir("TEMU/Bad Name:1")
        self.assertNotIn("/", os.path.basename(d))
        self.assertNotIn(":", os.path.basename(d))


class CredentialTest(unittest.TestCase):
    def test_reads_key_from_credentials_yaml(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, ".credentials.yaml"), "w", encoding="utf-8") as fh:
            fh.write("version: 1\nrefs:\n  DEEPSEEK_API_KEY: sk-abc123\n")
        with mock.patch.dict(os.environ, {"DSH_HOME": tmp}):
            self.assertEqual(dt.read_credential_key(), "sk-abc123")

    def test_missing_file_returns_empty(self):
        with mock.patch.dict(os.environ, {"DSH_HOME": tempfile.mkdtemp()}):
            self.assertEqual(dt.read_credential_key(), "")


class TimeoutJudgementTest(unittest.TestCase):
    """超时被杀 ≠ 失败：业务跑完了就要按业务的退出码算。

    2026-10-29 `TEMU-BEL-Abnormal-Daily`：82 单全部转出、`04_verify.json` 显示
    页面清零，最后一轮 rc=0；但 agent 被 Windows 在 08:40 强杀，verdict 没写成，
    于是整轮被记成「被终止」。用户看到的就是"明明执行成功了，通知却说失败"。

    ⇒ 超时路径必须看 attempts.jsonl，而不是一律 124。
    """

    def test_finished_business_survives_the_kill(self):
        code, why = dt.decide_timeout([{"rc": 0}], None)
        self.assertEqual(code, 0)
        self.assertIn("已跑完", why)

    def test_failed_business_stays_failed(self):
        code, why = dt.decide_timeout([{"rc": 42}], None)
        self.assertEqual(code, 42)
        self.assertIn("42", why)

    def test_later_success_after_earlier_failure_wins(self):
        code, _ = dt.decide_timeout([{"rc": 42}, {"rc": 42}, {"rc": 0}], None)
        self.assertEqual(code, 0)

    def test_last_attempt_still_governs(self):
        code, _ = dt.decide_timeout([{"rc": 0}, {"rc": 42}], None)
        self.assertEqual(code, 42)

    def test_unreadable_rc_is_not_laundered_into_success(self):
        code, _ = dt.decide_timeout([{"rc": None}], None)
        self.assertEqual(code, dt.NO_VERDICT)

    def test_a_real_verdict_still_gets_the_paranoid_rules(self):
        """verdict 存在时不能走捷径：它仍要过 decide() 那套。"""
        code, _ = dt.decide_timeout([{"rc": 1}], {"rc": 0, "resolved": False})
        self.assertEqual(code, dt.UNRESOLVED)


class AgentBudgetTest(unittest.TestCase):
    """The agent's budget must ALWAYS be smaller than the Windows limit.

    2026-10-29 两次生产假告警：`manage.py apply` 把 `timeout_minutes` 写成
    `ExecutionTimeLimit`，而旧公式给 agent `1.5×timeout + 5` —— 永远比 Windows
    允许的大。于是 Windows 在 turn 中途把整棵树杀掉，dsh_task 还没来得及读哨兵，
    Task Scheduler 记成 `267014`（被终止）：

      * `TEMU-BEL-Abnormal-Daily` 08:30：业务 09:05 已经 rc=0 全转完，但 08:40 的
        强杀早已落账 ⇒ 看门狗报「运行失败」。
      * `TEMU限流监控` 07:30：同一形状。

    ⇒ agent 预算必须 = 外层 - 1 分钟，留出收尾并回传真实退出码的余量。
    """

    def test_budget_is_smaller_than_the_windows_limit(self):
        for outer in (1, 5, 10, 20, 30, 45, 60, 240):
            budget = dt.agent_budget_seconds(outer)
            self.assertLess(
                budget, outer * 60,
                f"外层 {outer} 分钟时 agent 预算 {budget}s 不小于外层，必被强杀",
            )

    def test_budget_leaves_a_minute_of_headroom(self):
        self.assertEqual(dt.agent_budget_seconds(10), 9 * 60)
        self.assertEqual(dt.agent_budget_seconds(45), 44 * 60)

    def test_tiny_limit_still_gets_a_positive_budget(self):
        """1 分钟的外层上限不该算出 0 或负数（那会让 agent 完全没机会跑）。"""
        self.assertGreater(dt.agent_budget_seconds(1), 0)

    def test_the_old_1_5x_formula_is_gone(self):
        """钉住实现：不能有人把 1.5x 改回来（它比外层大，等于没有上限）。"""
        src = open(dt.__file__, encoding="utf-8").read()
        self.assertNotIn("int(base * 1.5) + 5", src)
        self.assertIn("agent_budget_seconds", src)


class AgentKillTest(unittest.TestCase):
    """超时必须**连孙进程一起杀**，否则 communicate() 会一直挂着。

    实测 2026-10-29：1200s 的预算拖到 2129s 才返回 —— 因为
    `subprocess.run(timeout=)` 只杀直接子进程 `cmd.exe`，孙进程 `dsh` 还活着，
    管道没关，`communicate()` 就永远等下去。
    """

    def test_timeout_kills_the_tree_and_returns_124(self):
        proc = mock.MagicMock()
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="x", timeout=5),
            (b"partial", b"oops"),
        ]
        proc.pid = 4242
        with mock.patch.object(dt.subprocess, "Popen", return_value=proc), \
             mock.patch.object(dt.subprocess, "run") as run:
            rc, tail = dt.run_agent("p", 5, {})
        self.assertEqual(rc, dt.TIMEOUT)
        self.assertIn("timeout", tail)
        argv = run.call_args[0][0]
        self.assertEqual(argv[:2], ["taskkill", "/F"])
        self.assertIn("4242", argv)
        proc.kill.assert_called_once()

    def test_a_stubborn_pipe_does_not_hang_forever(self):
        """连第二次 communicate 都超时，也必须返回而不是挂住。"""
        proc = mock.MagicMock()
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="x", timeout=5),
            subprocess.TimeoutExpired(cmd="x", timeout=20),
        ]
        proc.pid = 7
        with mock.patch.object(dt.subprocess, "Popen", return_value=proc), \
             mock.patch.object(dt.subprocess, "run"):
            rc, _ = dt.run_agent("p", 5, {})
        self.assertEqual(rc, dt.TIMEOUT)

    def test_normal_run_passes_the_real_rc_through(self):
        proc = mock.MagicMock()
        proc.communicate.return_value = (b'{"type":"final"}', b"")
        proc.returncode = 0
        with mock.patch.object(dt.subprocess, "Popen", return_value=proc):
            rc, _tail = dt.run_agent("p", 5, {})
        self.assertEqual(rc, 0)

    def test_cannot_start_dsh_is_a_failure_not_a_crash(self):
        with mock.patch.object(dt.subprocess, "Popen", side_effect=OSError("nope")):
            rc, tail = dt.run_agent("p", 5, {})
        self.assertNotEqual(rc, 0)
        self.assertIn("cannot start dsh", tail)


class PromptTest(unittest.TestCase):
    def test_prompt_names_the_wrapper_and_the_verdict_path(self):
        p = dt.build_prompt("TASK-A", "scripts\\run_x.cmd", r"C:\sent")
        self.assertIn("dsh_run.py", p)
        self.assertIn('--task "TASK-A"', p)
        self.assertIn("verdict.json", p)
        self.assertIn(r"C:\sent", p)
        self.assertIn("VERDICT rc=", p)

    def test_prompt_warns_about_5_and_6(self):
        p = dt.build_prompt("T", "x.cmd", "s")
        self.assertIn("5 / 6", p)
        self.assertIn("不要重试", p)


class EventSummaryTest(unittest.TestCase):
    def test_extracts_tools_and_final(self):
        raw = "\n".join(
            [
                '{"type":"status","phase":"turn_start"}',
                '{"type":"tool_call","tool":"pwsh","input":{}}',
                '{"type":"tool_call","tool":"read","input":{}}',
                '{"type":"status","phase":"turn_end","reason":{"kind":"completed"}}',
                '{"type":"final","text":"VERDICT rc=0"}',
                "noise line",
            ]
        )
        info = dt.summarize_events(raw)
        self.assertEqual(info["tools"], ["pwsh", "read"])
        self.assertEqual(info["final"], "VERDICT rc=0")
        self.assertEqual(info["turn_reason"], "completed")


class PanelRefreshTest(unittest.TestCase):
    """The panel hook must fire from OUTSIDE the agent's job object.

    DSH wraps spawned commands in a KILL_ON_JOB_CLOSE job, so the detached
    publisher that notify_panel.cmd relies on gets killed on session exit
    (measured 2026-10-26). dsh_task.py therefore fires the hook itself, and
    notify_panel.cmd stays quiet when DSH_AGENT_INNER is set.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dsh-refresh-")
        self._orig_proj = dt.PROJ
        dt.PROJ = self.tmp
        os.makedirs(os.path.join(self.tmp, "scripts"))
        open(os.path.join(self.tmp, "scripts", "panel_refresh.py"), "w").close()

    def tearDown(self):
        dt.PROJ = self._orig_proj

    @staticmethod
    def _entry(flag):
        return {"name": "T", "刷新面板": flag}

    def test_fires_when_flag_true(self):
        with mock.patch.object(dt, "load_entry", return_value=self._entry(True)), \
             mock.patch.object(dt.subprocess, "Popen") as popen:
            dt.refresh_panel_if_needed("T")
        self.assertEqual(popen.call_count, 1)
        argv = popen.call_args[0][0]
        self.assertIn("--request", argv)
        self.assertIn("dsh:T", argv)

    def test_skips_when_flag_false(self):
        """The panel-refresh task itself must never hook itself."""
        with mock.patch.object(dt, "load_entry", return_value=self._entry(False)), \
             mock.patch.object(dt.subprocess, "Popen") as popen:
            dt.refresh_panel_if_needed("T")
        popen.assert_not_called()

    def test_skips_when_flag_missing(self):
        with mock.patch.object(dt, "load_entry", return_value={"name": "T"}), \
             mock.patch.object(dt.subprocess, "Popen") as popen:
            dt.refresh_panel_if_needed("T")
        popen.assert_not_called()

    def test_unknown_task_is_a_noop(self):
        with mock.patch.object(dt, "load_entry", return_value=None), \
             mock.patch.object(dt.subprocess, "Popen") as popen:
            dt.refresh_panel_if_needed("nope")
        popen.assert_not_called()

    def test_spawn_failure_does_not_raise(self):
        with mock.patch.object(dt, "load_entry", return_value=self._entry(True)), \
             mock.patch.object(dt.subprocess, "Popen", side_effect=OSError("nope")):
            dt.refresh_panel_if_needed("T")  # must not raise

    def test_missing_script_is_a_noop(self):
        os.remove(os.path.join(self.tmp, "scripts", "panel_refresh.py"))
        with mock.patch.object(dt, "load_entry", return_value=self._entry(True)), \
             mock.patch.object(dt.subprocess, "Popen") as popen:
            dt.refresh_panel_if_needed("T")
        popen.assert_not_called()


class SandboxModeTest(unittest.TestCase):
    def test_default_is_workspace_write_not_danger(self):
        """danger-full-access was only needed while the ACL grant was broken.

        With TakeOwnership/ChangePermissions on the workspace root the integrity
        label write succeeds (rc 5 -> 0) and workspace-write provisions normally
        - and it keeps the approval policy at `ask` instead of `never`.
        """
        src = open(dt.__file__, encoding="utf-8").read()
        self.assertIn('env.setdefault("DSH_PERMISSION_MODE", "workspace-write")', src)
        self.assertNotIn('env.setdefault("DSH_PERMISSION_MODE", "danger-full-access")', src)

    def test_inner_marker_is_exported(self):
        src = open(dt.__file__, encoding="utf-8").read()
        self.assertIn('env["DSH_AGENT_INNER"] = "1"', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
