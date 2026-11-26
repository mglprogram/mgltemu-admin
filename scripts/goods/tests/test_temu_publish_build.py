# -*- coding: utf-8 -*-
"""temu_publish_build 离线单测。"""
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
SAMPLE_SPEC = Path(r"C:\Users\saing\.agents\skills\temu-publish\examples\spec_sample.json")


class TestTemuPublishBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SCHEMA_FILE.is_file() or not SAMPLE_SPEC.is_file():
            raise unittest.SkipTest("缺少 schema_BHY.json 或 spec_sample.json")
        cls.schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        cls.spec = json.loads(SAMPLE_SPEC.read_text(encoding="utf-8"))

    def test_sample_builds_clean_41_keys(self):
        payload, errs, warns = pb.build_draft_save_payload(self.spec, self.schema)
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])
        self.assertEqual(len(payload), 41)
        self.assertEqual(payload["cat5Id"], 30467)
        self.assertEqual(payload["cat6Id"], 0)
        self.assertEqual(len(payload["productPropertyReqs"]), 17)
        self.assertEqual(len(payload["productSkcReqs"][0]["productSkuReqs"]), 5)
        self.assertEqual(payload["productSkcReqs"][0]["productSkuReqs"][0]["siteSupplierPrices"][0]["supplierPrice"], 6999)

    def test_missing_required_attribute_fails(self):
        sp = copy.deepcopy(self.spec)
        del sp["attributes"]["风格"]
        _, errs, _ = pb.build_draft_save_payload(sp, self.schema)
        self.assertTrue(any("风格" in e for e in errs))

    def test_bad_composition_sum_fails(self):
        sp = copy.deepcopy(self.spec)
        sp["attributes"]["成分"] = {"聚酯纤维(涤纶）": 80}
        _, errs, _ = pb.build_draft_save_payload(sp, self.schema)
        self.assertTrue(any("100%" in e for e in errs))

    def test_summarize_size_spec(self):
        vo = {
            "businessId": 17595937603747,
            "name": "102105",
            "className": "男上装",
            "contentVO": {
                "meta": {"elements": [
                    {"id": 10002, "name": "胸围全围", "unit": "cm"},
                    {"id": 10003, "name": "衣长", "unit": "cm"},
                    {"id": 10004, "name": "袖长", "unit": "cm"},
                ]},
                "records": [
                    {"values": {"1": "S", "10002": "100", "10003": "71", "10004": "77.5"}},
                    {"values": {"1": "M", "10002": "108", "10003": "72.5", "10004": "79"}},
                ],
            },
        }
        summ = pb.summarize_size_spec(vo)
        self.assertEqual(summ["name"], "102105")
        self.assertEqual(summ["rows"][0]["尺码"], "S")
        self.assertEqual(summ["rows"][0]["胸围全围(cm)"], "100")
        self.assertEqual(summ["rows"][1]["衣长(cm)"], "72.5")

    def test_check_size_spec_matches(self):
        vo = {"name": "t", "contentVO": {"records": [
            {"values": {"1": "S"}}, {"values": {"1": "M"}}, {"values": {"1": "L"}}]}}
        self.assertTrue(pb.check_size_spec_matches(["S", "M"], vo)["ok"])
        bad = pb.check_size_spec_matches(["S", "XXXL"], vo)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["missing_in_template"], ["XXXL"])

    def test_verify_readback_ok_and_mismatch(self):
        payload, _, _ = pb.build_draft_save_payload(self.spec, self.schema)
        # 回读结构与发送结构不同层：运费/站点嵌在 productSaleExtAttr 下
        got = {
            "productName": payload["productName"],
            "categories": {f"cat{i}": {"catId": payload[f"cat{i}Id"]} for i in range(1, 11)},
            "productPropertyList": payload["productPropertyReqs"],
            "productSkcList": [{"productSkuList": payload["productSkcReqs"][0]["productSkuReqs"]}],
            "carouselImageUrls": payload["carouselImageUrls"],
            "sizeTemplateIds": payload["sizeTemplateIds"],
            "productWhExtAttr": {"productOrigin": payload["productWhExtAttrReq"]["productOrigin"]},
            "productSaleExtAttr": {
                "productShipment": {
                    "freightTemplate": {"freightTemplateId": payload["productShipmentReq"]["freightTemplateId"]}
                },
                "productSemiManaged": {
                    "bindSites": [{"siteId": s} for s in payload["productSemiManagedReq"]["bindSiteIds"]]
                },
            },
        }
        chk = pb.verify_draft_readback(payload, got)
        self.assertTrue(chk["ok"], chk)
        self.assertTrue(chk["freight_match"])
        self.assertTrue(chk["sites_match"])
        got["productName"] = "wrong"
        self.assertFalse(pb.verify_draft_readback(payload, got)["ok"])


if __name__ == "__main__":
    unittest.main()
