#!/usr/bin/env python3
"""订单页「只用自己的页」的契约测试（2026-10-28 用户投诉后加）。

用户原话：「尤其是 BEL，为什么把我之前的订单页面覆盖了，然后执行完也没有关掉」。

现场证据（`_logs/overdue-daily/2026-10-28.log`，BEL 11:00–11:14）：
    弹窗…（订单页就绪）
    [待办] 点「即将逾期未发货 167」→ 列表按该待办筛选
    [列表] 每页条数：set:500
    [⑥] … 17 单 确认 (ok)
    [BEL] exit=0
—— 这些动作全都发生在**用户自己开着的那个订单页**上。

根因：`orders_page()` 的实现是「窗口里有订单页就复用」，命中的一定是用户的页。
2026-10-27 加的 `mark_foreign()` 只挡住了 `goto`（导航改写），**挡不住业务动作**：
筛选、改每页条数、逐单确认照样落在用户的页上。页没被关掉是**对的**（它是用户的页），
但状态被改完了 —— 用户看到的就是「覆盖了我的订单页，还没关」。

修复口径：**复用页只能读，不能当工作区**。要干活就开自己的页，跑完由 `close()` 收掉。
这组测试钉住三条：
  ① `orders_page()` 只能返回本次自己开的页，否则新开；
  ② `new_tab_background()` 不许「捡」窗口里已存在的同址页；
  ③ 卡死自愈只体检自己的页，不许重载/关掉用户的页。
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "power", "lib"))

import temu_buylabel_driver as D  # noqa: E402

ORDERS = "https://agentseller-us.temu.com/mmsos/orders.html"


class _Page:
    """CdpPage 的最小替身：只要 targetId / _url，且**访问 .url 会抛**（死页语义）。"""

    def __init__(self, tid: str, url: str):
        self.targetId = tid
        self._url = url
        self.closed = False

    @property
    def url(self):
        raise AssertionError(".url 走 Runtime.evaluate，死页会卡 60s；选页时不该碰它")

    def close(self):
        self.closed = True


class _Fake(D.BuylabelDriver):
    """BuylabelDriver 的替身：**继承真类**（保住 `_own_targets/_own_pages/_own_page_by`
    这些真实实现），只覆盖会碰浏览器/配置的成员。`__init__` 不调用（那里要端口/cfg）。
    """

    def __init__(self, pages, opened=(), cfg_url=ORDERS):
        self.cfg = {"orders_url": cfg_url}
        self.ctx = type("Ctx", (), {"pages": list(pages)})()
        self._opened_targets = list(opened)
        self.opened_urls = []
        self.blocked = []

    def _block_heavy(self, page):
        self.blocked.append(page)

    def new_tab_background(self, url, tries=8):
        self.opened_urls.append(url)
        p = _Page("NEW-tid", url)
        self._opened_targets.append("NEW-tid")
        self.ctx.pages.append(p)
        return p

    def log(self, *_a):
        pass


def call(fake, name):
    """以实例方式调用（辅助函数与真类实现共用）。"""
    return getattr(fake, name)()


class OrdersPageIsOursTest(unittest.TestCase):
    def test_user_order_page_is_never_returned(self):
        """用户在开着订单页 —— 我们**不能**拿它当工作区。"""
        user = _Page("USER1", ORDERS)
        fake = _Fake([user])
        page = call(fake, "orders_page")
        self.assertIsNot(page, user, "不许复用用户的订单页")
        self.assertEqual(fake.opened_urls, [ORDERS], "应该自己开一个订单页")
        self.assertEqual(page.targetId, "NEW-tid")

    def test_reuses_only_our_own_orders_page(self):
        """"同一轮里第二次要订单页 → 复用自己那个（不能一单开一个页）。"""
        mine = _Page("MINE1", ORDERS)
        fake = _Fake([mine], opened=["MINE1"])
        page = call(fake, "orders_page")
        self.assertIs(page, mine)
        self.assertEqual(fake.opened_urls, [], "自己已有就不再开")

    def test_own_page_that_is_not_orders_is_ignored(self):
        """自己的页还停在别处（比如 waybill）→ 不算订单页。"""
        other = _Page("MINE1", "https://agentseller-us.temu.com/mmsos/waybill.html")
        fake = _Fake([other], opened=["MINE1"])
        page = call(fake, "orders_page")
        self.assertIsNot(page, other)
        self.assertEqual(fake.opened_urls, [ORDERS])

    def test_selection_never_touches_page_url_property(self):
        """选页时只能读 `_url`（`page.url` 会 evaluate，死页上会卡死）。"""
        fake = _Fake([_Page("USER1", ORDERS)])
        call(fake, "orders_page")          # _Page.url 会抛，没抛＝没用它
        self.assertTrue(True)


