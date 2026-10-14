# -*- coding: utf-8 -*-
"""「改单多趟收敛」离线单测（2026-10-13 用户口径）。

背景：`match_rule` 命中第一条即返回（单步语义），而一条 SKU 常需多步到终态，例如
    BEL-102104-10SetC1.1-XL  →(C1.1→C)→  BEL-102104-10SetC-XL  →(10SetC)→ 5SetA＋新增5SetC
旧实现每趟跑批只推进一步 ⇒ 要跑两天。`apply_rules()` 把多步收进同一趟。

纯离线：只测纯函数，不连帕沃尔、不提交任何东西。
"""
import os
import sys

SKILL = os.environ.get("PW_SKILL_ROOT") or os.path.expanduser(
    r"~\.workbuddy\skills\shipping-bhy-bel")
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import fix_sku as fs  # noqa: E402

SIZE_RE = r"-([A-Z]{1,3})(?:\(废\))?$"


def rs(*rules):
    """造一个最小 ruleset。rules = (id, match, set, qty[, add_sku, add_qty])

    ⚠ 规则里「新增行」的真实结构是 **`add` 列表** `[{"sku":..., "qty":...}]`
      （见 rules_*.json / `expand_add`），不是数据库列名 add_sku/add_qty。
    """
    out = []
    for r in rules:
        rule = {"id": r[0], "match": r[1], "set": r[2], "qty": r[3]}
        if len(r) > 4 and r[4]:
            rule["add"] = [{"sku": r[4], "qty": r[5] if len(r) > 5 else "x1"}]
        out.append(rule)
    return {"pname": "1", "size_re": SIZE_RE, "rules": out}


# ------------------------------------------------------------- 单步等价（向后兼容）
def test_single_step_identical_to_match_rule():
    R = rs(("r1", "AAA", "BBB", "x2"))
    fin, qty, adds, chain = fs.apply_rules("X-AAA-L", 3, R)
    assert fin == "X-BBB-L", fin
    assert qty == 6, qty                       # x2 × 3
    assert adds == [], adds
    assert chain == ["r1"], chain


def test_no_match_changes_nothing():
    R = rs(("r1", "AAA", "BBB", "x1"))
    fin, qty, adds, chain = fs.apply_rules("X-ZZZ-L", 2, R)
    assert (fin, qty, adds, chain) == ("X-ZZZ-L", 2, [], [])


# ------------------------------------------------------------- 多趟收敛（核心）
def test_two_step_converges_in_one_call():
    """1.1 剥除 → 拆套，两步必须【一次调用】走完。"""
    R = rs(("c11", "C1.1", "C", "x1"),
           ("split", "10SetC", "5SetA", "x1", "{p}5SetC-{size}", "x1"))
    fin, qty, adds, chain = fs.apply_rules("BEL-102104-10SetC1.1-XL", 1, R)
    assert fin == "BEL-102104-5SetA-XL", fin
    assert qty == 1, qty
    assert chain == ["c11", "split"], chain
    # {p} = match 之前的前缀（此处 "BEL-102104-"），{size} = 抽到的尺码
    assert [(a["sku"], a["qty"]) for a in adds] == [("BEL-102104-5SetC-XL", 1)], adds


def test_three_step_chain_converges():
    R = rs(("r1", "AAA", "BBB", "x1"), ("r2", "BBB", "CCC", "x1"),
           ("r3", "CCC", "DDD", "x1"))
    fin, qty, adds, chain = fs.apply_rules("AAA-1", 1, R)
    assert fin == "DDD-1", fin
    assert chain == ["r1", "r2", "r3"], chain


def test_multipliers_chain():
    """`xN` 是倍率 ⇒ 多趟串联（4件装→2件装x2，再→1件装x2 ⇒ 原数量×4）。"""
    R = rs(("s1", "4B", "2B", "x2"), ("s2", "2B", "1B", "x2"))
    fin, qty, adds, chain = fs.apply_rules("X-4B-L", 1, R)
    assert (fin, qty) == ("X-1B-L", 4), (fin, qty)


def test_add_rows_accumulate_across_passes():
    R = rs(("a", "P10", "P5", "x1", "{p}E5-{size}", "x1"),
           ("b", "P5", "P2", "x1", "{p}E2-{size}", "x1"))
    fin, qty, adds, chain = fs.apply_rules("P10-L", 1, R)
    assert fin == "P2-L", fin
    assert [(a["sku"], a["qty"]) for a in adds] == [("E5-L", 1), ("E2-L", 1)], adds


# ------------------------------------------------------------- 防环 / 上限
def test_cycle_is_detected_and_stops():
    """A→B→A 必须终止，不能无限循环。"""
    R = rs(("c1", "AAA", "BBB", "x1"), ("c2", "BBB", "AAA", "x1"))
    fin, qty, adds, chain = fs.apply_rules("AAA-1", 1, R)
    assert chain == ["c1"], chain             # 只走一步就停
    assert fin == "BBB-1", fin


def test_self_mapping_stops():
    """规则把 X 映射成它自己（无进展）⇒ 立即停。"""
    R = rs(("s", "AAA", "AAA", "x1"))
    fin, qty, adds, chain = fs.apply_rules("AAA-1", 1, R)
    assert (fin, chain) == ("AAA-1", []), (fin, chain)


