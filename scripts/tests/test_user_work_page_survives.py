# -*- coding: utf-8 -*-
"""2026-10-28 用户事故回归：定时任务把用户正在编辑的「新建商品」页关掉了。

事故经过
--------
用户在任务运行期间打开了
    https://agentseller.temu.com/goods/edit?from=productList&productId=324816945
收尾清理把它当成"本次新开的临时页"关掉，日志里留下了

    [页] 已关本次新开的页：https://agentseller.temu.com/goods/edit?from=productList&productId=324816945

根因
----
`close_new_seller_pages` 的旧判据是**「启动时不存在 ⇒ 是我开的」**。
用户在任务运行期间打开的任何新标签页，都完全符合这个特征 —— 它分不出来。
所以这不是"某个任务写错了"，而是**共享清理逻辑的所有权判断本身就是错的**。

修法
----
改成**只关能证明是自己开的**：
  ① `owned_ids` / scratch 登记表（`create_target` 会落盘 targetId + pid）；
  ② `openerId` 指向我们的页（站点从我们的页弹出来的）；
  ③ 纯过程页（授权/登录中间页、about:blank）—— 用户不会在上面干活。
再加一道保险：商品工作面（`/goods/`、`/newon/`、`/product/`）进保留清单。

本测试把三种情形钉死：
  · 用户中途开的商品页 → 绝不能关
  · 有归属证据的临时页 → 照关（否则会越堆越多）
  · 证明不了归属的页 → 不关（宁可漏关，不可错关）
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "power", "lib"))

#: 事故里被误关的真实 URL（一字不差照抄日志）
ACCIDENT_URL = (
    "https://agentseller.temu.com/goods/edit"
    "?from=productList&productId=324816945"
)


def _page(tid, url):
    return {"id": tid, "targetId": tid, "url": url, "type": "page"}


class UserWorkPageSurvivesTest(unittest.TestCase):
    """用户的工作面永远不能被定时任务关掉。"""

    def setUp(self):
        import cdp_transport
        self.t = cdp_transport

    def _close(self, pages, before_ids, owned_ids=None, openers=None):
        closed = []

        def _fake_close(port, tid):
            closed.append(tid)
            return True

        with mock.patch.object(self.t, "list_pages", return_value=pages), \
             mock.patch.object(self.t, "_openers_of", return_value=openers or {}), \
             mock.patch.object(self.t, "close_target", side_effect=_fake_close):
            self.t.close_new_seller_pages(9999, before_ids, owned_ids=owned_ids)
        return closed

    # ---- 事故原样复现 ----
    def test_the_exact_url_from_the_incident_is_never_closed(self):
        pages = [
            _page("ORDERS", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("USER", ACCIDENT_URL),
        ]
        closed = self._close(pages, {"ORDERS"})
        self.assertEqual(closed, [], "事故里被关掉的那个商品编辑页必须幸存")

    def test_incident_url_survives_even_if_wrongly_marked_as_ours(self):
        """就算归属证据搞错了（把它当成自己开的），保留清单也要拦住。

        两道保险必须独立生效：归属判断修好了，保留清单也不该被拿掉。
        """
        pages = [
            _page("ORDERS", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("USER", ACCIDENT_URL),
        ]
        closed = self._close(pages, {"ORDERS"}, owned_ids={"USER"})
        self.assertEqual(closed, [], "保留清单是第二道保险，不能被归属证据绕过")

    # ---- 归属判定 ----
    def test_unprovable_page_is_left_alone(self):
        pages = [
            _page("ORDERS", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("MAYBE", "https://agentseller.temu.com/unknown/feature"),
        ]
        self.assertEqual(self._close(pages, {"ORDERS"}), [])

    def test_provably_owned_scratch_page_is_closed(self):
        pages = [
            _page("ORDERS", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("MINE", "https://agentseller.temu.com/unknown/feature"),
        ]
        self.assertEqual(self._close(pages, {"ORDERS"}, owned_ids={"MINE"}),
                         ["MINE"])

    def test_page_opened_by_our_page_is_closed(self):
        pages = [
            _page("ORDERS", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("MINE", "https://agentseller.temu.com/a"),
            _page("POPUP", "https://agentseller.temu.com/b"),
        ]
        closed = self._close(pages, {"ORDERS"}, owned_ids={"MINE"},
                             openers={"MINE": "", "POPUP": "MINE"})
        self.assertEqual(sorted(closed), ["MINE", "POPUP"])

    def test_auth_intermediate_pages_still_cleaned(self):
        pages = [
            _page("ORDERS", "https://agentseller-us.temu.com/mmsos/orders.html"),
            _page("L", "https://seller.kuajingmaihuo.com/settle/seller-login"),
            _page("A", "https://agentseller-us.temu.com/auth/authentication"),
        ]
        closed = self._close(pages, {"ORDERS"})
        self.assertEqual(sorted(closed), ["A", "L"])


class KeepPathCoversGoodsWorkspaceTest(unittest.TestCase):
    """保留清单必须覆盖商家中心的所有用户工作面。"""

    def setUp(self):
        import cdp_transport
        self.t = cdp_transport

    def test_goods_workspace_paths_are_keep_paths(self):
        for url in (
            "https://agentseller.temu.com/goods/list",
            ACCIDENT_URL,
            "https://agentseller.temu.com/newon/product-select",
            "https://agentseller.temu.com/product/detail?id=1",
        ):
            self.assertTrue(self.t._is_keep_path(url), "%s 必须归保留清单" % url)

    def test_orders_and_export_still_kept(self):
        for url in (
            "https://agentseller-us.temu.com/mmsos/orders.html",
            "https://agentseller-us.temu.com/mmsos/export-order.html",
            "https://agentseller-us.temu.com/mmsos/waybill.html",
            "https://agentseller-us.temu.com/main/flux-analysis",
        ):
            self.assertTrue(self.t._is_keep_path(url), "%s 必须归保留清单" % url)

    def test_scratch_paths_are_process_pages_only(self):
        self.assertTrue(self.t._is_scratch_path(
            "https://seller.kuajingmaihuo.com/settle/seller-login"))
        self.assertTrue(self.t._is_scratch_path(
            "https://agentseller-us.temu.com/auth/authentication"))
        self.assertTrue(self.t._is_scratch_path("about:blank"))
        # 用户的商品页绝不能被当成过程页
        self.assertFalse(self.t._is_scratch_path(ACCIDENT_URL))


class OwnershipIsRecordedTest(unittest.TestCase):
    """`create_target` 必须把 targetId 落盘 —— 那是归属判定最硬的证据。"""

    def test_create_target_registers_the_id(self):
        src = open(os.path.join(_ROOT, "power", "lib", "cdp_transport.py"),
                   encoding="utf-8").read()
        self.assertIn("_reg_add(tid, port, url)", src)

    def test_close_uses_ownership_evidence(self):
        src = open(os.path.join(_ROOT, "power", "lib", "cdp_transport.py"),
                   encoding="utf-8").read()
        self.assertIn("def _is_ours(", src)
        self.assertIn("owned_ids", src)
        self.assertIn("_openers_of", src)

    def test_drivers_pass_owned_ids(self):
        for rel in ("power/lib/temu_buylabel_driver.py",
                    "power/lib/temu_abnormal_driver.py"):
            src = open(os.path.join(_ROOT, rel), encoding="utf-8").read()
            self.assertIn("owned_ids=set(owned)", src,
                          "%s must pass ownership proof to the cleanup" % rel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
