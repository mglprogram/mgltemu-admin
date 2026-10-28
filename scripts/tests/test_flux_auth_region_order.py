# -*- coding: utf-8 -*-
"""BEL 授权失败回归：`flux_auth` 用错了区域入口选择器顺序 + 只会坐标点击。

事故
----
2026-10-28 18:45 `TEMU-Today-Sales` 的 BEL 分支：
    [auth] 认证页 → 点「中国地区·商家中心」
    [auth] 已点击「中国地区 商家中心」
    [auth] FAIL 点了区域入口后未出现授权确认页
    （245 秒，退出码 3）

根因
----
TEMU 区域页上「商家中心」有多个 class 片段，**只有 `authentication_goto` 是按钮本体**，
`regionItem` / `regionSuffix` 只是文字片段（点它不触发）。而 `flux_auth.REGION_JS`
的顺序是 regionItem → regionSuffix → goto，于是先点了个空、白等 240 秒。

同一个坑 2026-10-27 已在 `power/lib/auth_temu_driver.py` 修过（那边顺序放对后
591 秒 → 7 秒）。但项目里有**两份实现**，`flux_auth` 这份没跟着改。

另外 `flux_auth` 只会坐标点击；后台标签页上 CDP 的 Input 事件打不到元素
（2026-10-10 实测：坐标点没反应、JS 点成功）。

本测试把两件事钉住：
  ① 两份实现的区域选择器顺序必须一致，且 `authentication_goto` 排第一；
  ② `flux_auth` 必须**先 JS 点、失败再坐标点**。
"""
from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "bel_goods"))
sys.path.insert(0, os.path.join(_ROOT, "power", "lib"))

FLUX_AUTH = os.path.join(_ROOT, "bel_goods", "flux_auth.py")
AUTH_DRIVER = os.path.join(_ROOT, "power", "lib", "auth_temu_driver.py")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class RegionSelectorOrderTest(unittest.TestCase):
    """`authentication_goto` 是按钮本体，必须排第一。"""

    def test_flux_auth_puts_goto_first(self):
        import flux_auth as fa
        sels = fa.REGION_SELECTORS_JS
        self.assertTrue(sels, "REGION_SELECTORS_JS 不能为空")
        self.assertIn("authentication_goto", sels[0],
                      "第一个候选必须是 authentication_goto（按钮本体）；"
                      "regionItem/regionSuffix 只是文字片段，点它不触发。实际顺序：%r" % (sels,))

    def test_auth_driver_puts_goto_first(self):
        import auth_temu_driver as ad
        self.assertIn("authentication_goto", ad.REGION_SELECTORS[0],
                      "auth_temu_driver 的顺序被改回去了？实际：%r"
                      % (ad.REGION_SELECTORS,))

    def test_both_implementations_agree(self):
        """两份实现必须同序 —— 只改一处正是这次事故的成因。"""
        import auth_temu_driver as ad
        import flux_auth as fa

        def norm(sels):
            return tuple(s.split("authentication_")[-1].rstrip("]'\"") for s in sels)

        self.assertEqual(
            norm(fa.REGION_SELECTORS_JS), norm(ad.REGION_SELECTORS),
            "两份区域选择器顺序不一致：flux_auth=%r auth_driver=%r\n"
            "⇒ 改其中一处时必须两处一起改（2026-10-28 BEL 授权失败就是这个原因）"
            % (fa.REGION_SELECTORS_JS, ad.REGION_SELECTORS))


class JsClickBeforeCoordinateTest(unittest.TestCase):
    """后台标签页里坐标点不可靠，必须先 JS 点。"""

    def test_flux_auth_has_js_click(self):
        src = read(FLUX_AUTH)
        self.assertIn("REGION_CLICK_JS", src, "缺少 JS 点击脚本")

    def test_flux_auth_tries_js_before_coordinate(self):
        src = read(FLUX_AUTH)
        body = src.split("def ensure_logged_in", 1)[1]
        i_js = body.find("REGION_CLICK_JS")
        i_coord = body.find("real_click")
        self.assertGreaterEqual(i_js, 0, "ensure_logged_in 里必须用 JS 点")
        self.assertGreaterEqual(i_coord, 0, "坐标点要保留作兜底")
        self.assertLess(i_js, i_coord, "必须先 JS 点、失败再退回坐标点")

    def test_js_click_script_handles_inner_text_nodes(self):
        """TEMU 可点元素常是外层容器；命中最内层 SPAN/I 时要点最近的 a/button/div。"""
        import flux_auth as fa
        js = fa.REGION_CLICK_JS
        self.assertIn("closest('a,button,div')", js)
        self.assertIn("disabled", js, "disabled 的入口要跳过")

    def test_click_js_returns_which_selector_it_hit(self):
        """要能回报命中了哪个选择器，否则日志看不出点的对不对。"""
        import flux_auth as fa
        self.assertIn("return s;", fa.REGION_CLICK_JS)


class RegionScriptsAreValidJsTest(unittest.TestCase):
    def test_scripts_json_decode_the_selector_list(self):
        """选择器列表是 json.dumps 注入的 —— 手写引号会在页面里语法错误。"""
        src = read(FLUX_AUTH)
        self.assertIn("json.dumps(list(REGION_SELECTORS_JS))", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
