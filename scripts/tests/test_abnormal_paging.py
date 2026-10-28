#!/usr/bin/env python3
"""异常单「读全 + 分批推进 + 不假成功」的契约测试（2026-10-28）。

背景（今天的真实事故，日志 `_logs/abnormal-daily/2026-10-28.log`）：
    [页] 每页条数 → 30（open=NO_SELECTOR pick=SKIP）   ← 没设上，静默继续
    [②] 异常清单 10 单                               ← 只读了首页
    [③] 2/10 … 10/10 → 已转卖家履约
    [④] 复核：原清单 10 单 → 已消失 10 / 仍异常 0      ← 假成功
    而页面当时写着「共有 77 条」—— 67 条一直在积压，任务连续多天报"完成"。

三个必须钉住的点：
  ① 列表要**读全**（total > 本页行数时翻页）；
  ② 处理要**分批推进**（一批转完会从列表消失，重新取下一批），直到清空或达上限；
  ③ 复核要**以页面总数为准**，剩一条都不能说 complete。
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "power", "lib"))

import temu_abnormal_driver as A  # noqa: E402

CFG = {
    "url": "https://agentseller.temu.com/lgst/auth-warehouse/abnormal-order",
    "url_key": "abnormal-order",
    "tab_abnormal": "异常",
    "page_size": 30,
    "page_size_options": [10, 20, 30],
    "waits": {},
    "labels": {"to_seller": "转卖家履约", "cancel": "取消"},
    "abnormal": {"max_rounds": 8, "max_orders": 200},
}


class FakeDriver(A.TemuAbnormalDriver):
    """用假页面数据驱动真逻辑：不连浏览器、不点真实页面。"""

    def __init__(self, total: int, per_page: int = 30):
        super().__init__(dict(CFG), log=lambda *_: None, evidence_dir=None)
        self._page_obj = object()
        self.remaining = ["PO-211-%08d" % i for i in range(total)]
        self.per_page = per_page
        self.page_no = 1
        self.converted_calls = []

    # ---- 屏蔽 IO
    def _page_or_open(self):
        return self._page_obj

    def sweep_popups(self, stage=""):
        return {}

    def _empty_guard(self, page, swept, stage):
        return []

    def shot(self, page, name, force=False):
        return None

    def save(self, obj, name, extra=None):
        return None

    def wait(self, key="", factor=1.0):
        return None

    def list_total(self, page):
        return len(self.remaining)

    # ---- 假页面
    def _page_rows(self):
        start = (self.page_no - 1) * self.per_page
        return [{"po": p} for p in self.remaining[start:start + self.per_page]]

    def _js(self, page, script, arg=None):
        if script is A._JS_ROWS:
            return self._page_rows()
        if script is A._JS_TOTAL:
            return len(self.remaining)
        if script is A._JS_CLICK_PAGE:
            n = int(arg or 0)
            pages = max(1, (len(self.remaining) + self.per_page - 1) // self.per_page)
            if n < 1 or n > pages:
                return "NOT_FOUND"
            self.page_no = n
            return "CLICKED"
        return None

    # ---- 假业务动作：转掉的单会从列表消失（真实系统也是这样）
    def _row_present(self, page, po):
        return po in self.remaining

    def _convert_one(self, page, po):
        self.converted_calls.append(po)
        self.remaining.remove(po)

    def _apply_list_view(self, page):
        self.page_no = 1
        return len(self._page_rows())

    def _clear_popovers(self, page):
        return None

    def goto(self, url):          # 某些路径会调用 page.goto
        self.page_no = 1


class ReadAllTest(unittest.TestCase):
    def test_pages_through_all_rows(self):
        d = FakeDriver(total=77, per_page=30)
        out = d.read_list()
        self.assertEqual(out["total"], 77)
        self.assertEqual(out["pages"], 3)
        self.assertEqual(out["count"], 77, "必须读全 77 条")
        self.assertFalse(out["truncated"])
        self.assertEqual(len(set(out["pos"])), 77, "不能重复计数")
        self.assertEqual(d.page_no, 1, "翻完要回到第 1 页")

    def test_fits_one_page(self):
        d = FakeDriver(total=10, per_page=30)
        out = d.read_list()
        self.assertEqual(out["count"], 10)
        self.assertEqual(out["pages"], 1)


class BatchConvertTest(unittest.TestCase):
    def test_converts_everything_across_batches(self):
        """77 条、每批 30 → 三轮推平，且 remaining_total 必须为 0。"""
        d = FakeDriver(total=77, per_page=30)
        out = d.convert_all()
        self.assertEqual(len(out["converted"]), 77)
        self.assertEqual(out["failed"], [])
        self.assertEqual(out["remaining_total"], 0)
        self.assertTrue(out["ok"], out)

    def test_respects_max_orders_cap(self):
        cfg = dict(CFG)
        cfg["abnormal"] = {"max_rounds": 8, "max_orders": 40}
        d = FakeDriver(total=77, per_page=30)
        d.cfg = cfg
        out = d.convert_all()
        self.assertEqual(len(out["converted"]), 40, "单次上限要生效")
        self.assertGreater(out["remaining_total"], 0)
        self.assertFalse(out["ok"], "没清空就不能报 ok（剩下的留给下一轮）")

    def test_stops_when_no_progress(self):
        """所有单都转失败 → 不许死循环，且 ok=False。"""
        d = FakeDriver(total=10, per_page=30)

        def boom(page, po):
            raise RuntimeError("转不动")

        d._convert_one = boom
        out = d.convert_all()
        self.assertEqual(len(out["failed"]), 10)
        self.assertFalse(out["ok"])

    def test_explicit_list_is_used_as_is(self):
        d = FakeDriver(total=5, per_page=30)
        pos = ["PO-211-00000001", "PO-211-00000002"]
        out = d.convert_all(pos)
        self.assertEqual(out["targets"], pos)
        self.assertEqual(sorted(out["converted"]), sorted(pos))


class VerifyTotalTest(unittest.TestCase):
    """verify 必须以「页面总数」为准，不许只看自己那批。"""

    def test_verify_fails_when_page_still_has_rows(self):
        d = FakeDriver(total=77, per_page=30)

        class P:
            url = CFG["url"]

            def is_foreign(self):
                return False

            def goto(self, *_a, **_k):
                return None

        d._page_or_open = lambda: P()
        # verify 里会 goto + 读 rows；让 rows 还有内容（模拟没处理完）
        d.remaining = ["PO-211-%08d" % i for i in range(67)]
        res = d.verify(["PO-211-00000000"])
        self.assertFalse(res["ok"], "页面还剩 67 条 → 绝不能报完成")
        self.assertGreater(res["left_on_page"], 0)


class VerdictWordingTest(unittest.TestCase):
    """`[VERDICT]` 必须把「页面上还剩几条」说出来，否则又会变成假成功。"""

    def _verdict(self, out):
        sys.path.insert(0, os.path.join(ROOT, "power"))
        import run_temu_abnormal as R
        return R._domain_verdict("verify", out)

    def test_summary_mentions_page_remaining(self):
        v = self._verdict({"targets": ["PO-1"], "remaining": [], "ok": False,
                           "left_on_page": 67})
        self.assertFalse(v["complete"])
        self.assertEqual(v["page_remaining"], 67)
        self.assertIn("67", v["summary"])
        self.assertIn("未处理", v["summary"])

    def test_clean_run_has_no_warning(self):
        v = self._verdict({"targets": ["PO-1"], "remaining": [], "ok": True,
                           "left_on_page": 0})
        self.assertTrue(v["complete"])
        self.assertNotIn("未处理", v["summary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
