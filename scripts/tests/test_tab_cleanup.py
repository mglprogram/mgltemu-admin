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
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "power", "lib"))
sys.path.insert(0, os.path.join(ROOT, "bel_goods"))


def read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


def _page(tid, url):
    return {"id": tid, "targetId": tid, "url": url, "type": "page"}


class CloseNewSellerPagesTest(unittest.TestCase):
    """`close_new_seller_pages` must be provably incapable of touching a
    pre-existing page or a non-seller page. This is the user's hard rule:
    不能动我正在使用的页面.
    """

    def setUp(self):
        import cdp_transport
        self.t = cdp_transport

    def _run(self, pages, before_ids, keep_min=1):
        closed_ids = []

        def _fake_close(port, tid):
            closed_ids.append(tid)
            return True

        with mock.patch.object(self.t, "list_pages", return_value=pages), \
             mock.patch.object(self.t, "_openers_of", return_value={}), \
             mock.patch.object(self.t, "close_target", side_effect=_fake_close):
            out = self.t.close_new_seller_pages(9999, before_ids,
                                                keep_min_tabs=keep_min)
        return closed_ids, out

    def test_baseline_pages_are_never_closed(self):
        pages = [
            _page("OLD1", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("OLD2", "https://agentseller.temu.com/goods/list"),
        ]
        closed, _ = self._run(pages, {"OLD1", "OLD2"})
        self.assertEqual(closed, [])

    def test_new_seller_page_is_closed(self):
        pages = [
            _page("OLD1", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("NEW1", "https://seller.kuajingmaihuo.com/settle/seller-login"),
        ]
        closed, _ = self._run(pages, {"OLD1"})
        self.assertEqual(closed, ["NEW1"])

    def test_new_non_seller_page_is_left_alone(self):
        """The user's shopping page / external site must survive."""
        pages = [
            _page("OLD1", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("USER1", "https://www.temu.com/"),
            _page("USER2", "https://us.shein.com/some-product"),
            _page("EXT1", "chrome-extension://abcdef/page.html"),
        ]
        closed, _ = self._run(pages, {"OLD1"})
        self.assertEqual(closed, [])

    def test_mixed_only_seller_closed(self):
        pages = [
            _page("OLD1", "https://agentseller.temu.com/goods/list"),
            _page("USER1", "https://www.temu.com/"),
            _page("NEW1", "https://seller.kuajingmaihuo.com/settle/seller-login"),
            _page("NEW2", "https://agentseller.temu.com/newon/product-select"),
        ]
        closed, _ = self._run(pages, {"OLD1"})
        # NEW1 是纯过程页（授权中间页）→ 关；
        # NEW2 是「上新/新建商品」工作面（用户天天在上面干活）→ **不关**。
        # ⚠ 2026-10-28 改：旧断言要求 NEW2 也被关，那正是"把用户页当临时页"的
        #   bug 本身（实测关掉了用户的 /goods/edit）。工作面现在归 KEEP_PATH_HINTS。
        self.assertEqual(closed, ["NEW1"])

    def test_orders_page_is_never_closed(self):
        """2026-10-27 accident: cleanup closed the freshly-opened orders page,
        so the window fell back to the QR login page and auth had to be redone."""
        pages = [
            _page("OLD1", "https://seller.kuajingmaihuo.com/login"),
            _page("NEW1", "https://agentseller-us.temu.com/mmsos/orders.html"),
        ]
        closed, _ = self._run(pages, {"OLD1"})
        self.assertEqual(closed, [], "the orders page IS the deliverable")

    def test_export_and_waybill_pages_are_kept(self):
        pages = [
            _page("OLD1", "https://seller.kuajingmaihuo.com/login"),
            _page("NEW1", "https://agentseller-us.temu.com/mmsos/export-order.html"),
            _page("NEW2", "https://agentseller-us.temu.com/mmsos/waybill.html?x=1"),
        ]
        closed, _ = self._run(pages, {"OLD1"})
        self.assertEqual(closed, [])

    def test_auth_pages_are_closed(self):
        """The stale auth/login tabs are exactly what SHOULD go."""
        pages = [
            _page("OLD1", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("NEW1", "https://seller.kuajingmaihuo.com/settle/seller-login"),
            _page("NEW2", "https://agentseller-us.temu.com/auth/authentication"),
        ]
        closed, _ = self._run(pages, {"OLD1"})
        self.assertEqual(sorted(closed), ["NEW1", "NEW2"])

    def test_keep_min_tabs_protects_against_wiping_the_window(self):
        """Never close the last real tab: that makes the whole store window vanish."""
        pages = [
            _page("NEW1", "https://agentseller-us.temu.com/mmsos/orders.html"),
        ]
        closed, _ = self._run(pages, set(), keep_min=1)
        self.assertEqual(closed, [], "must keep at least one real tab")

    def test_keep_min_tabs_allows_one_when_two_exist(self):
        pages = [
            _page("NEW1", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("NEW2", "https://seller.kuajingmaihuo.com/settle/seller-login"),
        ]
        closed, _ = self._run(pages, set(), keep_min=1)
        self.assertEqual(len(closed), 1)

    # ------------------------------------------------------------------
    # 2026-10-28 用户事故：任务运行期间用户打开「新建商品」，被收尾清理关掉。
    # 根因＝旧判据「启动时不存在 ⇒ 是我开的」。以下把这些场景钉住。
    # ------------------------------------------------------------------
    def test_user_opened_work_page_is_never_closed(self):
        """用户中途打开的 /goods/edit（新建商品）必须幸存 —— 本次事故原样复现。"""
        pages = [
            _page("OLD1", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("USER1", "https://agentseller.temu.com/goods/edit"
                           "?from=productList&productId=324816945"),
        ]
        closed, _ = self._run(pages, {"OLD1"})
        self.assertEqual(closed, [], "用户正在编辑的商品页不能关")

    def test_goods_workspace_paths_are_kept(self):
        """商品工作面全系（列表/新建/编辑/上新）都归保留清单。"""
        for path in (
            "/goods/list",
            "/goods/edit?productId=1",
            "/newon/product-select",
            "/product/detail?id=1",
        ):
            pages = [
                _page("OLD1", "https://agentseller-us.temu.com/mmsos/orders.html"),
                _page("USER1", "https://agentseller.temu.com" + path),
            ]
            closed, _ = self._run(pages, {"OLD1"})
            self.assertEqual(closed, [], "%s 是用户工作面，不能关" % path)

    def test_unknown_seller_page_is_not_closed_without_ownership_proof(self):
        """证明不了是自己开的页 ⇒ 一律不关（宁可漏关，不可错关）。"""
        pages = [
            _page("OLD1", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("MAYBE", "https://agentseller.temu.com/some/new/feature"),
        ]
        closed, _ = self._run(pages, {"OLD1"})
        self.assertEqual(closed, [], "没有归属证据就不能关")

    def test_owned_page_is_closed_even_without_scratch_path(self):
        """有归属证据（owned_ids）就照关 —— 否则临时页会越堆越多。"""
        pages = [
            _page("OLD1", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("MINE", "https://agentseller.temu.com/some/scratch/page"),
        ]
        closed_ids = []

        def _fake_close(port, tid):
            closed_ids.append(tid)
            return True

        with mock.patch.object(self.t, "list_pages", return_value=pages), \
             mock.patch.object(self.t, "_openers_of", return_value={}), \
             mock.patch.object(self.t, "close_target", side_effect=_fake_close):
            self.t.close_new_seller_pages(9999, {"OLD1"}, owned_ids={"MINE"})
        self.assertEqual(closed_ids, ["MINE"])

    def test_page_opened_by_our_page_is_closed(self):
        """站点从我们的页弹出来的页（openerId 指向自己的页）算自己开的。"""
        pages = [
            _page("OLD1", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("MINE", "https://agentseller.temu.com/scratch/one"),
            _page("POPUP", "https://agentseller.temu.com/scratch/two"),
        ]
        closed_ids = []

        def _fake_close(port, tid):
            closed_ids.append(tid)
            return True

        with mock.patch.object(self.t, "list_pages", return_value=pages), \
             mock.patch.object(self.t, "_openers_of",
                               return_value={"MINE": "", "POPUP": "MINE"}), \
             mock.patch.object(self.t, "close_target", side_effect=_fake_close):
            self.t.close_new_seller_pages(9999, {"OLD1"}, owned_ids={"MINE"})
        self.assertEqual(sorted(closed_ids), ["MINE", "POPUP"])

    def test_auth_intermediate_pages_still_get_closed(self):
        """授权中间页是纯过程页（用户不会在上面干活）→ 没有归属证据也要清。"""
        pages = [
            _page("OLD1", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("AUTH1", "https://seller.kuajingmaihuo.com/settle/seller-login"),
            _page("AUTH2", "https://agentseller-us.temu.com/auth/authentication"),
        ]
        closed, _ = self._run(pages, {"OLD1"})
        self.assertEqual(sorted(closed), ["AUTH1", "AUTH2"])

    def test_dead_port_is_a_noop(self):
        with mock.patch.object(self.t, "list_pages", side_effect=RuntimeError("no port")):
            self.assertEqual(self.t.close_new_seller_pages(9999, set()), [])

    def test_seller_hosts_covers_both_seller_domains_but_not_consumer(self):
        self.assertIn("agentseller-us.temu.com", self.t.SELLER_HOSTS)
        self.assertIn("agentseller.temu.com", self.t.SELLER_HOSTS)
        self.assertNotIn("www.temu.com", self.t.SELLER_HOSTS)
        self.assertNotIn("temu.com", self.t.SELLER_HOSTS)

    def test_snapshot_ids_tolerates_dead_port(self):
        with mock.patch.object(self.t, "list_pages", side_effect=RuntimeError("x")):
            self.assertEqual(self.t.snapshot_ids(9999), set())


class BaselineSnapshotOnceTest(unittest.TestCase):
    """`reconnect()` calls `connect()`; the baseline must not be reset.

    If it were reset, every page opened earlier in the same run would be folded
    into the baseline and could never be closed (2026-10-27 bug).
    """

    def test_buylabel_initialises_baseline_to_none(self):
        src = read("power/lib/temu_buylabel_driver.py")
        self.assertIn("self._baseline_targets = None", src)

    def test_buylabel_snapshots_only_when_none(self):
        src = read("power/lib/temu_buylabel_driver.py")
        self.assertIn('if getattr(self, "_baseline_targets", None) is None:', src)

    def test_abnormal_snapshots_in_connect(self):
        src = read("power/lib/temu_abnormal_driver.py")
        self.assertIn("snapshot_tabs", src)
        self.assertIn("close_new_seller_pages", src)

    def test_auth_driver_snapshots_and_cleans(self):
        src = read("power/lib/auth_temu_driver.py")
        self.assertIn("snapshot_tabs(port)", src)
        self.assertIn("close_new_seller_pages", src)

    def test_all_callers_pass_url_baseline(self):
        """URL baseline is the safety net for targetId churn."""
        for path in ("power/lib/auth_temu_driver.py",
                     "power/lib/temu_buylabel_driver.py",
                     "power/lib/temu_abnormal_driver.py",
                     "power/run_temu_auth_pipeline.py"):
            self.assertIn("before_urls", read(path), "%s must pass before_urls" % path)

    def test_pipeline_snapshots_and_cleans(self):
        src = read("power/run_temu_auth_pipeline.py")
        self.assertIn("_snapshot(", src)
        self.assertIn("_cleanup(", src)


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
        # 2026-10-28 改：先把 _opened_targets **留底**再关，这份底要当第二层的归属证据
        # 传下去（否则关完 _reg_drop 了，第二层就失去唯一能证明归属的线索）。
        self.assertIn('owned = [tid for tid in (getattr(self, "_opened_targets"', src,
                      '%s close() must snapshot _opened_targets before closing' % path)
        self.assertIn("owned_ids=set(owned)", src,
                      "%s must pass owned_ids as ownership proof" % path)

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
