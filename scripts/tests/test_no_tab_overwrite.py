#!/usr/bin/env python3
"""Pin the "never overwrite the user's tab" contract (2026-10-27).

User requirement, quoted verbatim:
    你在执行定时任务时打开新标签，不允许把我打开的标签覆盖，尤其是BEL

The failure mode this prevents: every driver picks an existing page when one
matches ("reuse, don't stack tabs"). That is fine — until the code follows it
with `page.goto(...)` to "correct" the URL. That single call rewrites the tab
the user is looking at: scroll position gone, half-filled form gone, filters
gone. From the user's side it looks like their page was hijacked.

Fix: a page that was picked up (not created by us) is marked `mark_foreign()`,
and `goto()` on such a page raises `ForeignPageError` instead of navigating.
Callers must open their own tab (`open_own_tab`).
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "power", "lib"))

import cdp_transport as t  # noqa: E402


def read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


def _page(url="https://agentseller.temu.com/goods/list", tid="T1"):
    return t.CdpPage(9999, {"id": tid, "targetId": tid,
                            "webSocketDebuggerUrl": "ws://page", "url": url})


class ForeignPageErrorTest(unittest.TestCase):
    def test_error_class_exists_and_is_a_cdp_error(self):
        self.assertTrue(issubclass(t.ForeignPageError, t.CdpError))

    def test_fresh_page_is_not_foreign(self):
        self.assertFalse(_page().is_foreign())

    def test_mark_foreign_flips_the_flag(self):
        pg = _page()
        pg.mark_foreign("test")
        self.assertTrue(pg.is_foreign())

    def test_goto_on_foreign_page_raises(self):
        """THE regression: silently rewriting the user's tab."""
        pg = _page("https://agentseller.temu.com/goods/list")
        pg.mark_foreign("用户开着的商品列表页")
        with self.assertRaises(t.ForeignPageError) as ctx:
            pg.goto("https://agentseller-us.temu.com/mmsos/orders.html")
        msg = str(ctx.exception)
        self.assertIn("拒绝改写复用页", msg)
        self.assertIn("goods/list", msg)

    def test_goto_on_own_page_is_allowed(self):
        pg = _page()
        sent = []

        def _fake_send(method, params=None, timeout=60.0):
            sent.append((method, params))
            return {}

        pg._send = _fake_send          # type: ignore[assignment]
        pg.wait_for_load_state = lambda *a, **k: None  # type: ignore[assignment]
        pg.goto("https://agentseller-us.temu.com/mmsos/orders.html")
        self.assertEqual(sent[0][0], "Page.navigate")

    def test_error_message_carries_the_why(self):
        pg = _page()
        pg.mark_foreign("异常处理页是复用来的")
        with self.assertRaises(t.ForeignPageError) as ctx:
            pg.goto("https://x/")
        self.assertIn("异常处理页是复用来的", str(ctx.exception))

    def test_open_own_tab_exists(self):
        self.assertTrue(callable(getattr(t.CdpPage, "open_own_tab", None)))

    def test_open_own_tab_result_is_not_foreign(self):
        """A tab we created ourselves must be freely navigable."""
        pg = _page()
        pg._reused = False
        self.assertFalse(pg.is_foreign())


class DriversMarkReuseTest(unittest.TestCase):
    """Every driver that reuses a page must mark it."""

    def test_buylabel_orders_page_marks_foreign(self):
        src = read("power/lib/temu_buylabel_driver.py")
        i = src.find("def orders_page(self):")
        self.assertGreater(i, 0)
        body = src[i:i + 1400]
        self.assertIn("mark_foreign", body)

    def test_abnormal_find_marks_foreign(self):
        src = read("power/lib/temu_abnormal_driver.py")
        i = src.find("def _find(self):")
        self.assertGreater(i, 0)
        body = src[i:i + 1200]
        self.assertIn("mark_foreign", body)

    def test_buylabel_handles_foreign_page_error(self):
        src = read("power/lib/temu_buylabel_driver.py")
        self.assertIn("from cdp_transport import ForeignPageError", src)
        self.assertGreaterEqual(src.count("except ForeignPageError"), 2)


class GotoSitesGuardedTest(unittest.TestCase):
    """No driver may call goto on a page it did not create."""

    DRIVERS = (
        "power/lib/temu_buylabel_driver.py",
        "power/lib/temu_abnormal_driver.py",
        "power/lib/auth_temu_driver.py",
    )

    def test_every_goto_is_guarded_or_on_own_page(self):
        for path in self.DRIVERS:
            src = read(path)
            lines = src.splitlines()
            for lineno, line in enumerate(lines, 1):
                if ".goto(" not in line:
                    continue
                stripped = line.lstrip()
                # skip prose: comments and bare docstring/comment lines
                if stripped.startswith("#") or stripped.startswith("已无用"):
                    continue
                if ".goto(" not in stripped.split("#")[0]:
                    continue          # only mentioned in a trailing comment
                # look back for a guard
                start = max(0, lineno - 12)
                window = "\n".join(lines[start:lineno])
                guarded = (
                    "is_foreign" in window
                    or "open_own_tab" in window
                    or "ForeignPageError" in window
                )
                self.assertTrue(
                    guarded,
                    "%s:%d calls goto without a foreign-page guard:\n    %s"
                    % (path, lineno, line.strip()))

    def test_sales_driver_opens_own_tab(self):
        src = read("power/lib/temu_sales_driver.py")
        self.assertIn("_open_own_tab", src)
        # the export navigation must go through it, not a bare goto on a picked page
        i = src.find("_open_own_tab(b, _EXPORT_URL)")
        self.assertGreater(i, 0)


class AuthDriverProtectionTest(unittest.TestCase):
    def test_go_home_does_not_clobber_user_page(self):
        src = read("power/lib/auth_temu_driver.py")
        i = src.find("def _go_home_and_click_region")
        self.assertGreater(i, 0)
        body = src[i:i + 1600]
        self.assertIn("is_foreign", body)
        self.assertIn("open_own_tab", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
