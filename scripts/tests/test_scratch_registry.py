#!/usr/bin/env python3
"""临时页登记表 / 强杀残留清理的契约测试（2026-10-27 用户投诉后加）。

用户原话：「为什么你新开的标签页用完不关？」

一查发现根因不在 `finally`：任务被计划任务**超时强杀**（结果码 0x41306 /
`TerminateProcess`）时，Python 连一行收尾代码都跑不到。实测证据是窗口里两个
「销量分析」页：创建于任务运行时刻、对 `Runtime.evaluate` 完全不响应 ——
那正是脚本开完没关、随后进程被杀的残留页。

所以需要一层**不依赖进程存活**的兜底：
  建页就把 targetId 落盘 → 下一次运行开工前，把「登记过但进程已死」的页关掉。

这组测试钉住四条不能破的边界：
  ① 只关**自己登记过**的页（用户的页永远安全）；
  ② 进程还活着的那条记录**不许**碰（两条流水线并发跑时）；
  ③ 窗口里只剩一个页时**不许**关（关掉整个店铺窗口会消失）；
  ④ 全程 best-effort，绝不抛。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "power", "lib"))
sys.path.insert(0, os.path.join(ROOT, "bel_goods"))


def read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


def _page(tid, url="https://agentseller.temu.com/main/data-center/goods-data"):
    return {"id": tid, "targetId": tid, "url": url, "type": "page",
            "webSocketDebuggerUrl": "ws://x/%s" % tid}


DEAD_PID = 999999          # 一定不存在的进程


class ScratchRegistryTest(unittest.TestCase):
    def setUp(self):
        import cdp_transport
        self.t = cdp_transport
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = Path(self.tmp.name) / "scratch_tabs.json"
        self._patch = mock.patch.object(self.t, "_SCRATCH_REG", self.reg)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()

    def _seed(self, tid="TID1", pid=DEAD_PID, port=1234, age_s=0):
        import time
        self.t._reg_save([{
            "tid": tid, "port": port, "pid": pid,
            "url": "https://agentseller.temu.com/main/data-center/goods-data",
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }])

    # ---------------------------------------------------------------- 登记
    def test_create_target_registers_the_tab(self):
        with mock.patch.object(self.t, "http_version", return_value={"webSocketDebuggerUrl": "ws://x"}), \
             mock.patch.object(self.t, "Cdp") as Cdp, \
             mock.patch.object(self.t, "sweep_leaked_scratch", return_value=[]):
            Cdp.return_value.send.return_value = {"result": {"targetId": "NEW1"}}
            tid = self.t.create_target(1234, "about:blank")
        self.assertEqual(tid, "NEW1")
        recs = self.t._reg_load()
        self.assertEqual([r["tid"] for r in recs], ["NEW1"])
        self.assertEqual(recs[0]["pid"], os.getpid())
        self.assertEqual(recs[0]["port"], 1234)

    def test_create_target_sweeps_before_creating(self):
        """开工前必须先扫残留 —— 否则被强杀的页永远清不掉。"""
        src = read("power/lib/cdp_transport.py")
        body = src.split("def create_target", 1)[1].split("def close_target", 1)[0]
        self.assertIn("sweep_leaked_scratch", body)
        self.assertLess(body.find("sweep_leaked_scratch"), body.find("Target.createTarget"))

    def test_close_target_drops_the_record(self):
        self._seed("TID1", pid=os.getpid())
        with mock.patch.object(self.t, "http_version", return_value={"webSocketDebuggerUrl": "ws://x"}), \
             mock.patch.object(self.t, "Cdp"), \
             mock.patch.object(self.t, "list_pages", return_value=[]):
            self.assertTrue(self.t.close_target(1234, "TID1"))
        self.assertEqual(self.t._reg_load(), [])

    # ---------------------------------------------------------------- 清理
    def test_sweep_closes_tab_from_dead_process(self):
        self._seed("TID1", pid=DEAD_PID)
        closed = []
        with mock.patch.object(self.t, "list_pages", return_value=[_page("TID1"), _page("USER")]), \
             mock.patch.object(self.t, "close_target",
                               side_effect=lambda p, tid: closed.append(tid) or True):
            out = self.t.sweep_leaked_scratch(1234)
        self.assertEqual(closed, ["TID1"], "死进程留下的页必须关")
        self.assertEqual([c[0] for c in out], ["TID1"])
        self.assertEqual(self.t._reg_load(), [], "关掉之后登记表要清空")

    def test_sweep_never_touches_live_process_tab(self):
        """并发跑两店时，另一条流水线开着的页不能动。"""
        self._seed("TID1", pid=os.getpid())
        closed = []
        with mock.patch.object(self.t, "list_pages", return_value=[_page("TID1"), _page("USER")]), \
             mock.patch.object(self.t, "close_target",
                               side_effect=lambda p, tid: closed.append(tid) or True):
            self.t.sweep_leaked_scratch(1234)
        self.assertEqual(closed, [])
        self.assertEqual([r["tid"] for r in self.t._reg_load()], ["TID1"],
                         "没动的记录要留着")

    def test_sweep_never_closes_the_last_tab(self):
        """关掉窗口最后一个页 ⇒ 整个店铺窗口消失（2026-09-24 事故）。"""
        self._seed("TID1", pid=DEAD_PID)
        closed = []
        with mock.patch.object(self.t, "list_pages", return_value=[_page("TID1")]), \
             mock.patch.object(self.t, "close_target",
                               side_effect=lambda p, tid: closed.append(tid) or True):
            self.t.sweep_leaked_scratch(1234)
        self.assertEqual(closed, [])
        self.assertEqual([r["tid"] for r in self.t._reg_load()], ["TID1"],
                         "没关掉就还得留着，下次再试")

    def test_sweep_prunes_records_whose_tab_is_gone(self):
        self._seed("TID1", pid=DEAD_PID)
        with mock.patch.object(self.t, "list_pages", return_value=[_page("USER")]):
            self.t.sweep_leaked_scratch(1234)
        self.assertEqual(self.t._reg_load(), [], "页已经不在了 → 记录该删")

    def test_sweep_survives_dead_port(self):
        self._seed("TID1", pid=DEAD_PID)
        with mock.patch.object(self.t, "list_pages", side_effect=RuntimeError("no port")):
            self.assertEqual(self.t.sweep_leaked_scratch(1234), [], "连不上就跳过，不许抛")
        self.assertEqual(len(self.t._reg_load()), 1, "连不上时记录保留，下次再试")

    def test_sweep_survives_corrupt_registry(self):
        self.reg.parent.mkdir(parents=True, exist_ok=True)
        self.reg.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.t._reg_load(), [], "坏文件当空表")
        self.assertEqual(self.t.sweep_leaked_scratch(1234), [])

    def test_registry_is_capped(self):
        """只留最近 200 条，长期跑不会无限增长。"""
        self.t._reg_save([{"tid": "T%d" % i, "port": 1, "pid": 1, "url": "", "at": ""}
                          for i in range(500)])
        with mock.patch.object(self.t, "http_version", return_value={"webSocketDebuggerUrl": "ws://x"}), \
             mock.patch.object(self.t, "Cdp") as Cdp, \
             mock.patch.object(self.t, "sweep_leaked_scratch", return_value=[]):
            Cdp.return_value.send.return_value = {"result": {"targetId": "NEW"}}
            self.t.create_target(1, "about:blank")
        self.assertLessEqual(len(self.t._reg_load()), 200)


class CallSitesSweepTest(unittest.TestCase):
    """两个会开临时页的入口都必须在开工前清障 + 仍保留 finally 关闭。"""

    def test_focus_scraper_sweeps(self):
        src = read("bel_goods/fetch_focus_spus.py")
        self.assertIn("sweep_leaked_scratch", src)

    def test_presale_reader_sweeps(self):
        src = read("power/lib/temu_presale.py")
        self.assertIn("sweep_leaked_scratch", src)

    def test_both_still_close_in_finally(self):
        for path in ("bel_goods/fetch_focus_spus.py", "power/lib/temu_presale.py"):
            self.assertIn("finally:", read(path), path)
            self.assertIn("_close_target(", read(path), path)

    def test_close_helpers_delegate_to_transport(self):
        """自己那套 raw closeTarget 会把登记表漏掉 → 必须走 cdp_transport.close_target。"""
        for path in ("bel_goods/fetch_focus_spus.py", "power/lib/temu_presale.py"):
            body = read(path).split("def _close_target", 1)[1].split("\n\n\n", 1)[0]
            self.assertIn("close_target(port, tid)", body, path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
