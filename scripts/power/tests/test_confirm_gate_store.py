# -*- coding: utf-8 -*-
"""test_confirm_gate_store.py — 闸门必须**按店铺**读产物，不能跨店串。

★ 2026-11-25 真机事故：
    BEL 跑第 18 步，闸门把 5 单全拦（「领星 OMS 未受理」）；
    但单查 BEL 的 OMS 明明有 3 单「仓库处理中」。
    根因：`_latest("*","**","*_hx_oms_status.json")` 按字典序取到了
         `kinglamansport_hx_oms_status.json` ⇒ 拿 KLS 的文件查 BEL 的单，全 miss。

本测试：两个店各有状态文件，`verifiable(pos, store=...)` 只能读**自己那店**的。
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
if _POWER not in sys.path:
    sys.path.insert(0, _POWER)

import confirm_gate as G  # noqa: E402


def _seed(root):
    day = os.path.join(root, "2026-11-25", "fulfill")
    # BEL：PO-A 仓库处理中且有运单号 ⇒ 应通过
    os.makedirs(os.path.join(day, "BEL"), exist_ok=True)
    json.dump({"rows": [{"po": "PO-A", "accounts": [
        {"tag": "LM739", "status": 2, "statusName": "仓库处理中", "track": "1Z-A"}]}]},
        open(os.path.join(day, "bel_hx_oms_status.json"), "w", encoding="utf-8"),
        ensure_ascii=False)
    # KLS：PO-B 同样是「处理中」，但**不应该**被 BEL 读到
    os.makedirs(os.path.join(day, "KinglamanSport"), exist_ok=True)
    json.dump({"rows": [{"po": "PO-B", "accounts": [
        {"tag": "LM739", "status": 2, "track": "1Z-B"}]}]},
        open(os.path.join(day, "kinglamansport_hx_oms_status.json"), "w", encoding="utf-8"),
        ensure_ascii=False)
    # 字典序上 kinglamansport 排在 bel 之后 ⇒ 老实现会选它
    return root


def test_gate_reads_own_store_file(monkeypatch, tmp_path):
    root = _seed(str(tmp_path))
    monkeypatch.setattr(G, "_ROOT", root, raising=True)

    ok, failed = G.verifiable(set(), {"PO-A"}, store="BEL")
    assert ok == {"PO-A"}, "BEL 的 PO-A 应通过（读 bel_ 文件）"
    assert not failed

    ok2, failed2 = G.verifiable(set(), {"PO-B"}, store="BEL")
    assert ok2 == set(), "BEL 不该读到 KLS 的文件 ⇒ PO-B 必须被拦"
    assert failed2 and failed2[0]["po"] == "PO-B"


def test_gate_kls_reads_own(monkeypatch, tmp_path):
    root = _seed(str(tmp_path))
    monkeypatch.setattr(G, "_ROOT", root, raising=True)
    ok, _ = G.verifiable(set(), {"PO-B"}, store="KinglamanSport")
    assert ok == {"PO-B"}
