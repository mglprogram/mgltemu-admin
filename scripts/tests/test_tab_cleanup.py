#!/usr/bin/env python3
"""Pin the "close the tab you opened" contract (2026-10-27 user requirement:
每执行一次定时任务，打开的新页面任务完成自己关闭).

Why this needs tests rather than eyeballs: every leak is INVISIBLE. The run
reports rc=0, the panel is green, and the only symptom is that the 紫鸟 window
has 40 tabs a week later. Three separate failure modes were found on 2026-10-27:

  1. `CdpBrowser.close()` was a no-op (`return None`), and `new_tab_background`
     threw away the `createTarget` targetId -> nothing could ever be closed.
  2. The close call sat at the END of a script's success path, so any exception
     mid-way left the tab behind forever.
  3. Reused pages (the user's own tabs) must NOT be closed.
"""

from __future__ import annotations

import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "power", "lib"))
sys.path.insert(0, os.path.join(ROOT, "bel_goods"))


def read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


class CloseTargetTest(unittest.TestCase):
    """`close_target` must be idempotent and must never raise."""

    def setUp(self):
        import cdp_transport
        self.t = cdp_transport

    def test_empty_id_is_true(self):
        self.assertTrue(self.t.close_target(9999, ""))
        self.assertTrue(self.t.close_target(9999, None))

    def test_dead_port_counts_as_closed(self):
        """Port gone == window gone == tab gone. Must not raise."""
        self.assertTrue(self.t.close_target(1, "someid"))

    def test_close_target_exists_and_is_callable(self):
        self.assertTrue(callable(getattr(self.t, "close_target", None)))

    def test_target_id_key_aliases_are_both_handled(self):
        """`/json/list` uses `id`; some payloads use `targetId`. Both must match."""
        src = read("power/lib/cdp_transport.py")
        self.assertIn('t.get("targetId") or t.get("id")', src)


class DriverRecordsWhatItOpensTest(unittest.TestCase):
    """A driver may only close targets it created itself."""

    def _assert_records(self, path, create_call):
        src = read(path)
        self.assertIn("self._opened_targets = []", src,
                      "%s must initialise _opened_targets" % path)
        self.assertIn("_opened_targets.append", src,
                      "%s must record created targets" % path)
        self.assertIn("close_target", src,
                      "%s must close them via close_target" % path)
        self.assertIn("for tid in list(getattr(self, \"_opened_targets\", []))",
                      src, "%s close() must iterate recorded ids" % path)

    def test_buylabel_driver_records(self):
        self._assert_records("power/lib/temu_buylabel_driver.py", "Target.createTarget")

    def test_abnormal_driver_records(self):
        self._assert_records("power/lib/temu_abnormal_driver.py", "create_target")

    def test_buylabel_close_is_not_a_noop(self):
        src = read("power/lib/temu_buylabel_driver.py")
        # the old body was essentially just browser.close(); make sure the new
        # one actually walks the recorded ids
        self.assertIn("关标签页失败（不阻断）", src)

    def test_abnormal_close_is_not_a_noop(self):
        src = read("power/lib/temu_abnormal_driver.py")
        self.assertNotIn("    def close(self):\n        return None", src,
                         "TemuAbnormalDriver.close must not be a no-op any more")


class OneShotVisitorsCloseInFinallyTest(unittest.TestCase):
    """Scripts that open a scratch tab must close it on EVERY path."""

    def _assert_finally(self, path):
        tree = ast.parse(read(path))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                if node.finalbody and any(
                    isinstance(n, ast.Call) and "close" in ast.dump(n.func)
                    for n in ast.walk(ast.Module(body=node.finalbody, type_ignores=[]))
                ):
                    found = True
        self.assertTrue(found, "%s must close the scratch tab in a finally block" % path)

    def test_cdp_visit(self):
        self._assert_finally("bel_goods/cdp_visit.py")

    def test_verify_fluxall(self):
        self._assert_finally("bel_goods/verify_fluxall.py")

    def test_fetch_focus_spus(self):
        self._assert_finally("bel_goods/fetch_focus_spus.py")

    def test_presale_closes_scratch_page(self):
        src = read("power/lib/temu_presale.py")
        self.assertIn("_close_target(port, tid)", src)
        self.assertIn("finally:", src)


class NoOpBrowserCloseTest(unittest.TestCase):
    def test_browser_close_is_intentionally_inert_but_page_close_works(self):
        """`CdpBrowser.close()` is allowed to be a no-op (it only detaches);
        the real cleanup must go through page/target close."""
        src = read("power/lib/cdp_transport.py")
        self.assertIn("Target.closeTarget", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
