#!/usr/bin/env python3
"""Pin the hung-renderer detection/recovery contract (2026-10-27).

The bug: BEL's orders page renderer froze. Browser-level CDP answered in 0.1s
(`Target.getTargetInfo` OK) but every `Runtime.evaluate` timed out at 60s. The
run then retried the SAME dead page three times and was killed at the 45-minute
limit, with a log line reading `[FAIL] action=overdue 失败: ` — empty message.

Two things must never regress:
  1. `hung` (browser alive, JS thread dead) must be distinguishable from
     `gone` (port/target dead) and from `alive`.
  2. An empty exception message must never reach the log again.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "power", "lib"))
sys.path.insert(0, os.path.join(ROOT, "power"))


def read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


class ProbePageAliveTest(unittest.TestCase):
    def setUp(self):
        import cdp_transport
        self.t = cdp_transport

    def _target(self, tid="T1", ws="ws://page"):
        return {"id": tid, "targetId": tid, "webSocketDebuggerUrl": ws,
                "url": "https://agentseller-us.temu.com/mmsos/orders.html"}

    def test_no_target_id_is_gone(self):
        self.assertEqual(self.t.probe_page_alive(9999, {}), "gone")
        self.assertEqual(self.t.probe_page_alive(9999, None), "gone")

    def test_dead_port_is_gone(self):
        with mock.patch.object(self.t, "http_version", side_effect=RuntimeError("no port")):
            self.assertEqual(self.t.probe_page_alive(9999, self._target()), "gone")

    def test_browser_ok_but_js_dead_is_hung(self):
        """THE core case: browser layer answers, page JS thread does not."""
        browser_ok = mock.Mock()
        js_dead = mock.Mock()
        js_dead.send.side_effect = TimeoutError()

        def _mk(ws):
            return browser_ok if ws == "ws://browser" else js_dead

        with mock.patch.object(self.t, "http_version",
                               return_value={"webSocketDebuggerUrl": "ws://browser"}), \
             mock.patch.object(self.t, "Cdp", side_effect=_mk):
            self.assertEqual(self.t.probe_page_alive(9999, self._target()), "hung")

    def test_browser_and_js_ok_is_alive(self):
        ok = mock.Mock()
        ok.send.return_value = {"result": {"result": {"value": 1}}}
        with mock.patch.object(self.t, "http_version",
                               return_value={"webSocketDebuggerUrl": "ws://browser"}), \
             mock.patch.object(self.t, "Cdp", return_value=ok):
            self.assertEqual(self.t.probe_page_alive(9999, self._target()), "alive")

    def test_browser_dead_is_gone_not_hung(self):
        dead = mock.Mock()
        dead.send.side_effect = RuntimeError("no such target")
        with mock.patch.object(self.t, "http_version",
                               return_value={"webSocketDebuggerUrl": "ws://browser"}), \
             mock.patch.object(self.t, "Cdp", return_value=dead):
            self.assertEqual(self.t.probe_page_alive(9999, self._target()), "gone")

    def test_missing_page_ws_is_hung(self):
        """Target exists but has no page websocket -> JS thread unreachable."""
        ok = mock.Mock()
        ok.send.return_value = {}
        with mock.patch.object(self.t, "http_version",
                               return_value={"webSocketDebuggerUrl": "ws://browser"}), \
             mock.patch.object(self.t, "Cdp", return_value=ok):
            self.assertEqual(
                self.t.probe_page_alive(9999, self._target(ws="")), "hung")


class RecoverHungPageTest(unittest.TestCase):
    def setUp(self):
        import cdp_transport
        self.t = cdp_transport

    def test_does_not_close_a_healthy_page(self):
        with mock.patch.object(self.t, "probe_page_alive", return_value="alive"), \
             mock.patch.object(self.t, "close_target") as ct:
            self.assertEqual(self.t.recover_hung_page(9999, {"id": "T1"}), "alive")
        ct.assert_not_called()

    def test_closes_a_hung_page_when_others_exist(self):
        with mock.patch.object(self.t, "probe_page_alive", return_value="hung"), \
             mock.patch.object(self.t, "list_pages",
                               return_value=[{"id": "T1"}, {"id": "T2"}]), \
             mock.patch.object(self.t, "close_target", return_value=True) as ct:
            self.assertEqual(self.t.recover_hung_page(9999, {"id": "T1"}), "closed")
        ct.assert_called_once()

    def test_never_closes_the_last_tab(self):
        """2026-10-27 accident: closing the only tab killed the whole store window."""
        with mock.patch.object(self.t, "probe_page_alive", return_value="hung"), \
             mock.patch.object(self.t, "list_pages", return_value=[{"id": "T1"}]), \
             mock.patch.object(self.t, "close_target") as ct:
            self.assertEqual(self.t.recover_hung_page(9999, {"id": "T1"}), "last-tab")
        ct.assert_not_called()

    def test_gone_page_is_not_closed(self):
        with mock.patch.object(self.t, "probe_page_alive", return_value="gone"), \
             mock.patch.object(self.t, "close_target") as ct:
            self.assertEqual(self.t.recover_hung_page(9999, {"id": "T1"}), "gone")
        ct.assert_not_called()

    def test_heal_uses_safe_replace_order(self):
        """Must open the new page BEFORE closing the dead one.

        Closing first kills the whole store window when the hung page is the
        only tab (2026-10-27, measured: window gone, port gone).
        """
        src = read("power/lib/temu_buylabel_driver.py")
        body = src[src.find("def _heal_hung_orders_page"):]
        nxt = body.find("\n    def ", 10)
        body = body[:nxt] if nxt > 0 else body
        self.assertIn("replace_hung_page", body)
        # the destructive path must NOT be called directly from the heal path
        self.assertNotIn("close_target(", body)

    def test_replace_hung_page_opens_before_closing(self):
        src = read("power/lib/cdp_transport.py")
        i = src.find("def replace_hung_page")
        self.assertGreater(i, 0)
        body = src[i:]
        nxt = body.find("\ndef ", 10)
        body = body[:nxt] if nxt > 0 else body
        self.assertLess(body.find("create_target"), body.find("close_target"))


class DriverWiringTest(unittest.TestCase):
    def test_orders_page_ready_heals_before_use(self):
        src = read("power/lib/temu_buylabel_driver.py")
        self.assertIn("_heal_hung_orders_page", src)
        # must be called inside _orders_page_ready, before orders_page()
        i = src.find("def _orders_page_ready(self):")
        self.assertGreater(i, 0)
        body = src[i:i + 1200]
        self.assertIn("_heal_hung_orders_page()", body)
        self.assertLess(body.find("_heal_hung_orders_page()"),
                        body.find("page = self.orders_page()"))

    def test_heal_does_not_recycle_port(self):
        """The safe replace keeps the window alive, so the port does NOT change.

        (Only the destructive close-first path recycled the renderer and moved
        the port 58538 -> 60091. We no longer take that path.)
        """
        src = read("power/lib/temu_buylabel_driver.py")
        body = src[src.find("def _heal_hung_orders_page"):]
        nxt = body.find("\n    def ", 10)
        body = body[:nxt] if nxt > 0 else body
        self.assertNotIn("find_port_after_recovery", body)

    def test_window_title_is_captured(self):
        src = read("power/lib/temu_buylabel_driver.py")
        self.assertIn("_window_title", src)


class EmptyErrorMessageTest(unittest.TestCase):
    """`str(TimeoutError())` is '' — the log showed `失败: ` with nothing after."""

    def test_timeout_error_str_is_empty(self):
        self.assertEqual(str(TimeoutError()).strip(), "")

    def test_runner_has_fallback_detail(self):
        src = read("power/run_temu_buylabel.py")
        self.assertIn("_detail", src)
        self.assertIn("type(e).__name__", src)
        self.assertIn("无消息", src)

    def test_abnormal_runner_also_has_fallback(self):
        """Same class of bug exists in the sibling entry point."""
        src = read("power/run_temu_abnormal.py")
        if "action=%s 失败" in src:
            self.assertIn("type(e).__name__", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
