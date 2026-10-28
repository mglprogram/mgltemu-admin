#!/usr/bin/env python3
"""「自己新开页，跑完即关」的契约测试 —— 异常单域 + 订单导出域（2026-10-28 用户口径）。

用户在 2026-10-28 明确要求把这条规则推广到剩下两处：
    「要不要我按今天这套（只用自己的页 + 跑完即关 + 测试）把这两处也一起改了？
      ：自己新开页，跑完即关」

两处的旧毛病（同一类）：
  · `temu_abnormal_driver._find()`：「窗口里有本域页就用」→ 命中的是**用户开着的
    异常处理页**，然后在这张页上切页签、改每页条数、转卖家履约；
  · `temu_sales_driver`：`_pick_page()` 挑「最像导出页」的那张（可能是用户的），
    且「已经是导出页就直接用」；自己开的页还从来不关（`b.close()` 只是 detach）。

这里钉住四条：
  ① 业务入口只认自己开的页（`_opened_targets`），没有就新开；
  ② 自己开的页一定登记（否则收尾关不掉）；
  ③ 收尾会真的关掉（`close()` / `finally` 里 `close_target`）；
  ④ 不再出现「按 URL 认领任意同址页」的写法（那正是捡到用户页的根因）。
"""

from __future__ import annotations

import ast
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "power", "lib"))

import temu_abnormal_driver as A  # noqa: E402
import temu_sales_driver as S  # noqa: E402


def read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return fh.read()


def body_of(src: str, name: str) -> str:
    """取类方法/函数的源码体（粗略边界：下一个同级 `def `）。"""
    part = src.split("def %s" % name, 1)[1]
    return part.split("\n    def ", 1)[0]


ABN = "power/lib/temu_abnormal_driver.py"
SALES = "power/lib/temu_sales_driver.py"


class AbnormalOwnsItsPageTest(unittest.TestCase):
    def test_find_only_returns_own_pages(self):
        src = read(ABN)
        body = body_of(src, "_find")
        self.assertIn("_own_targets()", body, "必须按自己的 targetId 过滤")
        self.assertIn("continue", body)

    def test_find_never_scans_by_url_alone(self):
        """旧实现是「key 在 url 里就返回」—— 那会命中用户的页。"""
        body = body_of(read(ABN), "_find")
        # 允许出现 key in url，但必须和「是自己开的」同一条判断链里
        self.assertLess(body.find("_own_targets"), body.find("key in"),
                        "先判归属，再判 URL")

    def test_own_page_is_recorded_for_cleanup(self):
        body = body_of(read(ABN), "_open_own_page")
        self.assertIn("_opened_targets.append", body)
        self.assertIn("create_target", body)

    def test_open_page_creates_when_no_own_page(self):
        body = body_of(read(ABN), "open_page")
        self.assertIn("_open_own_page", body)
        self.assertNotIn("复用已开着的异常处理页", body,
                         "不再复用用户的异常处理页当工作区")

    def test_close_closes_recorded_targets(self):
        body = body_of(read(ABN), "close")
        self.assertIn("for tid in list(getattr(self, \"_opened_targets\", []))", body)
        self.assertIn("close_target", body)

    def test_page_lookup_by_tid_not_url(self):
        """建页后按 targetId 认页；按 URL 认会命中用户的同址页。"""
        body = body_of(read(ABN), "_open_own_page")
        self.assertIn("_page_by_tid", body)
        self.assertNotIn("key in (t.get(\"url\")", body)


