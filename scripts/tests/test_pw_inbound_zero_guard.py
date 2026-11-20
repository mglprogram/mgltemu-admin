# -*- coding: utf-8 -*-
"""帕沃尔头程「解析到 0 单」不能静默写盘（2026-11-20 事故回归）。

事故：2026-11-20 12:06 那次定时抓取，帕沃尔列表只翻到 1645 单（正常 2576），
`14450` 开头解析出 **0 单**，但脚本照常落盘 —— 头程跟踪快照从 292 行掉到 209 行，
帕沃尔（14450）整批从页面上消失，用户第二天才发现。

修法两处：
  ① `pw_inbound_track.fetch()` 解析到 0 单直接抛 PWError（抓不全就别当成功）；
  ② `fetch_shipment_track.main()` 捕获后 return 5，**不写 shipment-track.json**，
     保留上一次的快照。
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PW_TRACK = ROOT / "scripts" / "pw_orders" / "pw_inbound_track.py"
SHIP = ROOT / "scripts" / "goods" / "fetch_shipment_track.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_zero_parsed_raises():
    """`mine` 为空时必须抛 PWError，而不是返回空列表让上层照常写盘。"""
    src = PW_TRACK.read_text(encoding="utf-8")
    assert "if not mine:" in src
    assert "raise PWError(" in src
    # 报错文案要带翻页数与行数，方便下次直接定位
    assert "共翻 %d 页 / %d 行" in src


def test_upper_layer_aborts_without_write():
    """上层捕获异常后 return 5，且**在写文件之前**返回。"""
    src = SHIP.read_text(encoding="utf-8")
    i_catch = src.index("✗ 帕沃尔头程抓取失败")
    i_write = src.index("payload[\"summary\"]")
    assert i_catch < i_write, "抓取失败的早退必须在落盘之前"
    assert "return 5" in src[i_catch - 200:i_catch + 600]


def test_both_files_parse():
    for p in (PW_TRACK, SHIP):
        ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def test_pw_error_imported():
    src = PW_TRACK.read_text(encoding="utf-8")
    assert "from pw_session import PW, PWError" in src


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
