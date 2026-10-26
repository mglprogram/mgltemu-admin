#!/usr/bin/env python3
"""Pin the trust rules of scripts/dsh_task.py.

The whole point of the orchestrator is that it does NOT believe the agent.
`dsh --profile headless` always exits 0, so every one of these cases would
otherwise be reported to health/watch/panel as "success".
"""

from __future__ import annotations

import json
import os
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