class SalesOwnsItsPageTest(unittest.TestCase):
    def test_open_own_tab_reports_target_id(self):
        body = body_of(read(SALES), "_open_own_tab")
        self.assertIn("return page, tid", body, "必须把 targetId 交回调用方，否则关不掉")

    def test_no_url_based_adoption(self):
        """不再「窗口里任意导出页就用它」—— 那是捡到用户页的根因。"""
        body = body_of(read(SALES), "_open_own_tab")
        self.assertNotIn('tid in (page.url or "")', body)
        self.assertIn("before", body, "要用运行前的快照排除用户已开的页")

    def test_failure_closes_the_created_target(self):
        body = body_of(read(SALES), "_open_own_tab")
        self.assertIn("close_target", body,
                      "认不到页时也要把自己建出来的 target 关掉")

    def test_export_always_opens_own_tab(self):
        """无条件自己开页：`_open_own_tab` 必须在任何「是不是导出页」判断**之前**。"""
        src = read(SALES)
        body = src.split("def export_daily_sales", 1)[1]
        self.assertIn("_open_own_tab(b, _EXPORT_URL, port)", body)
        self.assertLess(body.index("_open_own_tab"), body.index("_url_is_export_page"),
                        "标志位不能再决定「要不要开自己的页」（旧实现是已在导出页就直接用）")
        head = body[:body.index("_open_own_tab")]
        self.assertNotIn("_url_is_export_page(pg.url):", head,
                         "开页前不许先判断「窗口里已有导出页」")

    def test_export_closes_tab_in_finally(self):
        src = read(SALES)
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "export_daily_sales")
        tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try) and n.finalbody]
        dumped = " ".join(ast.dump(t) for t in tries)
        self.assertIn("close_target", dumped, "finally 里必须关掉自己的导出页")
        self.assertIn("own_tid", dumped)

    def test_modal_handling_happens_on_our_page(self):
        """`_close_modal` 不能先对 `_pick_page()` 拿到的页做（那可能是用户的页）。"""
        src = read(SALES)
        body = src.split("def export_daily_sales", 1)[1]
        self.assertLess(body.find("_open_own_tab"), body.find("_close_modal(pg)"),
                        "先开自己的页，再做弹窗处理")


class OpenOwnTabBehaviourTest(unittest.TestCase):
    """`_open_own_tab` 的行为（不碰真浏览器）：失败要抛、且绝不认领用户页。"""

    class _Ctx:
        def __init__(self, pages):
            self.pages = pages

    class _Browser:
        def __init__(self, pages):
            self.contexts = [OpenOwnTabBehaviourTest._Ctx(pages)]

    class _Page:
        def __init__(self, url):
            self.url = url

    def test_dead_port_raises_instead_of_falling_back(self):
        b = self._Browser([])
        with mock.patch("urllib.request.urlopen", side_effect=OSError("no port")):
            with self.assertRaises(RuntimeError):
                S._open_own_tab(b, S._EXPORT_URL, 1)

    def test_existing_user_page_is_never_returned(self):
        user = self._Page(S._EXPORT_URL)
        b = self._Browser([user])

        class _Resp:
            def read(self):
                return b'{"webSocketDebuggerUrl": "ws://x"}'

        with mock.patch("urllib.request.urlopen", lambda *a, **k: _Resp()), \
             mock.patch("cdp_transport.Cdp") as Cdp, \
             mock.patch("cdp_transport.close_target") as close2:
            Cdp.return_value.send.return_value = {"result": {"targetId": "NEW"}}
            with self.assertRaises(RuntimeError) as ctx:
                S._open_own_tab(b, S._EXPORT_URL, 1234)
            self.assertIn("没出现", str(ctx.exception))
            close2.assert_called()          # 认不到就关掉自己建出来的那个

    def test_new_page_is_returned_with_tid(self):
        user = self._Page(S._EXPORT_URL)
        pages = [user]
        b = self._Browser(pages)

        class _Resp:
            def read(self):
                return b'{"webSocketDebuggerUrl": "ws://x"}'

        created = self._Page("about:blank")

        def fake_send(ws, params=None, **kw):
            del ws, kw
            if (params or {}).get("background"):
                pages.append(created)       # 模拟浏览器把新页建出来
            return {"result": {"targetId": "NEW-tid"}}

        with mock.patch("urllib.request.urlopen", lambda *a, **k: _Resp()), \
             mock.patch("cdp_transport.Cdp") as Cdp:
            Cdp.return_value.send.side_effect = fake_send
            page, tid = S._open_own_tab(b, S._EXPORT_URL, 1234)
        self.assertEqual(tid, "NEW-tid")
        self.assertIs(page, created, "要拿新开的页，不是用户那张")
        self.assertIsNot(page, user)


if __name__ == "__main__":
    unittest.main(verbosity=2)
