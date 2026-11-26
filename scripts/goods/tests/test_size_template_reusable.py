# -*- coding: utf-8 -*-
"""尺码表创建口径：必须建「商品私有模板」(catId + reusable=false) 才能让后台渲染。

2026-11-26 实测对比：
  公共模板 17595938497252 (reusable=true, catId=null)
    → draft.sizeTemplateIds 写进去了，但后台刷新后仍是「+ 添加尺码表」
  私有模板 17595938694909 (reusable=false, catId=31910)
    → draft.sizeTemplateIds 写进去后，后台刷新正常显示数值表
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import temu_publish_build as pb  # noqa: E402

WANT = {"S": {"衣长": "63", "胸围全围": "96", "袖长": "16"}}


class TestSizespecPayloadReusable(unittest.TestCase):
    def setUp(self):
        self.vo = pb.build_size_template_vo("top", WANT, base_vo={"classId": 5}, name="202301")

    def test_no_cat_id_implies_reusable(self):
        p = pb.build_sizespec_payload(self.vo, new_name="202301", cat_id=None)
        self.assertIsNone(p["catId"])
        self.assertTrue(p["reusable"])

    def test_with_cat_id_is_product_private(self):
        p = pb.build_sizespec_payload(self.vo, new_name="x", cat_id=31910)
        self.assertEqual(p["catId"], 31910)
        self.assertFalse(p["reusable"])

    def test_explicit_reusable_wins(self):
        p = pb.build_sizespec_payload(self.vo, cat_id=31910, reusable=True)
        self.assertTrue(p["reusable"])

    def test_ensure_size_template_creates_product_private_template(self):
        other = pb.build_size_template_vo("top", {"S": {"衣长": "1", "胸围全围": "1", "袖长": "1"}},
                                          base_vo={"classId": 5}, name="other")
        other["businessId"] = 9
        captured = {}

        class FakeSess:
            def sizespec_detail(self, bid, reusable=True):
                return other

            def sizespec_create(self, payload):
                captured.update(payload)
                return 123

        schema = {"size_templates": {"31910": [{"businessId": 9, "name": "other"}]}}
        pick = pb.ensure_size_template(31910, schema, "top", WANT, FakeSess(), name_hint="202301")
        self.assertFalse(pick["reused"])
        self.assertEqual(pick["businessId"], 123)
        # 关键：必须是私有模板，否则后台不渲染
        self.assertEqual(captured["catId"], 31910)
        self.assertFalse(captured["reusable"])
        self.assertEqual(captured["name"], "202301")


if __name__ == "__main__":
    unittest.main()