def test_max_passes_cap():
    """超长链必须被 max_passes 截断（不会跑飞）。"""
    R = {"pname": "1", "size_re": SIZE_RE, "rules": [
        {"id": "m%d" % i, "match": "K%02d" % i, "set": "K%02d" % (i + 1),
         "qty": "x1", "add_sku": None, "add_qty": None} for i in range(20)]}
    fin, qty, adds, chain = fs.apply_rules("K00-1", 1, R, max_passes=5)
    assert len(chain) == 5, chain
    assert fin == "K05-1", fin


# ------------------------------------------------------------- build_plan 集成
def test_build_plan_uses_multipass():
    """build_plan 产出的 change 应是**终态**，且 rule 字段带完整链。"""
    R = rs(("c11", "C1.1", "C", "x1"), ("split", "10SetC", "5SetA", "x1"))
    order = {"order_id": "1", "deliver_no": "PO-T", "status": "下单失败",
             "can_edit": True,
             "items": [{"sku": "BEL-102104-10SetC1.1-M", "qty": 1, "name": "1"}]}
    plan = fs.build_plan([order], R)
    assert len(plan) == 1, plan
    c = plan[0]["changes"][0]
    assert c["new_sku"] == "BEL-102104-5SetA-M", c
    assert c["old_sku"] == "BEL-102104-10SetC1.1-M", c
    assert c["rule"] == "c11 + split", c


def test_build_plan_skips_unmatched():
    R = rs(("r1", "AAA", "BBB", "x1"))
    order = {"order_id": "1", "deliver_no": "PO-T", "status": "下单失败",
             "can_edit": True, "items": [{"sku": "ZZZ-M", "qty": 1, "name": "1"}]}
    assert fs.build_plan([order], R) == []


# ------------------------------------------------- converge_row：新增行也要收敛
def test_converge_row_processes_add_rows():
    """★ 2026-10-13 核心：**新增行也必须过规则**，否则要跑两天才收敛。

    真实场景 BEL：`202504-10SetB` → 主行 `5SetB` ＋ 新增 `5SetA`；
    而库里又有 `202504-5SetA → 202504-5SetB` ⇒ 新增行应收敛成 `5SetB`。
    """
    R = rs(("b", "202504-10SetB", "202504-5SetB", "x1", "{p}202504-5SetA-{size}", "x1"),
           ("a", "202504-5SetA", "202504-5SetB", "x1"))
    fin, qty, adds, chain = fs.converge_row("BEL-202504-10SetB-XXL", 1, R)
    assert fin == "BEL-202504-5SetB-XXL", fin
    assert [(a["sku"], a["qty"]) for a in adds] == [("BEL-202504-5SetB-XXL", 1)], adds
    assert chain == ["b", "a"], chain


def test_converge_row_duplicate_triggers_merge():
    """新增行收敛后与主行**同名** ⇒ 交给 merge_rows 合并成一行、数量累加。"""
    R = rs(("b", "202504-10SetB", "202504-5SetB", "x1", "{p}202504-5SetA-{size}", "x1"),
           ("a", "202504-5SetA", "202504-5SetB", "x1"))
    sku = "BEL-202504-10SetB-XXL"
    fin, qty, adds, chain = fs.converge_row(sku, 1, R)
    order = {"order_id": "1", "deliver_no": "PO-T", "status": "下单失败", "can_edit": True,
             "items": [{"sku": sku, "qty": 1, "name": "1"}]}
    item = {"order": order,
            "changes": [{"idx": 0, "old_sku": sku, "new_sku": fin,
                         "new_qty": qty, "add": adds}]}
    assert fs.has_dup_sku(item, "1") is True
    assert fs.target_rows(item, "1") == [("BEL-202504-5SetB-XXL", "1", 2)]


def test_converge_row_leaves_unrelated_add_alone():
    """新增行若不再命中任何规则，保持原样（不能凭空改）。"""
    R = rs(("b", "202504-10SetB", "202504-5SetB", "x1", "{p}202504-5SetE-{size}", "x1"),
           ("a", "202504-5SetA", "202504-5SetB", "x1"))
    fin, qty, adds, chain = fs.converge_row("BEL-202504-10SetB-L", 1, R)
    assert fin == "BEL-202504-5SetB-L", fin
    assert [(a["sku"], a["qty"]) for a in adds] == [("BEL-202504-5SetE-L", 1)], adds
    assert chain == ["b"], chain


def test_converge_row_no_match_is_identity():
    R = rs(("r1", "AAA", "BBB", "x1"))
    assert fs.converge_row("ZZZ-L", 3, R) == ("ZZZ-L", 3, [], [])


def test_converge_row_max_adds_guard():
    """规则互相追加时靠 max_adds 兜底，不会爆炸。"""
    R = {"pname": "1", "size_re": SIZE_RE, "rules": [
        {"id": "g%d" % i, "match": "G%d" % i, "set": "H%d" % i, "qty": "x1",
         "add": [{"sku": "G%d" % (i + 1), "qty": "x1"}]} for i in range(50)]}
    fin, qty, adds, chain = fs.converge_row("G0-L", 1, R, max_adds=5)
    assert len(adds) <= 6, len(adds)          # 有界
