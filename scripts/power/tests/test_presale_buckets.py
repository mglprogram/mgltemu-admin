# -*- coding: utf-8 -*-
"""test_presale_buckets.py — 预售面板「7天 / 15天」分档守门。

★★ 2026-11-27 用户追问：
    「发货前有没有去读取 TEMU 的修改库存和预售面板（BEL/BHY/KLS），
      把开了预售的产品收录到 7天/15天 预售 sheet？」

查下来：**面板确实读了**（`load_presale_panel`，实测覆盖三店 49 个预售价），
但**7天/15天 分不开** —— 因为 `items[].date` 格式不统一：

    "2026-11-30"      ← 字符串日期
    1795795199999     ← 毫秒时间戳（运营从后台直接透传）

原来只按 `%Y-%m-%d` 解析，35 条时间戳全被判「未知」⇒ 7天/15天 全落进更久桶。
本文件守：两种格式都要认，分档不许丢单。
"""
from __future__ import annotations

import datetime as dt
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
for _p in (_POWER, os.path.join(_POWER, "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bel_split_by_stock as B  # noqa: E402


def _item(store="BEL", code="BEL-204203-4SetC", size="L", date="", status="预售中"):
    return {"platform": "TEMU", "store": store, "code": code,
            "size": size, "date": date, "status": status}


def _panel(*items):
    return {"items": list(items)}


# ---------------------------------------------------------------- 日期解析


def test_date_string_format():
    assert B._panel_restock_date("2026-11-30") == "2026-11-30"
    assert B._panel_restock_date("2026/11/30") == "2026-11-30"
    assert B._panel_restock_date("2026-11-30 12:00:00") == "2026-11-30"


def test_date_millisecond_timestamp():
    """实测 1795795199999 → 2026-11-27（运营透传的毫秒时间戳）。"""
    got = B._panel_restock_date(1795795199999)
    assert got.startswith("2026-11-"), "毫秒时间戳没解析出来：%r" % got


def test_date_second_timestamp_also_ok():
    sec = int(dt.datetime(2026, 12, 5, 12, 0).timestamp())
    assert B._panel_restock_date(sec) == "2026-12-05"


def test_date_garbage_returns_empty():
    for bad in ("", None, "abc", "不是日期"):
        assert B._panel_restock_date(bad) == ""


# ---------------------------------------------------------------- 分档


def test_buckets_split_by_gap():
    today = "2026-11-27"
    raw = _panel(
        _item(size="L", date="2026-11-30"),    # +3 天 → 7天
        _item(size="M", date="2026-12-04"),    # +7 天 → 7天（边界含）
        _item(size="S", date="2026-12-05"),    # +8 天 → 15天
        _item(size="XL", date="2026-12-12"),   # +15 天 → 15天（边界含）
        _item(size="XXL", date="2026-12-20"),  # +23 天 → 更久
    )
    b = B.panel_presale_buckets(raw, today=today)
    sizes = {k: set(v.get("BEL", {}).get("204203-4SETC", set())) for k, v in b.items()}
    assert sizes["7天"] == {"L", "M"}, sizes["7天"]
    assert sizes["15天"] == {"S", "XL"}, sizes["15天"]
    assert sizes["更久"] == {"XXL"}, sizes["更久"]


def test_bucket_never_drops_unknown_dates():
    """日期解析不出 → 进「更久」，**不许丢单**（否则预售货会被当现货发出去）。"""
    raw = _panel(_item(size="L", date="垃圾日期"))
    b = B.panel_presale_buckets(raw, today="2026-11-27")
    assert "L" in b["更久"].get("BEL", {}).get("204203-4SETC", set())


def test_bucket_mixed_formats_are_separated():
    """同一份面板里两种日期格式混着 —— 这正是原来分不开的原因。"""
    today = "2026-11-27"
    ts = int(dt.datetime(2026, 12, 10, 23, 59).timestamp()) * 1000
    raw = _panel(
        _item(size="L", date="2026-11-30"),   # 字符串，+3 天 → 7天
        _item(size="M", date=str(ts)),        # 毫秒，+13 天 → 15天
    )
    b = B.panel_presale_buckets(raw, today=today)
    got7 = b["7天"].get("BEL", {}).get("204203-4SETC", set())
    got15 = b["15天"].get("BEL", {}).get("204203-4SETC", set())
    assert "L" in got7, "字符串日期没进 7天"
    assert "M" in got15, "毫秒时间戳没进 15天（就是它原来判成未知）"


def test_buckets_only_temu_presale_rows():
    """只收 TEMU + 预售中；SHEIN 行 / 已清零行一律不收。"""
    raw = _panel(
        _item(size="L", date="2026-11-30"),
        {"platform": "SHEIN", "store": "BS", "code": "BS-1", "size": "M",
         "date": "2026-11-30", "status": "预售中"},
        _item(size="XL", date="2026-11-30", status="已清零"),
    )
    b = B.panel_presale_buckets(raw, today="2026-11-27")
    all_sizes = set()
    for bucket in b.values():
        for codes in bucket.values():
            for ss in codes.values():
                all_sizes |= ss
    assert all_sizes == {"L"}, "混进了非 TEMU 或非预售中的行：%s" % all_sizes


def test_bucket_structure_matches_norm_panel():
    """返回结构与 `_norm_panel()` 同构：`{店: {款号: set(尺码)}}`，便于直接喂给拆仓。"""
    b = B.panel_presale_buckets(_panel(_item(size="L", date="2026-11-30")),
                                today="2026-11-27")
    seg = b["7天"]["BEL"]["204203-4SETC"]
    assert isinstance(seg, set) and "L" in seg
