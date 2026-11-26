# -*- coding: utf-8 -*-
"""同一货号只对应一条草稿：plan 阶段要能按 SKC extCode 找到已有草稿并原地复用。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))


class TestDraftReuseLookup(unittest.TestCase):
    """直接测 stage_plan 内部逻辑：按 extCode 找草稿 + 把 productDraftId 塞进 payload。"""

    def _run(self, specs, drafts, want_ext="BHY-202301-10SetA", reuse=0):
        sys.path.insert(0, str(ROOT / "scripts" / "goods"))
        import run_temu_publish as rtp

        class FakeSess:
            def __init__(self):
                self.queries = []

            def draft_list(self, page=1, page_size=50):
                return {"total": len(drafts),
                        "pageItems": [{"productDraftId": d} for d in drafts]}

            def draft_query(self, did):
                self.queries.append(did)
                skcs = [{"extCode": want_ext}] if did == drafts[0] else [{"extCode": "OTHER-X"}]
                return {"productSkcList": skcs}

        captured = {}

        def fake_build(sp, sch, pic_resolver=None, base_defaults=None):
            return ({"productDraftId": 0,
                     "productSkcReqs": [{"extCode": sp["ext_code"], "productSkuReqs": []}],
                     "productSemiManagedReq": {"bindSiteIds": [100]}}, [], [])

        orig_build = rtp.pb.build_draft_save_payload
        rtp.pb.build_draft_save_payload = fake_build
        try:
            plans, err = rtp.stage_plan("BHY", specs, {}, None, sess=FakeSess(),
                                        reuse_draft=reuse)
        finally:
            rtp.pb.build_draft_save_payload = orig_build
        captured["plans"] = plans
        return plans, err

    def test_reuses_draft_by_ext_code(self):
        plans, _ = self._run([("s", {"ext_code": "BHY-202301-10SetA"})], [55363913492, 111])
        self.assertEqual(plans[0]["payload"]["productDraftId"], 55363913492)

    def test_explicit_draft_id_wins(self):
        plans, _ = self._run([("s", {"ext_code": "BHY-202301-10SetA"})],
                             [55363913492, 111], reuse=999)
        self.assertEqual(plans[0]["payload"]["productDraftId"], 999)

    def test_no_match_leaves_zero_and_will_create(self):
        plans, _ = self._run([("s", {"ext_code": "BHY-202301-10SetA"})], [111],
                             want_ext="BHY-NOT-THIS-ONE")
        # 草稿里没有这个货号 → 保持 0，apply 时才新建
        self.assertEqual(plans[0]["payload"]["productDraftId"], 0)


if __name__ == "__main__":
    unittest.main()
