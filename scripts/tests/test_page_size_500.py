#!/usr/bin/env python3
"""Pin the "每页拉满 500" fix (2026-10-28).

Background: the user asked "处理逾期订单 你可以直接拉到每页500个订单". The list was
stuck at 20 rows while the todo card said 167 orders, so 147 orders were never
touched. Three separate defects stacked up:

  1. `_JS_PAGE_SIZE_HEAD` looked for an ancestor table containing "PO-". That
     anchor no longer matches (the PO text lives deeper), so the function
     returned an EMPTY dict `{}` — not even `no-sc` — and the caller bailed out.
  2. The click landed on the select *header*; the dropdown only opens when you
     click the whole `sizeChanger` container.
  3. `cdp_click` dispatched mouse events WITHOUT `buttons`. beast-core Select
     ignores that sequence entirely. With `buttons:1` it opens immediately.
  4. (cosmetic) The readback happened before the list re-fetched, so a
     successful 500 looked like `set:20`.

Measured before/after on BEL: rows 20 -> 238, dropdown value 20 -> 500.
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "power", "lib"))

import temu_buylabel_driver as d  # noqa: E402


def read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


class PageSizeHeadJsTest(unittest.TestCase):
    def test_head_js_does_not_require_po_anchor_only(self):
        """It must still try PO- first, but fall back when that anchor is gone."""
        js = d._JS_PAGE_SIZE_HEAD
        self.assertIn("PO-", js, "should still try the precise anchor first")
        # the fallback: take the last visible sizeChanger regardless of PO-
        self.assertIn("scs.length - 1", js)
        self.assertIn("no-head", js)

    def test_box_js_exists(self):
        self.assertTrue(hasattr(d, "_JS_PAGE_SIZE_BOX"))
        js = d._JS_PAGE_SIZE_BOX
        self.assertIn("sizeChanger", js)
        self.assertIn("state", js)

    def test_box_targets_container_not_header(self):
        """The container is what opens the dropdown; the header does not."""
        js = d._JS_PAGE_SIZE_BOX
        # must measure the sizeChanger element itself (sc.getBoundingClientRect)
        self.assertIn("sc.getBoundingClientRect", js)
        # and must NOT be scoped to the header
        self.assertNotIn("ST_head", js)


class CdpClickButtonsTest(unittest.TestCase):
    """beast-core Select ignores mouse events without `buttons`."""

    def test_cdp_click_sends_buttons(self):
        src = read("power/lib/temu_buylabel_driver.py")
        i = src.find("def cdp_click(self, page")
        self.assertGreater(i, 0)
        body = src[i:i + 1400]
        self.assertIn('"buttons": 0', body)
        self.assertIn('"buttons": 1', body)

    def test_pressed_has_buttons_one(self):
        src = read("power/lib/temu_buylabel_driver.py")
        i = src.find("def cdp_click(self, page")
        body = src[i:i + 1400]
        # pressed line carries buttons:1
        pressed = [ln for ln in body.splitlines() if "mousePressed" in ln]
        self.assertTrue(pressed)
        joined = "\n".join(body.splitlines())
        idx = joined.find("mousePressed")
        window = joined[idx:idx + 220]
        self.assertIn('"buttons": 1', window)


class SetListPageSizeTest(unittest.TestCase):
    def test_opens_dropdown_via_container(self):
        src = read("power/lib/temu_buylabel_driver.py")
        i = src.find("def set_list_page_size")
        self.assertGreater(i, 0)
        body = src[i:i + 4000]
        self.assertIn("_JS_PAGE_SIZE_BOX", body)
        self.assertIn("_open_dropdown", body)

    def test_has_dom_click_fallback(self):
        """New tabs put an INPUT over the dropdown coords; only DOM click works."""
        self.assertTrue(hasattr(d, "_JS_PAGE_SIZE_DOM_OPEN"))
        js = d._JS_PAGE_SIZE_DOM_OPEN
        self.assertIn("h.click()", js)
        self.assertIn("sizeChanger", js)

    def test_dom_click_is_the_last_resort(self):
        """Order matters: coords first (existing tabs), DOM click last (new tabs)."""
        src = read("power/lib/temu_buylabel_driver.py")
        i = src.find("def _open_dropdown")
        self.assertGreater(i, 0)
        nxt = src.find("\n        opts = _open_dropdown()", i)
        body = src[i:nxt if nxt > 0 else i + 2200]
        self.assertLess(body.find("_JS_PAGE_SIZE_BOX"), body.find("_JS_PAGE_SIZE_DOM_OPEN"))

    def test_readback_polls_until_value_changes(self):
        """A stale read made a successful 500 look like set:20."""
        src = read("power/lib/temu_buylabel_driver.py")
        i = src.find("def set_list_page_size")
        # take everything up to the next method definition
        nxt = src.find("\n    def ", i + 10)
        body = src[i:nxt if nxt > 0 else len(src)]
        self.assertIn("for _ in range(10)", body)
        self.assertIn("if got == pick", body)
        self.assertIn("rows=", body)

    def test_waits_for_layout_stability(self):
        """Rows alone are not enough: the dropdown x coordinate must settle too."""
        src = read("power/lib/temu_buylabel_driver.py")
        i = src.find("def set_list_page_size")
        nxt = src.find("\n    def ", i + 10)
        body = src[i:nxt if nxt > 0 else len(src)]
        self.assertIn("_prev", body)
        self.assertIn("_sig", body)


class PickOptionTest(unittest.TestCase):
    def test_prefers_cap(self):
        opts = [{"text": t} for t in ("5", "10", "20", "50", "100", "500")]
        self.assertEqual(d.pick_page_size_option(opts, 167, cap=500), "500")

    def test_never_exceeds_cap(self):
        opts = [{"text": t} for t in ("20", "100", "500")]
        self.assertEqual(d.pick_page_size_option(opts, 167, cap=100), "100")

    def test_empty_options(self):
        self.assertIsNone(d.pick_page_size_option([], 167, cap=500))


if __name__ == "__main__":
    unittest.main(verbosity=2)
