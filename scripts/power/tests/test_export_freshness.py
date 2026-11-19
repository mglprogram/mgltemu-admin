# -*- coding: utf-8 -*-
"""test_export_freshness.py — 「导出后与昨日单号重叠校验」的回归钉。

2026-11-19 用户令：「加上『导出后与昨日单号重叠校验 + 过期就重导』这道校验」。
真机背景（BS 整店误判缺货）：当天 04:00 导出 14 单，11:00 现场重导 69 单，
两批单号**零重叠** —— 04:00 拿的是上一轮的残留表，于是恒信仓 / 帕沃尔仓两张表全空。

判据（纯函数 `stale_export_reason`）：
  · 本批单号 ≥ min_sample（默认 5）；
  · 且与「昨天那份」重叠率 ≥ min_overlap（默认 50%）。
两条都命中才算旧 —— 小批量店（真没单）不会被误伤。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_SCRIPTS = os.path.dirname(_POWER)
for p in (_POWER, os.path.join(_POWER, "lib"), _SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_temu_fulfill as F  # noqa: E402


# ---------------------------------------------------------------- 纯函数判据
def test_full_overlap_is_stale():
    cur = {"A1", "A2", "A3", "A4", "A5", "A6"}
    prev = {"A1", "A2", "A3", "A4", "A5", "A6", "OLD"}
    assert F.stale_export_reason(cur, prev)


def test_zero_overlap_is_fresh():
    """真机 BS：14 单与昨天零重叠 —— 这不是旧表，判新鲜（重导由别的证据触发）。"""
    cur = {"A1", "A2", "A3", "A4", "A5", "A6"}
    prev = {"B1", "B2", "B3", "B4", "B5", "B6"}
    assert F.stale_export_reason(cur, prev) == ""


def test_half_overlap_is_stale_boundary():
    cur = {"A1", "A2", "A3", "A4", "A5", "A6"}
    prev = {"A1", "A2", "A3", "X", "Y", "Z"}      # 3/6 = 50%
    # 50% 命中阈值（>=）→ 旧
    assert F.stale_export_reason(cur, prev, min_overlap=0.5)
    # 阈值抬到 0.6 → 不算旧
    assert F.stale_export_reason(cur, prev, min_overlap=0.6) == ""


def test_small_batch_is_never_flagged():
    """小批量（< min_sample）一律不判旧 —— 待处理只有 2 单的店不能天天被判旧表。"""
    cur = {"A1", "A2"}
    prev = {"A1", "A2"}
    assert F.stale_export_reason(cur, prev) == ""
    assert F.stale_export_reason(cur, prev, min_sample=10) == ""


def test_missing_prev_or_cur_is_fresh():
    assert F.stale_export_reason(set(), {"A1"}) == ""
    assert F.stale_export_reason({"A1"}, set()) == ""
    assert F.stale_export_reason(set(), set()) == ""


def test_reason_mentions_overlap_numbers():
    cur = {"A%d" % i for i in range(10)}
    prev = set(cur) | {"Z"}
    r = F.stale_export_reason(cur, prev)
    assert "100%" in r and "10/10" in r, r


# ---------------------------------------------------------------- 读表 / 比对文件
def _make_xlsx(path, orders, header="订单号"):
    from openpyxl import Workbook  # noqa: PLC0415
    wb = Workbook()
    ws = wb.active
    ws.append([header, "卖家SKU", "数量"])
    for o in orders:
        ws.append([o, "BS-X-1", 1])
    wb.save(path)
    return path


def test_order_ids_reads_unique_ids(tmp_path):
    p = _make_xlsx(str(tmp_path / "a.xlsx"), ["O1", "O2", "O1", ""])
    assert F._order_ids(p) == {"O1", "O2"}


def test_order_ids_tolerates_missing_file(tmp_path):
    assert F._order_ids(str(tmp_path / "nope.xlsx")) == set()


def test_order_ids_without_order_column_returns_empty(tmp_path):
    p = _make_xlsx(str(tmp_path / "b.xlsx"), ["O1"], header="单号X")
    assert F._order_ids(p) == set()


def test_gate_marks_stale_and_deletes_old_file(tmp_path):
    """判旧时必须删掉旧表（否则重导会写进同名文件、人看到的是旧的）。"""
    day = "2026-11-19"
    cur_path = _make_xlsx(str(tmp_path / "cur.xlsx"), ["O1", "O2", "O3", "O4", "O5", "O6"])
    prev = {"O1", "O2", "O3", "O4", "O5", "O6"}
    orig = F._yesterday_order_ids
    F._yesterday_order_ids = lambda store, d: prev      # noqa: E731
    try:
        info = F._export_freshness_gate("BS", day, cur_path, log=lambda *a, **k: None)
    finally:
        F._yesterday_order_ids = orig
    assert info["verdict"] == "stale", info
    assert not os.path.exists(cur_path), "判旧时应删掉旧表"


def test_gate_fresh_keeps_file(tmp_path):
    cur_path = _make_xlsx(str(tmp_path / "cur.xlsx"), ["N1", "N2", "N3", "N4", "N5", "N6"])
    orig = F._yesterday_order_ids
    F._yesterday_order_ids = lambda store, d: {"O%d" % i for i in range(6)}  # noqa: E731
    try:
        info = F._export_freshness_gate("BS", "2026-11-19", cur_path,
                                        log=lambda *a, **k: None)
    finally:
        F._yesterday_order_ids = orig
    assert info["verdict"] == "fresh", info
    assert os.path.exists(cur_path)


def test_gate_unknown_when_no_prev(tmp_path):
    cur_path = _make_xlsx(str(tmp_path / "cur.xlsx"), ["N1", "N2"])
    orig = F._yesterday_order_ids
    F._yesterday_order_ids = lambda store, d: set()     # noqa: E731
    try:
        info = F._export_freshness_gate("BS", "2026-11-19", cur_path,
                                        log=lambda *a, **k: None)
    finally:
        F._yesterday_order_ids = orig
    assert info["verdict"] == "fresh" and info["prev_orders"] == 0


def test_export_kind_by_store():
    assert F._export_kind("BS") == "SHEIN"
    assert F._export_kind("BE") == "SHEIN"
    assert F._export_kind("BHY") == "TEMU"
