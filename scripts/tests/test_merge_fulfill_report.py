# -*- coding: utf-8 -*-
"""test_merge_fulfill_report.py — 六店合成一张汇总表（用户 2026-11-15 令）。

用户原话：「最后一步总结表，六个店铺不分成六个大表格，直接合在一个表格，
每个店铺对应几个 sheet，一个个排好 sheet」。

钉住：
  1. sheet 顺序＝`SUMMARY_STORES`（BHY → BEL → BD → BANGL → BSGL → BE），店内 sheet 保持原序；
  2. 全局「失败订单」sheet 加店名前缀（否则六店撞名）；
  3. 单店文件里的系统键（BAN / BS）在汇总表里换成用户叫法（BANGL / BSGL）；
  4. 某店当天没表就跳过，不生成空 sheet；
  5. 群消息只报**一个**汇总表链接。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_PROJ, "scripts", "tasks"))
sys.path.insert(0, os.path.join(_PROJ, "scripts"))

import openpyxl  # noqa: E402
import push_fulfill_report as P  # noqa: E402


def _make_store_file(path, store, sheets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(name)
        ws.append(["参考单号", "平台SKU"])
        for r in rows:
            ws.append(r)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)
    return path


def test_merge_orders_sheets_by_store(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUT_ROOT", str(tmp_path))
    day = "2026-11-15"
    d = os.path.join(str(tmp_path), day)
    files = {
        "BHY": _make_store_file(os.path.join(d, "BHY_缺货与失败订单_20261115.xlsx"), "BHY",
                                [("BHY 恒信仓", [["PO-1", "BS2-203311-4SetA-M"]]),
                                 ("BHY 帕沃尔仓", []), ("BHY 缺货", []),
                                 ("BHY 预售", []), ("失败订单", [])]),
        "BANGL": _make_store_file(os.path.join(d, "BAN_缺货与失败订单_20261115.xlsx"), "BAN",
                                  [("BAN 帕沃尔仓", [["GSU1", "BE-1"]]),
                                   ("BAN 缺货", [["GSU2", "BE-2"]]), ("失败订单", [])]),
        "BE": _make_store_file(os.path.join(d, "BE_缺货与失败订单_20261115.xlsx"), "BE",
                               [("BE 帕沃尔仓", [["GSU3", "BE-3"]]),
                                ("BE 缺货", []), ("失败订单", [])]),
    }
    merged = P.merge_reports(day, files)
    assert merged and os.path.exists(merged)
    wb = openpyxl.load_workbook(merged)
    try:
        assert wb.sheetnames == [
            "BHY 恒信仓", "BHY 帕沃尔仓", "BHY 缺货", "BHY 预售", "BHY 失败订单",
            "BANGL 帕沃尔仓", "BANGL 缺货", "BANGL 失败订单",
            "BE 帕沃尔仓", "BE 缺货", "BE 失败订单",
        ], wb.sheetnames
        assert wb["BHY 恒信仓"]["A2"].value == "PO-1"
        assert wb["BANGL 帕沃尔仓"]["A2"].value == "GSU1"
    finally:
        wb.close()


def test_merge_skips_store_without_file(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUT_ROOT", str(tmp_path))
    day = "2026-11-15"
    d = os.path.join(str(tmp_path), day)
    files = {"BE": _make_store_file(os.path.join(d, "BE_x.xlsx"), "BE",
                                    [("BE 帕沃尔仓", [["A", "b"]]), ("失败订单", [])])}
    merged = P.merge_reports(day, files)
    wb = openpyxl.load_workbook(merged)
    try:
        assert wb.sheetnames == ["BE 帕沃尔仓", "BE 失败订单"]
    finally:
        wb.close()


def test_merge_returns_empty_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUT_ROOT", str(tmp_path))
    assert P.merge_reports("2026-11-15", {}) == ""


def test_message_reports_single_merged_link(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUT_ROOT", str(tmp_path))
    day = "2026-11-15"
    files = {"BHY": os.path.join(str(tmp_path), "BHY_x.xlsx")}
    title, msg = P.build_message(day, files, {},
                                 merged_link="https://www.kdocs.cn/l/xyz",
                                 merged_name="2026-11-15_发货汇总.xlsx")
    assert "TEMU" in title
    assert "https://www.kdocs.cn/l/xyz" in msg
    assert "2026-11-15_发货汇总.xlsx" in msg


def test_summary_stores_are_the_six_real_stores():
    # ★ 2026-11-17 用户令：「在发货总链加上一个 TM-KinglamanSport 店铺的发货」
    #   ⇒ 汇总表第 7 家，排在原有六家之后（原有顺序不动）。
    assert P.SUMMARY_STORES == ("BHY", "BEL", "BD", "BANGL", "BSGL", "BE",
                                "KinglamanSport")
