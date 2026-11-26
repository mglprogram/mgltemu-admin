# -*- coding: utf-8 -*-
"""尺码默认口径（上衣=衣长/胸围/袖长，裤子=裤长/臀围/腰围）单测。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import temu_publish_build as pb  # noqa: E402

TOP_WANT = {
    "S": {"衣长": "63", "胸围全围": "96", "袖长": "16"},
    "M": {"衣长": "65", "胸围全围": "100", "袖长": "17"},
}
PANTS_WANT = {
    "M": {"裤长": "100", "臀围": "104", "腰围": "80"},
}


def _vo(elements, rows, name="tpl", class_id=3):
    els = [{"id": pb.SIZE_ELEM_ID[n], "name": n, "unit": "cm"} for n in elements]
    recs = []
    for size, vals in rows.items():
        values = {"1": size}
        for n in elements:
            values[str(pb.SIZE_ELEM_ID[n])] = vals.get(n, "")
        recs.append({"values": values})
    return {"businessId": 1, "name": name, "classId": class_id,
            "contentVO": {"meta": {"elements": els, "groups": [{"id": 1, "name": "尺码"}]},
                          "records": recs}}


class TestSizeProfile(unittest.TestCase):
    def test_profiles_are_exactly_user_defaults(self):
        self.assertEqual(pb.SIZE_PROFILES["top"]["elements"], ["衣长", "胸围全围", "袖长"])
        self.assertEqual(pb.SIZE_PROFILES["pants"]["elements"], ["裤长", "臀围", "腰围"])

    def test_normalize_maps_user_names(self):
        got, errs = pb.normalize_size_values("top", {"衣长": 63, "胸围": 96, "袖长": 16})
        self.assertEqual(errs, [])
        self.assertEqual(got, {"衣长": "63", "胸围全围": "96", "袖长": "16"})

    def test_normalize_rejects_extra_elements(self):
        # 肩宽不在上衣默认口径里 → 报错而不是静默忽略
        got, errs = pb.normalize_size_values("top", {"衣长": 63, "肩宽": 40})
        self.assertTrue(errs)
        self.assertNotIn("肩宽", got)

    def test_chest_not_multiplied_by_two(self):
        got, _ = pb.normalize_size_values("top", {"胸围": 96})
        self.assertEqual(got["胸围全围"], "96")

    def test_pants_profile(self):
        got, errs = pb.normalize_size_values("pants", {"裤长": 100, "臀围": 104, "腰围": 80})
        self.assertEqual(errs, [])
        self.assertEqual(got, {"裤长": "100", "臀围": "104", "腰围": "80"})

    def test_guess_profile(self):
        self.assertEqual(pb.guess_profile("女士休闲运动裤"), "pants")
        self.assertEqual(pb.guess_profile("女士休闲运动T恤"), "top")
        self.assertEqual(pb.guess_profile("", {"title_en": "Womens Yoga Pants"}), "pants")

    def test_exact_match_is_reused(self):
        vo = _vo(["衣长", "胸围全围", "袖长"], TOP_WANT)
        res = pb.size_spec_matches(vo, "top", TOP_WANT)
        self.assertTrue(res["ok"], res["diffs"])

    def test_extra_element_breaks_match(self):
        vo = _vo(["衣长", "胸围全围", "袖长", "肩宽"],
                 {k: dict(v, 肩宽="40") for k, v in TOP_WANT.items()})
        res = pb.size_spec_matches(vo, "top", TOP_WANT)
        self.assertFalse(res["ok"])
        self.assertTrue(any("量体项不一致" in d for d in res["diffs"]))

    def test_value_mismatch_breaks_match(self):
        rows = {k: dict(v) for k, v in TOP_WANT.items()}
        rows["S"]["胸围全围"] = "82"
        res = pb.size_spec_matches(_vo(["衣长", "胸围全围", "袖长"], rows), "top", TOP_WANT)
        self.assertFalse(res["ok"])
        self.assertTrue(any("S 胸围全围" in d for d in res["diffs"]))

    def test_missing_size_breaks_match(self):
        rows = {"S": TOP_WANT["S"]}
        res = pb.size_spec_matches(_vo(["衣长", "胸围全围", "袖长"], rows), "top", TOP_WANT)
        self.assertFalse(res["ok"])
        self.assertTrue(any("缺尺码 M" in d for d in res["diffs"]))

    def test_build_template_vo_has_only_profile_elements(self):
        vo = pb.build_size_template_vo("top", TOP_WANT, base_vo={"classId": 5},
                                       name="女士上衣-默认")
        names = [e["name"] for e in vo["contentVO"]["meta"]["elements"]]
        self.assertEqual(names, ["衣长", "胸围全围", "袖长"])
        self.assertEqual(vo["classId"], 5)
        row = vo["contentVO"]["records"][0]["values"]
        self.assertEqual(row["1"], "S")
        self.assertEqual(row["10002"], "96")
        self.assertEqual(row["10003"], "63")
        self.assertEqual(row["10004"], "16")

    def test_build_template_vo_pants(self):
        vo = pb.build_size_template_vo("pants", PANTS_WANT)
        names = [e["name"] for e in vo["contentVO"]["meta"]["elements"]]
        self.assertEqual(names, ["裤长", "臀围", "腰围"])
        row = vo["contentVO"]["records"][0]["values"]
        self.assertEqual(row["10005"], "100")  # 裤长
        self.assertEqual(row["10026"], "104")  # 臀围
        self.assertEqual(row["10024"], "80")   # 腰围

    def test_build_template_vo_roundtrips_through_matches(self):
        vo = pb.build_size_template_vo("top", TOP_WANT, base_vo={"classId": 5})
        self.assertTrue(pb.size_spec_matches(vo, "top", TOP_WANT)["ok"])

    def test_build_sizespec_payload_shape(self):
        vo = pb.build_size_template_vo("top", TOP_WANT, base_vo={"classId": 5})
        p = pb.build_sizespec_payload(vo, new_name="女士上衣-默认", cat_id=31910)
        self.assertEqual(p["catId"], 31910)
        self.assertEqual(p["classId"], 5)
        self.assertEqual(p["name"], "女士上衣-默认")
        self.assertFalse(p["reusable"])
        self.assertIn("unEnbleAutoSelectedList", p["ext"])
        self.assertEqual(len(p["content"]["records"]), 2)
        self.assertNotIn("valueList", p["content"]["records"][0])
        self.assertNotIn("unit", p["content"]["meta"]["elements"][0])


class TestPickSizeTemplate(unittest.TestCase):
    def test_picks_exact_and_skips_others(self):
        exact = _vo(["衣长", "胸围全围", "袖长"], TOP_WANT, name="exact")
        exact["businessId"] = 42
        other = _vo(["衣长", "胸围全围", "袖长"],
                    {k: dict(v, 衣长="1") for k, v in TOP_WANT.items()}, name="other")
        other["businessId"] = 7

        class FakeSess:
            def sizespec_detail(self, bid, reusable=True):
                return {42: exact, 7: other}[bid]

        schema = {"size_templates": {"31910": [{"businessId": 7, "name": "other"},
                                               {"businessId": 42, "name": "exact"}]}}
        pick = pb.pick_size_template(31910, schema, "top", TOP_WANT, sess=FakeSess())
        self.assertTrue(pick["reused"])
        self.assertEqual(pick["businessId"], 42)

    def test_builds_new_vo_when_nothing_matches(self):
        other = _vo(["衣长", "胸围全围", "袖长"],
                    {k: dict(v, 衣长="1") for k, v in TOP_WANT.items()}, name="other", class_id=5)
        other["businessId"] = 7

        class FakeSess:
            def sizespec_detail(self, bid, reusable=True):
                return other

        schema = {"size_templates": {"31910": [{"businessId": 7, "name": "other"}]}}
        pick = pb.pick_size_template(31910, schema, "top", TOP_WANT, sess=FakeSess())
        self.assertFalse(pick["reused"])
        self.assertIsNone(pick["businessId"])
        self.assertEqual(pick["classId"], 5)  # 从已有模板继承 classId
        self.assertTrue(pb.size_spec_matches(pick["vo"], "top", TOP_WANT)["ok"])


if __name__ == "__main__":
    unittest.main()
