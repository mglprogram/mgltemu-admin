# -*- coding: utf-8 -*-
"""「一店一子代理，主会话只监控与修」这条铁律的守门测试（用户 2026-11-09 定）。

用户原话：「**BHY和BEL都拿子代理去跑，这个主会话用于监控和修复**」。

为什么要写测试：这条规则以前只活在 SKILL.md 正文里，靠"我记得"。
2026-11-06 定的「帕沃尔段派子代理」已经因为提示只在 `delegated and confirm` 时才打印、
且名单里没有恒信段，差点被理解成「只有帕沃尔要外派」。
现在把两件事钉成机器可判的：

1. SKILL.md 里**必须**有这条铁律、必须写明「一个店一个子代理」「两店可并行」「主会话只监控」；
2. `run_temu_fulfill.py` 真跑时**必须**按店打印「整店交给它自己的子代理」；
3. 提示里必须同时出现两店名字的规则说明（不能只写 BHY 或只写 BEL）。

纯离线：只读源码与文档，不连浏览器。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import run_temu_fulfill as F  # noqa: E402

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def _skill_path():
    """SKILL.md 在 ~/.agents/skills 下（不在仓库里），取不到就跳过。"""
    home = os.path.expanduser("~")
    return os.path.join(home, ".agents", "skills", "temu-fulfill", "SKILL.md")


# ─────────────────────────────────── ① SKILL.md ─────────────────────────────────
def test_skill_declares_one_subagent_per_store():
    p = _skill_path()
    if not os.path.exists(p):
        import pytest
        pytest.skip("SKILL.md 不在本机")
    s = _read(p)
    assert "BHY和BEL都拿子代理去跑" in s, "必须原文记着用户这句口径"
    assert "一个店 = 一个子代理" in s or "一个店一个子代理" in s
    assert "主会话" in s and "监控" in s and "修复" in s
    assert "两店**可以并行**" in s or "两店可以并行" in s


def test_skill_forbids_cross_store_operation():
    p = _skill_path()
    if not os.path.exists(p):
        import pytest
        pytest.skip("SKILL.md 不在本机")
    s = _read(p)
    assert "不许" in s and ("碰 BEL" in s or "跨店" in s), "必须写明子代理不许跨店"


def test_skill_keeps_same_store_internal_serial():
    """同店内部仍按 pipeline 串行 —— 别为抢时间把同店步骤并发。"""
    p = _skill_path()
    if not os.path.exists(p):
        import pytest
        pytest.skip("SKILL.md 不在本机")
    s = _read(p)
    assert "同店内部" in s and "串行" in s


def test_skill_old_pw_rule_is_nested_not_replaced():
    """11-06 的帕沃尔铁律不能被 11-09 那条"覆盖掉"，要写明是被包住。"""
    p = _skill_path()
    if not os.path.exists(p):
        import pytest
        pytest.skip("SKILL.md 不在本机")
    s = _read(p)
    assert "帕沃尔仓那份**必须派子代理**跑" in s
    assert "包住" in s


# ─────────────────────────────────── ② 入口提示 ─────────────────────────────────
def test_entry_brief_mentions_both_stores_and_parallel():
    """分派清单必须点名两店并行 + 一店一子代理，否则子代理会被串着派。"""
    brief = "\n".join(getattr(F, "DELEGATE_BRIEF", ()) or ())
    assert "一店一子代理" in brief, "分派清单里要写明粒度"
    assert "BHY" in brief and "BEL" in brief
    assert "并行" in brief


def test_entry_brief_first_item_is_store_and_window():
    """第一条必须是「店名 + 窗口名、禁跨店」—— 这是最容易被漏、漏了就白跑的那条。"""
    brief = list(getattr(F, "DELEGATE_BRIEF", ()) or ())
    assert brief, "DELEGATE_BRIEF 不能为空"
    assert "店名" in brief[0] and "窗口" in brief[0] and "跨店" in brief[0]


def test_entry_hint_prints_even_without_pw_steps():
    """★ 关键回归：原来只在 `delegated and confirm` 才打印 ⇒ 纯恒信批根本不提示外派。

    2026-11-09 改成「只要真跑就提示整店外派」。这里用源码判据钉住（该段是 IO，不便于单跑）。
    """
    src = _read(os.path.join(_HERE, "run_temu_fulfill.py"))
    assert "if delegated and a.confirm:" not in src, \
        "旧的打印条件（只在有帕沃尔步骤时提示）必须改掉"
    assert "整店应交给它自己的子代理跑" in src
    # 提示要挂在 `if a.confirm:` 下，不再依赖 delegated 是否为空
    i = src.find("if a.confirm:")
    j = src.find("整店应交给它自己的子代理跑")
    assert 0 < i < j, "打印条件必须是真跑，而不是「有帕沃尔步骤」"


def test_entry_delegate_steps_still_declared():
    """帕沃尔段名单不能被这条新规则顺手删掉（同店内部还要按它并行）。"""
    assert set(F.DELEGATE_STEPS) >= {"label_pw", "pw_receive", "pw_track"}
