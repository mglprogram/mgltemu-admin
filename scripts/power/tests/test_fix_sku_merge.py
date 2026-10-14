# -*- coding: utf-8 -*-
"""「合并完全相同 SKU、数量累加」的离线单测（2026-10-12 用户口径）。

纯离线：只测纯函数（`merge_rows` / `target_rows` / `has_dup_sku` / `expect_items`），
不连帕沃尔、不提交任何东西。

背景：两条规则的主行可能映射到同一 SKU（实证 71/5148 单），例如 BEL
    `202504-10SetB` → 主行 `202504-5SetB` ＋ 新增 `5SetA`
    `202504-10SetG` → 主行 `202504-5SetB` ＋ 新增 `5SetE`
⇒ 同单含这两者时会产出两行相同 SKU，用户要求合并成一行、数量累加。
"""
import os
import sys

# `fix_sku.py` 属于发货技能（不在本 TEMU 工程内）⇒ 显式指到技能目录。
# 允许用 PW_SKILL_ROOT 覆盖（换机/换路径时）。
SKILL = os.environ.get("PW_SKILL_ROOT") or os.path.expanduser(
    r"~\.workbuddy\skills\shipping-bhy-bel")
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import fix_sku as fs  # noqa: E402


def _item(items, changes):
    """造一个最小 item：items=[(sku,qty,name)]，changes=[(idx,new_sku,new_qty,adds)]"""
    return {
        "order": {"order_id": "1", "items": [
            {"sku": s, "qty": q, "name": n} for s, q, n in items]},
        "changes": [
            {"idx": i, "old_sku": items[i][0], "new_sku": ns,
             "new_qty": nq, "add": [{"sku": a, "qty": aq} for a, aq in adds]}
            for (i, ns, nq, adds) in changes],
    }


# --------------------------------------------------------------- merge_rows
def test_merge_rows_sums_same_sku():
    out = fs.merge_rows([("A", "1", 1), ("B", "1", 2), ("A", "1", 3)])
    assert out == [("A", "1", 4), ("B", "1", 2)], out


def test_merge_rows_keeps_order_of_first_occurrence():
    out = fs.merge_rows([("Z", "1", 1), ("A", "1", 1), ("Z", "1", 1)])
    assert [r[0] for r in out] == ["Z", "A"]


def test_merge_rows_no_dup_is_identity():
    src = [("A", "1", 1), ("B", "1", 1)]
    assert fs.merge_rows(src) == src


# --------------------------------------------------------- has_dup_sku
def test_has_dup_true_when_two_rules_map_to_same_main_sku():
    """BEL 实证场景：10SetB 与 10SetG 的主行都落到 5SetB-L。"""
    it = _item(
        items=[("BEL-202504-10SetB-L", 1, "1"), ("BEL-202504-10SetG-L", 1, "1")],
        changes=[(0, "BEL-202504-5SetB-L", 1, [("BEL-202504-5SetA-L", 1)]),
                 (1, "BEL-202504-5SetB-L", 1, [("BEL-202504-5SetE-L", 1)])])
    assert fs.has_dup_sku(it) is True


def test_has_dup_false_when_all_distinct():
    it = _item(
        items=[("BEL-102104-10SetC-M", 1, "1"), ("BEL-102104-10SetC-L", 1, "1")],
        changes=[(0, "BEL-102104-5SetA-M", 1, [("BEL-102104-5SetC-M", 1)]),
                 (1, "BEL-102104-5SetA-L", 1, [("BEL-102104-5SetC-L", 1)])])
    assert fs.has_dup_sku(it) is False


def test_has_dup_detects_main_vs_added_collision():
    """主行与新增行撞车也算重复。"""
    it = _item(items=[("X-10SetC-M", 1, "1")],
               changes=[(0, "X-5SetA-M", 1, [("X-5SetA-M", 1)])])
    assert fs.has_dup_sku(it) is True


# ------------------------------------------------------------ target_rows
def test_target_rows_merges_and_sums():
    it = _item(
        items=[("BEL-202504-10SetB-L", 1, "1"), ("BEL-202504-10SetG-L", 1, "1")],
        changes=[(0, "BEL-202504-5SetB-L", 1, [("BEL-202504-5SetA-L", 1)]),
                 (1, "BEL-202504-5SetB-L", 1, [("BEL-202504-5SetE-L", 1)])])
    rows = fs.target_rows(it, "1")
    assert rows == [("BEL-202504-5SetB-L", "1", 2),
                    ("BEL-202504-5SetA-L", "1", 1),
                    ("BEL-202504-5SetE-L", "1", 1)], rows


def test_target_rows_keeps_untouched_rows_name():
    """没被规则命中的行保持原样（含原产品名称）。"""
    it = _item(items=[("KEEP-M", 1, "原名"), ("X-10SetC-M", 1, "1")],
               changes=[(1, "X-5SetA-M", 1, [])])
    rows = fs.target_rows(it, "1")
    assert rows[0] == ("KEEP-M", "原名", 1), rows
    assert rows[1] == ("X-5SetA-M", "1", 1), rows


def test_target_rows_multi_qty_accumulates():
    """同一目标 SKU 出现三次 → 数量累加为 3。"""
    it = _item(
        items=[("A-10SetB-M", 1, "1"), ("A-10SetG-M", 1, "1"), ("A-10SetH-M", 1, "1")],
        changes=[(0, "A-5SetB-M", 1, []), (1, "A-5SetB-M", 1, []), (2, "A-5SetB-M", 1, [])])
    assert fs.target_rows(it, "1") == [("A-5SetB-M", "1", 3)]


# ------------------------------------------------------------ expect_items
def test_expect_items_is_merged_view():
    """回读校验必须按**合并后**比对，否则行数对不上会报假不一致。"""
    it = _item(
        items=[("BEL-202504-10SetB-L", 1, "1"), ("BEL-202504-10SetG-L", 1, "1")],
        changes=[(0, "BEL-202504-5SetB-L", 1, [("BEL-202504-5SetA-L", 1)]),
                 (1, "BEL-202504-5SetB-L", 1, [("BEL-202504-5SetE-L", 1)])])
    exp = fs.expect_items(it, "1")
    assert exp == [["BEL-202504-5SetB-L", "2", "1"],
                   ["BEL-202504-5SetA-L", "1", "1"],
                   ["BEL-202504-5SetE-L", "1", "1"]], exp


def test_expect_items_unchanged_when_no_dup():
    """无重复时，输出与旧行为完全一致（回归保护）。"""
    it = _item(items=[("BEL-102104-10SetC-M", 1, "1")],
               changes=[(0, "BEL-102104-5SetA-M", 1, [("BEL-102104-5SetC-M", 1)])])
    assert fs.expect_items(it, "1") == [["BEL-102104-5SetA-M", "1", "1"],
                                        ["BEL-102104-5SetC-M", "1", "1"]]
