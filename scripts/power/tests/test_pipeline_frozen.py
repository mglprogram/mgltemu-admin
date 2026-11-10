# -*- coding: utf-8 -*-
"""test_pipeline_frozen.py — 发货 pipeline **固化**的守门测试（纯离线）。

固化的是**顺序**，不是代码：`data/temu_fulfill_pipeline.json → steps` 是唯一权威，
代码里只放「怎么跑」，不许再有第二份顺序。这个文件守四件事：

  1. **没有硬编码顺序**：源码里不许出现 `["refresh_stock", "split", ...]` 这种步骤名序列
     （改了 json 却还有一处代码顺序 = 又回到"改了没用"的老路）。
  2. **json ↔ 代码一致**：每个步骤名都有实现；`delegate: true` 必须等于 `DELEGATE_STEPS`；
     写/软失败的判定必须落在 `WRITE_STEPS` / `SOFT_STEPS` 里。
  3. **顺序本身有约束**：库存先于拆仓、下单代发先于修失败单、修完才确认订单、确认完才取号、
     确认发货是最后一步、report 收尾。
  4. **每个步骤都得写清「做什么」**（`_做什么` 非空），免得下次只剩个名字看不懂。
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
sys.path.insert(0, _POWER)

import run_temu_fulfill as F  # noqa: E402

PIPE = os.path.join(_POWER, "data", "temu_fulfill_pipeline.json")

#: 固化时的步骤（改 json 必须同步改这里 —— 这是"知道自己在改流程"的显式签名）
FROZEN = [
    "refresh_stock", "export_orders", "split",
    # ★ 2026-11-10 新增（用户令）：split 之后给 SHEIN 店铺（BS/BD）按拆仓买面单
    "label_shein",
    "label_pw", "label_hx",
    # ★ 2026-11-09 补：2026-11-08 用户令新增（下单代发前必须「更新订单」，
    #   否则复制订单找不到订单）。当时只改了 json、没同步这里，两条测试因此长期红。
    "pw_update_orders",
    "hwc_order", "pw_fail_scan", "pw_fail_fix", "pw_receive", "pw_track",
    "hx_ship", "capture_pw", "hx_oms_check", "temu_confirm", "report", "notify",
]


def _pipeline() -> dict:
    if not os.path.exists(PIPE):
        import pytest  # noqa: PLC0415
        pytest.skip("本机没有 pipeline json（data/ 被 gitignore）")
    return json.load(open(PIPE, encoding="utf-8"))


def _names() -> list:
    return [str(s.get("name")) for s in _pipeline()["steps"]]


# ---------------------------------------------------------------- 1. 顺序只有一份


def test_frozen_order_matches_json():
    assert _names() == FROZEN, "改流程必须同时改 FROZEN 与 json，别只改一边"


def test_no_hardcoded_step_order_in_code():
    """★ 顺序只能来自 json。源码里若再写一串步骤名（列表/元组），就等于有两份真相。

    ⚠ 只看**列表字面量** `[...]` 后面跟 ≥3 个步骤名的写法（列表天然是"按顺序"的语义）；
      `WRITE_STEPS = (...)` / `DELEGATE_STEPS = (...)` 是**集合语义**（顺序无意义），
      它们在别的测试里逐项核对，不算"第二份顺序"。
    """
    offenders = []
    for fn in os.listdir(_POWER):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(_POWER, fn), encoding="utf-8").read()
        hits = re.findall(
            r"\[\s*(?:[\"'](?:%s)[\"']\s*,\s*){3,}" % "|".join(FROZEN), src)
        if hits:
            offenders.append((fn, hits[0][:80]))
    assert not offenders, ("代码里出现了步骤名序列，顺序必须只放在 json：%s" % offenders)


# ---------------------------------------------------------------- 2. json ↔ 代码一致


def test_every_step_has_impl_and_impl_has_no_ghost():
    names = set(_names())
    assert set(F.STEPS) == names, "STEPS 实现与 json 步骤必须一一对应（多一个少一个都不行）"


def test_delegate_flag_matches_code():
    """json 的 `delegate: true` 必须与 `DELEGATE_STEPS` 完全一致（两边漂移就没人派子代理了）。"""
    flagged = {str(s.get("name")) for s in _pipeline()["steps"] if s.get("delegate")}
    assert flagged == set(F.DELEGATE_STEPS), (flagged, F.DELEGATE_STEPS)


def test_write_and_soft_sets_are_subset_and_disjoint():
    names = set(_names())
    assert set(F.WRITE_STEPS) <= names, "WRITE_STEPS 里有不存在的步骤"
    assert set(F.SOFT_STEPS) <= names, "SOFT_STEPS 里有不存在的步骤"
    assert not (set(F.WRITE_STEPS) & {"refresh_stock", "split", "pw_fail_scan"})


def test_default_is_soft_only_structural_steps_are_hard():
    """★ 2026-11-07 用户口径：「最重要就是你的 pipeline 规定的硬失败改成软失败」。

    守三件事：
      ① 硬失败清单只有 `refresh_stock` / `split`（没它们后面就没有输入）；
      ② 其余**每一个**步骤，`step_is_soft` 都必须返回 True（默认软，加新步骤不用改代码）；
      ③ `SOFT_STEPS` 不许和 `STRUCTURAL_STEPS` 重叠（两份口径打架＝又回到"改一处没用"）。
    """
    assert tuple(F.STRUCTURAL_STEPS) == ("refresh_stock", "split"), \
        "硬失败只许是结构性步骤，其余一律软失败"
    assert not (set(F.SOFT_STEPS) & set(F.STRUCTURAL_STEPS))
    for n in _names():
        assert F.step_is_soft(n) == (n not in F.STRUCTURAL_STEPS), n


def test_soft_failure_does_not_stop_the_chain():
    """软失败要让链条继续跑，且**不许**把自己的失败伪装成 `stopped_at`。"""
    runs = [
        {"name": "label_hx", "status": "fail", "soft": True, "why": "no-option"},
        {"name": "pw_track", "status": "ok"},
        {"name": "temu_confirm", "status": "ok"},
    ]
    s = F.summarize(runs)
    assert s["soft_failed"] == ["label_hx"]
    assert s["hard_failed"] == [] and s["stopped_at"] == ""
    assert s["ok"] is True, "软失败不该让整链判 ok=False（链条确实跑完了）"
    assert "temu_confirm" in s["ran"], "软失败后面的步骤必须照跑"


def test_hard_failure_still_stops():
    runs = [{"name": "split", "status": "fail", "soft": False},
            {"name": "label_pw", "status": "ok"}]
    s = F.summarize(runs)
    assert s["ok"] is False and s["stopped_at"] == "split"
    assert s["hard_failed"] == ["split"] and s["soft_failed"] == []


def test_gate_list_covers_all_platform_mutating_steps():
    """改平台状态的步骤必须都在 WRITE_STEPS 里，否则没 --confirm 也会真写。

    ⚠ `export_orders` **故意不在** WRITE_STEPS 里：它只是生成一份导出任务、
      重复跑不改变任何订单状态（幂等），而且拆仓没有它就没有输入 ——
      把它卡在 --confirm 后面会让「只看数据」这种用法跑不出表。
    """
    for n in ("hwc_order", "pw_fail_fix", "pw_receive", "pw_track", "hx_ship",
              "label_pw", "label_hx", "temu_confirm"):
        assert n in F.WRITE_STEPS, "%s 会改平台状态，必须进 WRITE_STEPS" % n
    assert "export_orders" not in F.WRITE_STEPS, "导表幂等，不该被 --confirm 挡住"


def test_export_orders_sits_between_stock_and_split():
    """★ 2026-11-07：导表是拆仓的输入 ⇒ 必须在 split 之前、库存刷新之后。"""
    n = _names()
    assert n.index("refresh_stock") < n.index("export_orders") < n.index("split")
    for step in ("label_pw", "label_hx", "hwc_order", "temu_confirm", "report"):
        assert n.index("export_orders") < n.index(step), step


# ---------------------------------------------------------------- 3. 顺序的业务约束


def test_inventory_before_split():
    n = _names()
    assert n.index("refresh_stock") < n.index("split")


def test_labels_before_any_powerwarehouse_action():
    """两个仓的面单都必须在动仓库之前买好。"""
    n = _names()
    for label in ("label_pw", "label_hx"):
        for step in ("hwc_order", "pw_fail_scan", "pw_fail_fix", "pw_receive", "pw_track"):
            assert n.index(label) < n.index(step)


def test_hwc_order_is_the_first_powerwarehouse_write():
    """★ 2026-11-07 用户纠正：先 hwc_shop 下单代发，才谈得上失败单。"""
    n = _names()
    i = n.index("hwc_order")
    assert i < n.index("pw_fail_scan") < n.index("pw_fail_fix")
    assert i < n.index("pw_receive") < n.index("pw_track")


def test_confirm_orders_after_fail_fix_and_before_track():
    n = _names()
    assert n.index("pw_fail_fix") < n.index("pw_receive") < n.index("pw_track")


def test_temu_confirm_is_followed_by_report_then_notify():
    """★ 顺序口径（2026-11-07 更新）：

        确认发货 → 本店出表（report，每店一次）→ 整批收尾推送（notify，最后一步）

    `temu_confirm` 仍然是「发货这件事的最后一步」（用户 2026-11-06 原话），
    但它后面还有两个**只读/外部**步骤：出表、推群+云盘。
    用户 2026-11-07：「这个 4 点的发货任务最后完成后，推送给我飞书的 temu 群
    和云盘把两个表格」⇒ 推送必须是整链最后一步。
    """
    n = _names()
    assert n[-2:] == ["report", "notify"]
    for step in n[:-2]:
        assert n.index(step) <= n.index("temu_confirm"), step
    assert n.index("temu_confirm") == len(n) - 3, "确认发货后面只能剩「出表 + 推送」"


def test_hx_ship_and_checks_before_temu_confirm():
    n = _names()
    for step in ("hx_ship", "capture_pw", "hx_oms_check"):
        assert n.index(step) < n.index("temu_confirm"), step


# ---------------------------------------------------------------- 4. 说明书不能空


def test_every_step_documents_what_it_does():
    for s in _pipeline()["steps"]:
        assert str(s.get("_做什么") or "").strip(), "步骤 %s 缺 `_做什么`" % s.get("name")


def test_top_level_notes_exist():
    d = _pipeline()
    for k in ("_版本", "_跑法", "_写操作闸门", "_一段话流程（给人看）", "_两个「下单」别混淆"):
        assert str(d.get(k) or "").strip(), "缺顶层说明 %s" % k


# ---------------------------------------------------------------- 5. 步骤禁用（enabled=false）


def _disabled() -> set:
    return {str(s.get("name")) for s in _pipeline()["steps"]
            if s.get("enabled", True) is False}


def test_disabled_steps_are_known_and_skipped():
    """★ 2026-11-10 用户令：「发货的 pipeline 的 TEMU 买面单已经被禁用」。

    守三件事：
      ① 只许禁用已知步骤（写错名字等于没禁）；
      ② 整链 `select_steps` 里不许出现禁用步骤；
      ③ `--only` 点名禁用步骤必须抛错 —— 禁用就是一条路都不给走。
    """
    off = _disabled()
    assert off <= set(FROZEN), "禁用了不存在的步骤：%s" % (off - set(FROZEN))
    picked = {str(s.get("name")) for s in F.select_steps(_pipeline()["steps"])}
    assert not (picked & off), "整链仍在跑被禁用的步骤：%s" % (picked & off)
    for n in off:
        try:
            F.select_steps(_pipeline()["steps"], only=n)
        except ValueError:
            continue
        raise AssertionError("--only %s 必须被拒绝（该步骤 enabled=false）" % n)


def test_temu_buylabel_steps_are_disabled():
    """当前禁用清单（用户 2026-11-10 令）：
      · TEMU 买面单：label_pw / label_hx（BHY/BEL 不再买面单）
      · 帕沃尔确认取号：pw_receive（待确认页确认订单）/ pw_track（批量获取单号）
      · TEMU 批量确认发货：temu_confirm
    恢复任一步时同步改本断言。"""
    assert _disabled() == {"label_pw", "label_hx", "pw_receive", "pw_track",
                           "hx_ship", "temu_confirm"}, _disabled()
