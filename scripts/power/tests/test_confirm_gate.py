# -*- coding: utf-8 -*-
"""test_confirm_gate.py — 「必须领星和帕沃尔都发货成功才确认」的回归钉。

★ 用户 2026-11-24 原话：「这个点确认，必须核实到领星和帕沃尔全部发货成功」
  判据（同轮确认）：
    · 帕沃尔：`pw_now.json` 的 status == 已下单 **且** 有 id（拿到运单号，pw_track 阶段）
    · 恒信：`hx_oms_status.json` 的 status ∈ {2,3} **且** track 非空
  任一边不满足 ⇒ 不进确认列表，交人工。
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
if _POWER not in sys.path:
    sys.path.insert(0, _POWER)

import confirm_gate as G  # noqa: E402


def _seed(tmp_path, monkeypatch, pw_rows, hx_rows):
    """造一个假 out 目录并让 _latest 指向它。"""
    q = tmp_path / "2026-11-24" / "fulfill" / "BHY"
    q.mkdir(parents=True)
    (q / "pw_now.json").write_text(json.dumps({"rows": pw_rows}, ensure_ascii=False),
                                   encoding="utf-8")
    (q / "bhy_hx_oms_status.json").write_text(
        json.dumps({"rows": hx_rows}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(G, "_ROOT", str(tmp_path), raising=True)

    def fake_latest(pattern):
        base = os.path.basename(pattern)
        p = q / base.replace("*", "").replace("**", "")
        if base.startswith("*"):
            cand = list(q.glob(base.lstrip("*").lstrip("/").lstrip("\\") or "*"))
            return str(cand[0]) if cand else ""
        return str(p)

    monkeypatch.setattr(G, "_latest", lambda pat: str(next(iter(q.glob("*" + os.path.basename(pat).lstrip('*'))), "")),
                        raising=True)


def test_pw_needs_tracking_number(monkeypatch, tmp_path):
    """帕沃尔只「已下单」没有 id（运单号）⇒ 不算成功。"""
    _seed(tmp_path, monkeypatch,
          pw_rows=[{"po": "PO-1", "status": "已下单", "id": ""}], hx_rows=[])
    ok, failed = G.verifiable({"PO-1"}, set())
    assert ok == set() and failed and "运单号" in failed[0]["why"]


def test_pw_ok_with_track(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch,
          pw_rows=[{"po": "PO-1", "status": "已下单", "id": "2998279"}], hx_rows=[])
    ok, failed = G.verifiable({"PO-1"}, set())
    assert ok == {"PO-1"} and not failed


def test_hx_needs_status_2_or_3_and_track(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, pw_rows=[],
          hx_rows=[{"po": "PO-2", "accounts": [{"status": 2,
                                                "track": "1Z9", "tag": "LM739"}]},
                   {"po": "PO-3", "accounts": [{"status": 2, "track": ""}]},
                   {"po": "PO-4", "accounts": [{"status": 9, "track": "1Z9"}]}])
    ok, failed = G.verifiable(set(), {"PO-2", "PO-3", "PO-4"})
    assert ok == {"PO-2"}
    assert {f["po"] for f in failed} == {"PO-3", "PO-4"}


def test_both_sides_must_pass(monkeypatch, tmp_path):
    """同一单两边都有 ⇒ 两边都成功才算。"""
    _seed(tmp_path, monkeypatch,
          pw_rows=[{"po": "PO-9", "status": "已下单", "id": "1"}],
          hx_rows=[{"po": "PO-9", "accounts": [{"status": 2, "track": ""}]}])
    ok, failed = G.verifiable({"PO-9"}, {"PO-9"})
    assert ok == set() and failed
