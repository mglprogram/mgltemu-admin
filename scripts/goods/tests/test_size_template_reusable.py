# -*- coding: utf-8 -*-
"""尺码表创建口径：catId=None + reusable=true 才进公共模板库（2026-11-26 实测）。"""
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
        self.vo = pb.build_size_template_vo("top", WANT, base_vo={"classId": 5}, name="女士上衣-默认")

    def test_no_cat_id_implies_reusable(self):
        p = pb.build_sizespec_payload(self.vo, new_name="女士上衣-默认", cat_id=None)
        self.assertIsNone(p["catId"])
        self.assertTrue(p["reusable"])

    def test_with_cat_id_is_product_private(self):
        p = pb.build_sizespec_payload(self.vo, new_name="x", cat_id=31910)
        self.assertEqual(p["catId"], 31910)
        self.assertFalse(p["reusable"])

    def test_explicit_reusable_wins(self):
        p = pb.build_sizespec_payload(self.vo, cat_id=31910, reusable=True)
        self.assertTrue(p["reusable"])

    def test_ensure_size_template_creates_public_template(self):
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
        pick = pb.ensure_size_template(31910, schema, "top", WANT, FakeSess(), name_hint="女士上衣-默认")
        self.assertFalse(pick["reused"])
        self.assertEqual(pick["businessId"], 123)
        self.assertIsNone(captured["catId"])
        self.assertTrue(captured["reusable"])
        self.assertEqual(captured["name"], "女士上衣-默认")


if __name__ == "__main__":
    unittest.main()