class NewTabBackgroundNeverAdoptsTest(unittest.TestCase):
    """`new_tab_background` 旧实现会「捡」窗口里同址的页 —— 那正是用户的页。"""

    def test_preexisting_page_is_not_adopted(self):
        user = _Page("USER1", ORDERS)
        fake = _Fake([user], opened=[])
        # 只验证前置复用逻辑：把自己 open 的页列表清空后，它不能把 user 页返回
        banned = []

        def _create(url, **kw):
            banned.append(url)
            raise RuntimeError("stop-here")     # 不再往下走（只断言「没复用」）

        with mock.patch.object(D, "cdp_transport"):
            with mock.patch.object(fake, "new_tab_background", _create):
                pass
        # 直接调真实实现的前半段：把 browser 置空，让它走 CDP 分支并失败
        fake.browser = None
        fake.log = lambda *a: None
        fake._opened_targets = []
        with self.assertRaises(Exception):
            D.BuylabelDriver.new_tab_background(fake, ORDERS)
        self.assertNotIn(user, fake.blocked, "不许把用户的页当自己开的页处理")

    def test_source_only_reuses_own_pages(self):
        src = open(os.path.join(ROOT, "power/lib/temu_buylabel_driver.py"), encoding="utf-8").read()
        body = src.split("def new_tab_background", 1)[1].split("def ", 1)[0]
        self.assertIn("self._own_pages()", body,
                      "前置复用必须只遍历自己开的页")
        self.assertNotIn("for p in list(self.ctx.pages):\n                if _url_same_target",
                         body, "旧写法（遍历全部页）必须去掉")


class HealOnlyOwnPageTest(unittest.TestCase):
    def test_heal_ignores_user_order_page(self):
        """用户订单页卡死也**不许**由我们去重载/关掉。"""
        fake = _Fake([_Page("USER1", ORDERS)], opened=[])
        fake._port = 1234
        with mock.patch.object(D.cdp_transport, "list_pages",
                               return_value=[{"id": "USER1", "url": ORDERS}]):
            with mock.patch.object(D.cdp_transport, "probe_page_alive") as probe:
                self.assertEqual(call(fake, "_heal_hung_orders_page"), "none")
                probe.assert_not_called()

    def test_heal_targets_our_own_page(self):
        fake = _Fake([], opened=["MINE1"])
        fake._port = 1234
        with mock.patch.object(D.cdp_transport, "list_pages",
                               return_value=[{"id": "MINE1", "url": ORDERS}]):
            with mock.patch.object(D.cdp_transport, "probe_page_alive",
                                   return_value="alive") as probe:
                self.assertEqual(call(fake, "_heal_hung_orders_page"), "ok")
                probe.assert_called_once()


class SourceContractTest(unittest.TestCase):
    def test_orders_page_does_not_scan_all_pages(self):
        src = open(os.path.join(ROOT, "power/lib/temu_buylabel_driver.py"), encoding="utf-8").read()
        body = src.split("def orders_page", 1)[1].split("def _heal_hung_orders_page", 1)[0]
        self.assertIn("_own_page_by", body)
        self.assertNotIn("for p in self.ctx.pages:", body,
                         "不许再遍历全部页（会把用户的页当工作区）")

    def test_close_still_closes_what_we_opened(self):
        src = open(os.path.join(ROOT, "power/lib/temu_buylabel_driver.py"), encoding="utf-8").read()
        # 2026-10-28：改成先留底 `owned` 再遍历关闭（这份底同时当第二层的归属证据）。
        self.assertIn('owned = [tid for tid in (getattr(self, "_opened_targets"', src)
        self.assertIn("for tid in owned:", src)
        self.assertIn("owned_ids=set(owned)", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
