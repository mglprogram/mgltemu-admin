"""替发/拆解规则库（config/temu_stock_rules.json）的单元测试 —— 不联网、不碰浏览器。

钉住的口径（用户 2026-11-18 口述）：
  ① 显式款号组：组内双向互顶（2038-4SetA ↔ 2038-4SetB）
  ② `X-所有变体`：该款号下**全部套系**互通（102105-3BHL ↔ 102105-3BGR）
  ③ 跨款号组：2032 / 204203 / 207802-4SetA 三者所有变体都能互顶
  ④ 规则里分两行写的组要自动并起来（304002-3B ↔ 3040A-3B ↔ T3038-3B）
  ⑤ 只在「本款吃紧（可用<=0 或 可售<=3 天）」时才借；取组内最大、不相加
  ⑥ 拆解：102303-6SetA = 102302-3BHW + 5SetA（或 5SetB），也可 3BHW×2
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import substitute_rules as S  # noqa: E402


@pytest.fixture(scope="module")
def rules():
    return S.Rules(ROOT / "config" / "temu_stock_rules.json")


def test_style_of_strips_store_prefix():
    assert S.style_of("BEL-202504-5SetA") == "202504-5SetA"
    assert S.style_of("BK-2032-4SetA") == "2032-4SetA"
    assert S.style_of("BLA-E2032-8SetB") == "E2032-8SetB"
    assert S.style_of("202504-5SetA") == "202504-5SetA"


def test_explicit_pair(rules):
    assert rules.are_substitutable("2038-4SetA", "2038-4SetB")
    assert rules.are_substitutable("BEL-2038-4SetB", "2038-4SetA")   # 带前缀也认
    assert not rules.are_substitutable("2038-4SetA", "2038-4SetC")   # 显式组只限这两款


def test_family_all_variants(rules):
    # 102105《-》102105-所有变体
    assert rules.are_substitutable("102105-3BHL", "102105-3BGR")
    assert rules.are_substitutable("2033-4SetA", "2033-9SetB")
    assert not rules.are_substitutable("102105-3BHL", "102104-5SetA")


def test_cross_number_group(rules):
    # 204203<->2032-所有变体<->204203-所有变体<->207802-4SetA
    assert rules.are_substitutable("2032-4SetB", "204203-4SetC")
    assert rules.are_substitutable("207802-4SetA", "2032-4SetA")
    assert rules.are_substitutable("204207-4SetA", "204002-4SetB")   # 204207《-》204002-所有变体
    assert not rules.are_substitutable("2032-4SetA", "2033-4SetA")


def test_groups_merge_across_lines(rules):
    # 两条规则拼成的链：304002-3B ↔ 3040A-3B ↔ T3038-3B
    assert rules.are_substitutable("304002-3B", "T3038-3B")
    assert rules.are_substitutable("3040A-3B", "304002-3B")
    # 1B 侧单独一组，1B 与 3B 不互通
    assert rules.are_substitutable("304002-1B", "3037-1B")
    assert not rules.are_substitutable("304002-1B", "304002-3B")


def test_1b_and_3b_never_cross(rules):
    """用户 2026-11-18：「3B 对应用 3B 替」⇒ 1B 组与 3B 组不许串。"""
    g1b = {m for m in ("304002-1B", "3040A-1B", "T3038-1B", "3037-1B")}
    g3b = {m for m in ("304002-3B", "3040A-3B", "T3038-3B")}
    for a in g1b:
        for b in g3b:
            assert not rules.are_substitutable(a, b), (a, b)
    assert rules.are_substitutable("3040A-1B", "T3038-1B")
    assert rules.are_substitutable("3040A-3B", "T3038-3B")
    assert not rules.are_substitutable("304002-3B", "3037-1B")   # 3037 只有 1B 侧


def test_best_takes_max_not_sum(rules):
    stock = {("202504-5SetA", "L"): 0.0, ("202504-5SetB", "L"): 88.0,
             ("202504-5SetE", "L"): 40.0}
    qty, why = rules.best("202504-5SetA", "L", lambda s, z: stock.get((s, z), 0.0),
                          own=0.0, daily=10.0)
    assert qty == 88.0            # 取最大 88，不是 88+40
    assert "5SetB" in why


def test_best_skips_when_own_stock_is_enough(rules):
    stock = {("2038-4SetA", "M"): 90.0, ("2038-4SetB", "M"): 500.0}
    qty, why = rules.best("2038-4SetA", "M", lambda s, z: stock.get((s, z), 0.0),
                          own=90.0, daily=10.0)     # 90/10 = 9 天 > 3 天
    assert (qty, why) == (0.0, "")


def test_best_triggers_when_tight(rules):
    stock = {("2038-4SetA", "M"): 10.0, ("2038-4SetB", "M"): 500.0}
    qty, why = rules.best("2038-4SetA", "M", lambda s, z: stock.get((s, z), 0.0),
                          own=10.0, daily=10.0)     # 1 天 <= 3 天 → 借
    assert qty == 500.0 and "2038-4SetB" in why


def test_recipe_assemble(rules):
    stock = {("102302-3BHW", "L"): 4.0, ("102302-5SetA", "L"): 2.0,
             ("102302-5SetB", "L"): 9.0}
    qty, why = rules.assemble("102303-6SetA", "L", lambda s, z: stock.get((s, z), 0.0))
    # 三种拆法：3BHW+5SetA=min(4,2)=2 / 3BHW+5SetB=min(4,9)=4 / 3BHW×2=2 ⇒ 取最好 4
    assert qty == 4.0 and "拆解" in why


def test_recipe_unknown_style_is_zero(rules):
    assert rules.assemble("209999-1B", "L", lambda s, z: 99.0) == (0.0, "")


def test_config_has_no_structural_error(rules):
    msgs = rules.validate(catalog=None)
    assert not [m for m in msgs if "只有 1 个成员" in m], msgs
    assert len(rules.groups) >= 15
    assert {r["target"] for r in rules.recipes} >= {"102303-6SetA", "102302-5SetA"}


def test_combo_kits_20261119(rules):
    """用户 2026-11-19 给的组合套：目标款 = 组件同尺码库存 ÷ 用量 取整。"""
    stock = {"H103902-2B": 40, "N204003-2B": 30, "204203-4SetA": 100,
             "204207-4SetA": 100, "102104-5SetC": 9, "102104-5SetA": 4,
             "102503-5SetA": 7, "102503-5SetB": 9, "102503-5SetC": 5}
    fn = lambda s, z: stock.get(s, 0)          # noqa: E731
    cases = {
        "H103902-4B": 20,      # 2B×2
        "H103902-6B": 13,      # 2B×3
        "N204003-4B": 15,      # 2B×2
        "N204003-6B": 10,      # 2B×3
        "204203-8SetA": 50,    # 4SetA×2
        "204207-8SetA": 50,
        "102104-10SetC1.1": 4,  # 5SetC + 5SetA（取小）
        "102503-10SetA": 7,    # 5SetA + 5SetB
        "102503-10SetB": 3,    # 5SetA×2
        "102503-10SetC": 4,    # 5SetB×2
        "102503-10SetD": 2,    # 5SetC×2
        "102503-10SetE": 5,    # 5SetA + 5SetC
        "102503-10SetF": 5,    # 5SetB + 5SetC
    }
    for target, want in cases.items():
        got, why = rules.assemble(target, "L", fn)
        assert got == want, (target, got, want, why)
        assert why.startswith("拆解")
