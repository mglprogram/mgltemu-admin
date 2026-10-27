#!/usr/bin/env python3
"""Pin the TEMU auth pipeline contract (2026-10-27).

The bug this all exists for: `_click_auth_confirm` used to return True as soon
as `_js_click()` *called* `el.click()` — it never checked whether the page
moved. BEL 2026-10-27: the log said `[授权] JS 点「确认授权并前往」`, the URL never
changed, and the run then failed with a misleading "授权未完成". A JS click on
that button does nothing; only a real mouse event sequence (with `buttons`)
triggers the redirect.

So the tests below pin two things:
  1. "clicked" != "advanced" — every click method must be verified.
  2. a stage that cannot see the page (port == 0) must NOT report success.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (
    os.path.join(ROOT, "power", "lib"),
    os.path.join(ROOT, "power"),
):
    if p not in sys.path:
        sys.path.insert(0, p)

import auth_temu_driver as atd  # noqa: E402


class FakePage:
    """Minimal stand-in: url can be made to change or stay put."""

    def __init__(self, url="https://seller.kuajingmaihuo.com/settle/seller-login?x=1",
                 pages=3, move_on=()):
        self.port = 9999
        self.url = url
        self._pages = pages
        self.move_on = set(move_on)   # which click methods cause a jump
        self.clicks = []

    def wait_for_timeout(self, ms):
        return None

    def cdp_session(self):
        raise RuntimeError("no real cdp in tests")


class ConfirmAdvancedTest(unittest.TestCase):
    def test_url_change_counts_as_advanced(self):
        pg = FakePage()
        with mock.patch.object(atd, "_pages_via_cdp", return_value=[1, 2, 3]):
            advance = atd._confirm_advanced(pg, pg.url, 3)
        self.assertFalse(advance, "url and page count both unchanged -> no advance")

        pg2 = FakePage(url="https://agentseller-us.temu.com/mmsos/orders.html")
        with mock.patch.object(atd, "_pages_via_cdp", return_value=[1, 2, 3]):
            self.assertTrue(atd._confirm_advanced(pg2, "https://old", 3))

    def test_same_auth_url_is_not_advanced(self):
        """THE BEL case: still on seller-login, nothing moved."""
        pg = FakePage(url="https://seller.kuajingmaihuo.com/settle/seller-login?x=1")
        with mock.patch.object(atd, "_pages_via_cdp", return_value=[1, 2, 3]):
            self.assertFalse(atd._confirm_advanced(pg, pg.url, 3))

    def test_seller_page_counts_as_advanced(self):
        pg = FakePage(url="https://agentseller-us.temu.com/mmsos/orders.html")
        with mock.patch.object(atd, "_pages_via_cdp", return_value=[1, 2, 3]):
            self.assertTrue(atd._confirm_advanced(pg, pg.url, 3))

    def test_page_count_change_counts_as_advanced(self):
        pg = FakePage()
        with mock.patch.object(atd, "_pages_via_cdp", return_value=[1, 2, 3, 4]):
            self.assertTrue(atd._confirm_advanced(pg, pg.url, 3))

    def test_closed_page_counts_as_advanced(self):
        class Boom:
            """A page whose target is gone: every attribute access raises."""

            port = 9999

            def __init__(self):
                self._closed = True

            @property
            def url(self):
                raise RuntimeError("Target closed")

            def wait_for_timeout(self, ms):
                return None

        with mock.patch.object(atd, "_pages_via_cdp", return_value=[1]):
            self.assertTrue(atd._confirm_advanced(Boom(), "https://old", 1))


class TryAllClickMethodsTest(unittest.TestCase):
    def test_js_click_without_movement_reports_failure(self):
        """THE regression: JS click returns True but the page never moves."""
        pg = FakePage()
        loc = mock.Mock()
        with mock.patch.object(atd, "_js_click", return_value=True), \
             mock.patch.object(atd, "_dispatch_real_mouse", return_value=False), \
             mock.patch.object(atd, "_pages_via_cdp", return_value=[1, 2, 3]), \
             mock.patch.object(atd, "_confirm_advanced", return_value=False):
            self.assertFalse(atd._try_all_click_methods(pg, loc, "test"))

    def test_real_mouse_rescues_when_js_fails(self):
        pg = FakePage()
        loc = mock.Mock()
        calls = []

        def _advance(*_a):
            calls.append(1)
            # first call (after JS) -> False; second (after real mouse) -> True
            return len(calls) > 1

        with mock.patch.object(atd, "_js_click", return_value=True), \
             mock.patch.object(atd, "_dispatch_real_mouse", return_value=True), \
             mock.patch.object(atd, "_pages_via_cdp", return_value=[1, 2, 3]), \
             mock.patch.object(atd, "_confirm_advanced", side_effect=_advance):
            self.assertTrue(atd._try_all_click_methods(pg, loc, "test"))

    def test_all_methods_failing_returns_false(self):
        pg = FakePage()
        loc = mock.Mock()
        loc.click.side_effect = RuntimeError("nope")
        with mock.patch.object(atd, "_js_click", return_value=False), \
             mock.patch.object(atd, "_dispatch_real_mouse", return_value=False), \
             mock.patch.object(atd, "_pages_via_cdp", return_value=[1, 2, 3]):
            self.assertFalse(atd._try_all_click_methods(pg, loc, "test"))


class ClickAuthConfirmTest(unittest.TestCase):
    def test_returns_false_when_nothing_advances(self):
        pg = FakePage()
        cand = mock.Mock()
        cand.count.return_value = 1
        cand.first = mock.Mock()
        pg.get_by_text = mock.Mock(return_value=cand)
        with mock.patch.object(atd, "_try_all_click_methods", return_value=False):
            self.assertFalse(atd._click_auth_confirm(pg))

    def test_returns_true_when_a_method_advances(self):
        pg = FakePage()
        cand = mock.Mock()
        cand.count.return_value = 1
        cand.first = mock.Mock()
        pg.get_by_text = mock.Mock(return_value=cand)
        with mock.patch.object(atd, "_try_all_click_methods", return_value=True):
            self.assertTrue(atd._click_auth_confirm(pg))


class PipelineStagePortTest(unittest.TestCase):
    """A stage that can see no page must not claim success."""

    def setUp(self):
        import run_temu_auth_pipeline as pl
        self.pl = pl

    def test_needs_port_set_is_complete(self):
        names = {n for n, _, _ in self.pl.STAGES}
        self.assertTrue(self.pl._NEEDS_PORT <= names)
        self.assertNotIn("port", self.pl._NEEDS_PORT)  # port itself cannot need port

    def test_ensure_port_probes_when_missing(self):
        ctx = {"store": "BEL", "port": 0}
        with mock.patch.object(self.pl, "stage_port", return_value=(True, "端口 1234")) as sp:
            ok, detail = self.pl._ensure_port(ctx)
        self.assertTrue(ok)
        sp.assert_called_once()
        self.assertIn("1234", detail)

    def test_ensure_port_keeps_existing(self):
        ctx = {"store": "BEL", "port": 1234}
        with mock.patch.object(self.pl, "stage_port") as sp:
            ok, _ = self.pl._ensure_port(ctx)
        self.assertTrue(ok)
        sp.assert_not_called()

    def test_ensure_port_fails_loudly(self):
        ctx = {"store": "BEL", "port": 0}
        with mock.patch.object(self.pl, "stage_port", return_value=(False, "窗口没开")):
            ok, detail = self.pl._ensure_port(ctx)
        self.assertFalse(ok)
        self.assertIn("窗口没开", detail)

    def test_stage_order_is_sane(self):
        names = [n for n, _, _ in self.pl.STAGES]
        self.assertEqual(names[0], "port")
        self.assertEqual(names[-1], "verify")
        self.assertLess(names.index("region"), names.index("confirm"))


class NoStdoutRewrapTest(unittest.TestCase):
    """Importing the pipeline must not blow up the caller's stdout.

    `run_temu_auth.py` rewraps sys.stdout at import time; importing THAT from
    the pipeline closed the pipeline's own stream (`I/O operation on closed
    file` on every print afterwards). The pipeline must not import it.
    """

    def test_pipeline_does_not_import_run_temu_auth(self):
        src = open(os.path.join(ROOT, "power", "run_temu_auth_pipeline.py"),
                   encoding="utf-8").read()
        self.assertNotIn("import run_temu_auth\n", src)
        self.assertNotIn("spec_from_file_location", src)

    def test_pipeline_rewrap_is_guarded(self):
        """The stdout rewrap must be behind `__main__`, not module level."""
        src = open(os.path.join(ROOT, "power", "run_temu_auth_pipeline.py"),
                   encoding="utf-8").read()
        self.assertIn('if __name__ == "__main__"', src)


class RegionSelectorOrderTest(unittest.TestCase):
    """Pin the fix for the 591-second region stage (2026-10-27).

    The real clickable element is `div.authentication_goto` (the button itself);
    `regionItem` / `regionSuffix` are text fragments that never respond. With
    goto last, the driver burned ~10 minutes trying 8 dead combinations before
    succeeding. Reordering made the same run take 7 seconds.
    """

    def test_goto_is_the_first_candidate(self):
        self.assertTrue(atd.REGION_SELECTORS[0].endswith("authentication_goto]"))

    def test_all_candidates_still_present(self):
        joined = " ".join(atd.REGION_SELECTORS)
        for frag in ("authentication_goto", "authentication_regionItem",
                     "authentication_regionSuffix"):
            self.assertIn(frag, joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
