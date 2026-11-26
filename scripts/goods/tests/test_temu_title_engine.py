# -*- coding: utf-8 -*-
"""temu_title_engine 离线单测。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import temu_title_engine as te  # noqa: E402


class TestTitleEngine(unittest.TestCase):
    def test_find_banned(self):
        self.assertEqual(te.find_banned("9 Pcs Mens Lightweight Hoodies"), [])
        self.assertIn("upf", te.find_banned("Men UPF50 Hoodie"))
        self.assertIn("rash guard", te.find_banned("Mens rash guard shirt"))
        self.assertIn("gift", te.find_banned("Best gift for men"))

    def test_analyze_corpus_counts(self):
        corpus = te.analyze_corpus([
            "9pcs mens hooded sweatshirt breathable quick dry",
            "9pcs mens hooded sweatshirt lightweight",
            "mens hooded hoodies breathable",
        ])
        self.assertEqual(corpus["count"], 3)
        self.assertGreaterEqual(te.score_term("breathable", corpus), 2)
        self.assertGreaterEqual(te.score_term("hooded", corpus), 2)

    def test_pick_best(self):
        corpus = te.analyze_corpus(["Athletic hoodie", "Athletic top", "Casual shirt"])
        best, hits = te.pick_best(["Athletic", "Casual", "Sports"], corpus)
        self.assertEqual(best, "Athletic")
        self.assertEqual(hits, 2)

    def test_build_title_full_order(self):
        facts = {
            "qty_pcs": 9, "who": "Mens", "fabric": "Lightweight",
            "noun": "Hoodies", "feature": ["Breathable", "Quick Dry"],
            "style": "Athletic", "season": "All Season", "fit": "Regular Fit",
            "scene": ["Fishing", "Outdoor"],
        }
        r = te.build_title(facts, {}, variant="full")
        self.assertTrue(r["ok"])
        self.assertEqual(
            r["title"],
            "9 Pcs Mens Lightweight Hoodies Breathable Quick Dry Athletic "
            "All Season Regular Fit Fishing Outdoor")
        # 槽位顺序必须符合官方模板
        self.assertEqual([s["slot"] for s in r["slots"]],
                         ["几件装", "人群", "面料/颜色", "品类名词",
                          "细节/功能/图案", "细节/功能/图案", "风格",
                          "季节", "版型", "适用场景", "适用场景"])

    def test_build_title_safe_drops_qty(self):
        facts = {"qty_pcs": 9, "who": "Mens", "noun": "Hoodies"}
        r = te.build_title(facts, {}, variant="safe")
        self.assertNotIn("9 Pcs", r["title"])
        self.assertTrue(r["ok"])

    def test_build_title_lean_drops_style_season_fit(self):
        facts = {"qty_pcs": 9, "who": "Mens", "noun": "Hoodies",
                 "style": "Athletic", "season": "All Season", "fit": "Regular Fit"}
        r = te.build_title(facts, {}, variant="lean")
        for w in ("Athletic", "All Season", "Regular Fit"):
            self.assertNotIn(w, r["title"])

    def test_build_title_flags_banned(self):
        facts = {"qty_pcs": 9, "who": "Mens UPF50", "noun": "Hoodies"}
        r = te.build_title(facts, {}, variant="full")
        self.assertIn("upf", r["banned"])
        self.assertFalse(r["ok"])

    def test_length_guard(self):
        facts = {"who": "Mens", "noun": "X" * 300}
        r = te.build_title(facts, {}, variant="full")
        self.assertFalse(r["ok"])

    def test_suggest_facts_from_corpus(self):
        corpus = te.analyze_corpus([
            "mens athletic hoodies breathable",
            "mens athletic hoodies quick dry",
        ])
        facts = te.suggest_facts_from_corpus(corpus, {})
        self.assertEqual(facts.get("who"), "Mens")
        self.assertEqual(facts.get("style"), "Athletic")


if __name__ == "__main__":
    unittest.main()
