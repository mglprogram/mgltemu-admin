#!/usr/bin/env python3
"""Pin log_cause.agent_verdict / cause_for behaviour after the dsh migration.

The rule under test: an agent verdict is only a "cause" when it reports a
failure. A stale or over-optimistic verdict (rc=0) must fall through to the log
scrape instead of masking a real error.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import log_cause  # noqa: E402


class VerdictTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sentinel-")
        self._orig = log_cause.__dict__.get("_SENTINEL_ROOT_FOR_TEST")
        # Redirect the sentinel root by patching the module's __file__-derived path.
        self.proj = Path(self.tmp) / "proj"
        (self.proj / "scripts").mkdir(parents=True)
        self.sentinel = self.proj / "_logs" / "dsh-sentinel"
        self.sentinel.mkdir(parents=True)
        self._patch = mock.patch.object(
            log_cause, "__file__", str(self.proj / "scripts" / "log_cause.py")
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def _write(self, task, payload, encoding="utf-8"):
        d = self.sentinel / task
        d.mkdir(parents=True, exist_ok=True)
        with io.open(d / "verdict.json", "w", encoding=encoding) as fh:
            json.dump(payload, fh, ensure_ascii=False)

    def test_failure_verdict_is_returned(self):
        self._write("T1", {"rc": 3, "diagnosis": "导出页停在 loading"})
        self.assertEqual(log_cause.cause_for("T1"), "导出页停在 loading")

    def test_success_verdict_falls_through(self):
        self._write("T2", {"rc": 0, "diagnosis": "一切正常"})
        with mock.patch.object(log_cause, "last_cause", return_value="log says boom"):
            self.assertEqual(log_cause.cause_for("T2"), "log says boom")

    def test_success_verdict_without_log_cause_returns_empty(self):
        self._write("T3", {"rc": 0, "diagnosis": "一切正常"})
        with mock.patch.object(log_cause, "last_cause", return_value=""):
            self.assertEqual(log_cause.cause_for("T3"), "")

    def test_bom_encoded_verdict_is_readable(self):
        """An agent writing from PowerShell emits a BOM; it must still parse."""
        self._write("T4", {"rc": 5, "diagnosis": "窗口锁被占"}, encoding="utf-8-sig")
        self.assertEqual(log_cause.cause_for("T4"), "窗口锁被占")

    def test_stale_verdict_is_ignored(self):
        self._write("T5", {"rc": 9, "diagnosis": "两天前的旧结论"})
        old = time.time() - 48 * 3600
        os.utime(self.sentinel / "T5" / "verdict.json", (old, old))
        with mock.patch.object(log_cause, "last_cause", return_value="fresh log"):
            self.assertEqual(log_cause.cause_for("T5"), "fresh log")

    def test_missing_verdict_falls_back(self):
        with mock.patch.object(log_cause, "last_cause", return_value="from log"):
            self.assertEqual(log_cause.cause_for("nope"), "from log")

    def test_broken_verdict_falls_back(self):
        d = self.sentinel / "T6"
        d.mkdir(parents=True)
        (d / "verdict.json").write_text("{not json", encoding="utf-8")
        with mock.patch.object(log_cause, "last_cause", return_value="from log"):
            self.assertEqual(log_cause.cause_for("T6"), "from log")

    def test_array_verdict_is_ignored(self):
        self._write("T7", [{"rc": 1, "diagnosis": "x"}])
        with mock.patch.object(log_cause, "last_cause", return_value="from log"):
            self.assertEqual(log_cause.cause_for("T7"), "from log")

    def test_never_raises(self):
        with mock.patch.object(log_cause, "last_cause", side_effect=RuntimeError("boom")):
            self.assertEqual(log_cause.cause_for("T8"), "")

    def test_task_name_is_sanitised_like_the_writer(self):
        """dsh_task.py sanitises the folder name; readers must agree."""
        self._write("A_B_C", {"rc": 1, "diagnosis": "sanitised ok"})
        self.assertEqual(log_cause.cause_for("A/B/C"), "sanitised ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
