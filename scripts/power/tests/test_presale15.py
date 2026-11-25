"""15 天预售（长时效预售）口径的单元测试 —— 不连网络、不写平台。

钉住用户 2026-11-19 给的名单（config/temu_presale_15d.json）：
  BHY：202504-10SetA S/M/XL/XXL；204203-4SetA XL/XXL；204203-4SetC M/L/XL/XXL
  BEL：204203-4SetC M/L/XL/XXL
以及它在发货表里的落法：
  · 只有点名了店铺的才出 `<店> 15天预售` sheet（SHEIN 店不出）；
  · 同款没点名的尺码照常走仓/缺货，不能整款被扣；
  · 15 天预售与 7 天预售互不串组（这两批名单没有交集，但口径要分开）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "power"))

import bel_split_by_stock as B  # noqa: E402
import gen_shortage_fail_xlsx as G  # noqa: E402

CASES_YES = [
    ("BHY-202504-10SetA-S", "BHY"),
    ("BHY-202504-10SetA-M", "BHY"),
    ("BHY-202504-10SetA-XL", "BHY"),
    ("BHY-202504-10SetA-XXL", "BHY"),
    ("BHY-204203-4SetA-XL", "BHY"),
    ("BHY-204203-4SetA-XXL", "BHY"),
    ("BHY-204203-4SetC-M", "BHY"),
    ("BHY-204203-4SetC-L", "BHY"),
    ("BHY-204203-4SetC-XL", "BHY"),
    ("BHY-204203-4SetC-XXL", "BHY"),
    ("BEL-204203-4SetC-M", "BEL"),
    ("BEL-204203-4SetC-L", "BEL"),
    ("BEL-204203-4SetC-XL", "BEL"),
    ("BEL-204203-4SetC-XXL", "BEL"),
]

CASES_NO = [
    ("BHY-202504-10SetA-L", "BHY"),      # 没点名的尺码
    ("BHY-204203-4SetA-M", "BHY"),
    ("BEL-204203-4SetA-XL", "BEL"),      # BEL 只有 4SetC
    ("BEL-202504-10SetA-S", "BEL"),      # BEL 没有 10SetA
    ("BHY-204203-4SetC-3XL", "BHY"),     # 没点名的尺码（2026-11-25 S 已收录，换成 3XL 当反例）
    ("BD-204203-4SetC-M", "BD"),         # SHEIN 店不该进
    ("BHY-204203-4SetC-M", ""),          # 不给店 → False
]


@pytest.mark.parametrize("key,store", CASES_YES)
def test_named_sizes_are_presale15(key, store):
    assert B.is_presale15(key, store) is True


@pytest.mark.parametrize("key,store", CASES_NO)
def test_unnamed_sizes_are_not(key, store):
    assert B.is_presale15(key, store) is False


def test_config_matches_user_list():
    cfg = B.load_presale15(force=True)
    assert set(cfg) == {"BHY", "BEL"}
    assert cfg["BHY"]["202504-10SETA"] == {"S", "M", "XL", "XXL"}
    assert cfg["BHY"]["204203-4SETA"] == {"XL", "XXL"}
    assert cfg["BHY"]["204203-4SETC"] == {"S", "M", "L", "XL", "XXL"}
    assert cfg["BEL"] == {"204203-4SETC": {"S", "M", "L", "XL", "XXL"}}


def test_presale15_and_presale_are_separate(monkeypatch):
    """15 天预售 ≠ 7 天预售：这些 SKU 不该同时被判成 7 天预售。

    ★ 2026-11-19 口径换代后，7 天预售读的是**面板**（运营在后台开的）。
      面板里如果某款真的开了 7 天预售，它同时进 7天/D 组是**对的**（只是表上分开排）。
      所以这里只验「15天名单本身不会让 is_presale 为真」：
      把面板缓存置空（＝读不到面板）再判，避免用真实面板数据让用例随线上漂移。
    """
    monkeypatch.setattr(B, "PANEL_CACHE", {}, raising=True)
    monkeypatch.setattr(B, "PANEL_META", {}, raising=True)
    for key, store in CASES_YES:
        assert B.is_presale(key, store) is False


def test_sheet_emitted_only_for_configured_stores():
    split = {"groups": {"PRESALE15": [
        {"ord": "PO-1", "sku": "BHY-204203-4SetC-M", "key": "K", "qty": 1,
         "why": "15 天预售款"}]}}
    bhy = G.group_sheets([], split, "BHY")
    assert "BHY 15天预售" in bhy
    assert (bhy["BHY 15天预售"]["rows"] or [])[0]["参考单号"] == "PO-1"
    assert "BEL 15天预售" in G.group_sheets([], {"groups": {}}, "BEL")
    assert "BD 15天预售" not in G.group_sheets([], {"groups": {}}, "BD")


def test_workbook_contains_sheet(tmp_path):
    """真的落到 xlsx 上，确认 sheet 名和行都在。"""
    from openpyxl import load_workbook  # noqa: PLC0415

    split = {"groups": {"PRESALE15": [
        {"ord": "PO-9", "sku": "BEL-204203-4SetC-XL", "key": "K", "qty": 2,
         "why": "15 天预售款"}]}}
    sheets = G.group_sheets([], split, "BEL")
    out = tmp_path / "BEL_test.xlsx"
    G.write_xlsx("BEL", sheets, str(out))
    wb = load_workbook(out)
    assert "BEL 15天预售" in wb.sheetnames
    ws = wb["BEL 15天预售"]
    assert [c.value for c in ws[1]][:2] == ["参考单号", "平台SKU"]
    assert ws.cell(row=2, column=1).value == "PO-9"
