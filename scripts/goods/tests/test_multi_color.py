# -*- coding: utf-8 -*-
"""多复色固化：一个草稿挂 N 组复色，颜色自动递增、货号重复本地拦截。

2026-11-27 实测（草稿 50589964144）：
  · 同色两个 SKC → 第二个被吞（TEMU 一色一 SKC）
  · 「混合色 / 混合色1 / 混合色2 / 混合色3」+ 唯一货号 → 4 条全部存住
  · extCode 撞了 → 撞的那条被**静默丢弃**（save 仍返回 success），必须本地拦
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
ATTRS = {
    "材质": "聚酯纤维(涤纶）", "成分": {"聚酯纤维(涤纶）": 100}, "风格": "运动",
    "护理说明": "可机洗且不可干洗", "类型": "运动衫&连帽衫", "领型": "连帽",
    "面料": "高弹", "是否透明": "否", "图案": "纯色", "细节": "无",
    "场景": ["休闲"], "适用人群": "男士", "季节": ["春"], "场合": "运动",
    "版型": "常规", "织造方式": "针织(含钩织、毛织面料)", "印花类型": "无印花",
    "面料纹理1": "光面", "里料纹理": "无里料/无内衬",
    "面料克重1（g/m²)": "140",
}


def _skc(tag: str, color: str | None = None) -> dict:
    out = {
        "ext_code": "BHY-T-%s" % tag,
        "images": ["https://x/%s.jpg" % tag],
        "sizes": {"S": {"ext_code": "BHY-T-%s-S" % tag, "price_usd": 49.99, "weight_g": 600}},
    }
    if color is not None:
        out["color"] = color
    return out


class TestMultiColorSkc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCHEMA_FILE.is_file():
            raise unittest.SkipTest("缺少 schema_BHY.json")
        cls.schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        if not (cls.schema.get("templates") or {}).get(str(CAT)):
            raise unittest.SkipTest("schema 里没有类目 %s" % CAT)

    def _build(self, skcs):
        spec = {
            "cat_id": CAT, "title": "测试", "title_en": "test",
            "sites": ["美国站"], "warehouses": ["恒信加州3"],
            "freight_template_id": "HFT-17641354508533933163",
            "size_template_id": 17595940022053,
            "skcs": skcs, "attributes": copy.deepcopy(ATTRS),
        }
        return pb.build_draft_save_payload(spec, self.schema, pic_resolver=lambda u: u)

    def test_duplicate_color_auto_renamed(self):
        payload, errs, _ = self._build([_skc("A", "混合色"), _skc("B", "混合色"),
                                        _skc("C", "混合色")])
        self.assertEqual(errs, [])
        colors = [k["mainProductSkuSpecReqs"][0]["specName"] for k in payload["productSkcReqs"]]
        self.assertEqual(colors, ["混合色", "混合色1", "混合色2"])

    def test_auto_numbering_is_contiguous_from_two(self):
        payload, errs, _ = self._build([_skc("A", "混合色"), _skc("B", "混合色"),
                                        _skc("C", "混合色"), _skc("D", "混合色")])
        self.assertEqual(errs, [])
        colors = [k["mainProductSkuSpecReqs"][0]["specName"] for k in payload["productSkcReqs"]]
        self.assertEqual(colors, ["混合色", "混合色1", "混合色2", "混合色3"])

    def test_explicit_colors_kept(self):
        payload, errs, _ = self._build([_skc("A", "混合色"), _skc("B", "混合色1"),
                                        _skc("C", "混合色2")])
        self.assertEqual(errs, [])
        colors = [k["mainProductSkuSpecReqs"][0]["specName"] for k in payload["productSkcReqs"]]
        self.assertEqual(colors, ["混合色", "混合色1", "混合色2"])
        self.assertEqual(len(payload["productSkcReqs"]), 3)

    def test_missing_color_filled_in(self):
        payload, errs, _ = self._build([_skc("A", "混合色"), _skc("B"), _skc("C")])
        self.assertEqual(errs, [])
        colors = [k["mainProductSkuSpecReqs"][0]["specName"] for k in payload["productSkcReqs"]]
        self.assertEqual(len(colors), 3)
        self.assertEqual(colors[0], "混合色")
        self.assertEqual(len(set(colors)), 3, "自动补的颜色必须互不相同")

    def test_duplicate_skc_extcode_is_error(self):
        a = _skc("A", "混合色")
        b = _skc("A", "混合色1")   # 同一个 ext_code
        payload, errs, _ = self._build([a, b])
        self.assertTrue(any("SKC extCode 重复" in e for e in errs), errs)

    def test_duplicate_sku_extcode_is_error(self):
        a = _skc("A", "混合色")
        b = _skc("B", "混合色1")
        b["sizes"] = {"S": {"ext_code": "BHY-T-A-S", "price_usd": 49.99, "weight_g": 600}}
        payload, errs, _ = self._build([a, b])
        self.assertTrue(any("SKU extCode 重复" in e for e in errs), errs)

    def test_each_skc_keeps_own_images(self):
        a = _skc("A", "混合色")
        b = _skc("B", "混合色1")
        b["images"] = ["https://x/b-only.jpg"]
        payload, errs, _ = self._build([a, b])
        self.assertEqual(errs, [])
        self.assertEqual(payload["productSkcReqs"][1]["previewImgUrls"], ["https://x/b-only.jpg"])

    def test_custom_color_not_in_spec_property_reqs(self):
        payload, errs, _ = self._build([_skc("A", "混合色"), _skc("B", "混合色1")])
        self.assertEqual(errs, [])
        color_props = [p for p in payload["productSpecPropertyReqs"]
                       if p.get("parentSpecName") == "颜色"]
        self.assertEqual(len(color_props), 1)
        self.assertEqual(color_props[0]["propValue"], "混合色")


if __name__ == "__main__":
    unittest.main()
