# -*- coding: utf-8 -*-
"""pipeline 的门控判定：`classify_verify_row()`（唯一判定点）。

用户口径变更（2026-11-02）：
  旧：「非待处理 → 就点」
  新：「**只有仓库处理中/已出库才点**，异常单不点、单独列表给我」

为什么要改：「非待处理」里混着 `异常（status=5）`。异常单要人工处理
（地址 / SKU / 物流问题），点了确认也不会出库。实测 2026-11-02 当天
22 条异常里有 4 单被误点（PO-211-00972341412471597 等）。

运行: python -m pytest C:\\Users\\saing\\.workbuddy\\skills\\temu-pending-confirm\\tests -q
（本文件也可被 D:\\AI-workplace\\TEMU 的 pytest 直接收集）
"""
import importlib.util
import os
import sys

import pytest

PIPE = r"C:\Users\saing\.workbuddy\skills\temu-pending-confirm\pipeline.py"


def _load():
    spec = importlib.util.spec_from_file_location("pending_pipeline", PIPE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pending_pipeline"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pl():
    return _load()


def _row(*accs, po="PO-1"):
    return {"po": po, "accounts": list(accs)}


def _acc(tag, status, name):
    return {"tag": tag, "status": status, "statusName": name}


# ---------------------------------------------------------------- 正常可点
def test_warehouse_processing_is_ok(pl):
    """仓库处理中（2）⇒ 可以点。这是绝大多数单的状态。"""
    assert pl.classify_verify_row(_row(_acc("LM739", 2, "仓库处理中"))) == "ok"


def test_shipped_is_ok(pl):
    """已出库（3）⇒ 可以点（其实已经出库了，点了也无害，属幂等）。"""
    assert pl.classify_verify_row(_row(_acc("14450", 3, "已出库"))) == "ok"


# ---------------------------------------------------------------- 不点
def test_pending_is_not_ok(pl):
    """待处理（1）⇒ 不点，列成表给人去发货。"""
    assert pl.classify_verify_row(_row(_acc("LM739", 1, "待处理"))) == "pend"


def test_abnormal_is_not_ok(pl):
    """**异常（5）⇒ 不点**（2026-11-02 修订的核心）。"""
    assert pl.classify_verify_row(_row(_acc("LM739", 5, "异常"))) == "abnormal"


def test_abnormal_wins_over_ok_when_two_accounts_disagree(pl):
    """两个账号都有该单且结论不一致 ⇒ 取**更保守**的那个（异常优先于可点）。

    实测当天有 4 单两个账号都出现。若一边说"仓库处理中"、另一边说"异常"，
    点下去就是把异常单确认掉了 —— 必须按异常处理。
    """
    r = _row(_acc("LM739", 2, "仓库处理中"), _acc("14450", 5, "异常"))
    assert pl.classify_verify_row(r) == "abnormal"
    r2 = _row(_acc("LM739", 2, "仓库处理中"), _acc("14450", 1, "待处理"))
    assert pl.classify_verify_row(r2) == "pend", "待处理比异常更靠前（两种都不点，但报告归类要准）"


def test_unfound_is_not_ok(pl):
    """两个账号都没找到 ⇒ 结论不可下，不点。"""
    assert pl.classify_verify_row(_row()) == "unfound"


def test_status_name_text_is_respected_when_code_missing(pl):
    """状态号缺失（0/None）但文本写着「待处理」⇒ 仍按待处理处理（别信 0）。"""
    assert pl.classify_verify_row(_row(_acc("LM739", 0, "待处理"))) == "pend"


# ---------------------------------------------------------------- 草稿（2026-11-03 实测）
def test_draft_status_is_not_ok(pl):
    """`status=0`（**草稿**）不能算可发。

    实测 2026-11-03：BEL 在平台点完「确认发货」后，领星那几条**立刻**出现
    `status=0 / 草稿`，几分钟后才转成 2「仓库处理中」。
    旧实现是黑名单（只排掉 1 和 5）⇒ 0 掉进 ok ⇒ 下一轮会**再发一次**（重复发货）。
    """
    assert pl.classify_verify_row(_row(_acc("LM739", 0, "草稿"))) != "ok"


def test_ok_is_a_whitelist_not_a_blacklist(pl):
    """只有 2/3 才算可发 —— 未知状态码一律不发（漏发还能下一轮补，重复发不行）。"""
    assert pl.classify_verify_row(_row(_acc("LM739", 2, "仓库处理中"))) == "ok"
    assert pl.classify_verify_row(_row(_acc("LM739", 3, "已出库"))) == "ok"
    for st in (0, 6, 7, 99):
        assert pl.classify_verify_row(_row(_acc("LM739", st, ""))) != "ok", \
            "status=%s 不在白名单里，不能算可发" % st


def test_cancelled_is_not_ok(pl):
    """已取消（4）也不该发。"""
    assert pl.classify_verify_row(_row(_acc("LM739", 4, "已取消"))) != "ok"


# ---------------------------------------------------------------- 判定唯一性
def test_gate_and_reports_share_one_verdict_function():
    """门控、审计报告都必须调同一个判定函数（禁止各写一份 → 迟早不一致）。"""
    src = open(PIPE, encoding="utf-8").read()
    assert src.count("classify_verify_row(") >= 3, \
        "判定函数应被门控 + 审计报告 + 定义处引用（≥3 次）"
    # 旧的内联判据（自己数 status==1）不许再出现
    assert src.count('int(x.get("status") or 0) == 1') == 0, \
        "门控里不许再内联判 status==1 —— 统一走 classify_verify_row"
