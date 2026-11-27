# -*- coding: utf-8 -*-
"""多复色（混合色 / 混合色1 / 混合色2…）：同一颜色只允许一个 SKC，多的只能用自定义名。

2026-11-27 实测：
  · 两个 SKC 都写「混合色」(specId=21263) → 第二个被静默吞掉，回读只剩 1 个
  · 第二个写自定义「混合色1」(specId=0, 自由文本) → 两个都保留，后台正常显示
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import temu_publish_build as pb  # noqa: E402

SCHEMA_FILE = ROOT / "data" / "temu-publish" / "schema_BHY.json"
CAT = 31883


class TestCustomColor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCHEMA_FILE.is_file():
            raise unittest.SkipTest("缺少 schema_BHY.json")
        sch = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        cls.props = (sch.get("templates") or {}).get(str(CAT), {}).get("properties") or []
        if not cls.props:
            raise unittest.SkipTest("schema 里没有类目 %s" % CAT)

    def test_standard_color_uses_real_ids(self):
        sp, err = pb._resolve_sale_spec(self.props, "颜色", "混合色")
        self.assertIsNone(err)
        self.assertGreater(sp["specId"], 0)
        self.assertGreater(sp["vid"], 0)
        self.assertFalse(sp.get("custom"))

    def test_custom_color_allowed_when_requested(self):
        sp, err = pb._resolve_sale_spec(self.props, "颜色", "混合色1", allow_custom=True)
        self.assertIsNone(err)
        self.assertEqual(sp["specName"], "混合色1")
        self.assertEqual(sp["specId"], 0)
        self.assertTrue(sp["custom"])

    def test_custom_color_rejected_by_default(self):
        sp, err = pb._resolve_sale_spec(self.props, "颜色", "混合色1")
        self.assertIsNone(sp)
        self.assertIn("找不到值", err)

    def test_custom_color_not_in_spec_property_reqs(self):
        """自定义颜色不能进 productSpecPropertyReqs（那里只放标准表值）。"""
        spec = {
            "cat_id": CAT,
            "title_en": "x",
            "sites": ["美国站"],
            "warehouses": ["恒信加州3", "加州三仓"],
            "freight_template_id": "HFT-17641354508533933163",
            "size_template_id": 17595940022053,
            "skcs": [
                {"ext_code": "A", "color": "混合色", "images": ["https://x/a.jpg"],
                 "sizes": {"S": {"price_usd": 49.99, "weight_g": 600}}},
                {"ext_code": "B", "color": "混合色1", "images": ["https://x/b.jpg"],
                 "sizes": {"S": {"price_usd": 49.99, "weight_g": 600}}},
            ],
            "attributes": {},
        }
        sch = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        payload, errs, _ = pb.build_draft_save_payload(spec, sch,
                                                       pic_resolver=lambda u: u)
        colors = [p for p in payload["productSpecPropertyReqs"]
                  if p.get("parentSpecName") == "颜色"]
        self.assertEqual(len(colors), 1, "自定义颜色不该进 productSpecPropertyReqs")
        self.assertEqual(colors[0]["propValue"], "混合色")
        self.assertEqual(len(payload["productSkcReqs"]), 2)


if __name__ == "__main__":
    unittest.main()
