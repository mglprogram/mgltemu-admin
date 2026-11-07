# -*- coding: utf-8 -*-
"""test_push_fulfill_report.py — 发货收尾推送的纯函数与硬约束。

用户 2026-11-07 口径：
    「这个 4 点的发货任务最后完成后，推送给我飞书的 temu 群和云盘把两个表格」

钉住四条真机踩出来的约束：
  · 找不到表就**不推**（不是失败：该批可能全成功或无单）
  · 群消息标题必须含 `TEMU`（群机器人关键词校验，否则 19024）
  · kdocs / 飞书都必须**显式**用 7897（进程环境的 10808 是废弃端口，
    urllib 会照它连、直接 WinError 10061，且改 env 也压不住）
  · 推送/上传失败**永不改退出码**（发货已完成）
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_PROJ, "scripts", "tasks"))
sys.path.insert(0, os.path.join(_PROJ, "scripts"))

import push_fulfill_report as P  # noqa: E402


# ---------------------------------------------------------------- 找表

def test_find_reports_returns_empty_for_unknown_day():
    assert P.find_reports("1999-01-01") == {}


def test_find_reports_picks_newest_when_v2_exists(tmp_path, monkeypatch):
    """`report` 因文件被占用会改存 `_v2`，必须取最新的那个而不是死认原名。"""
    monkeypatch.setattr(P, "OUT_ROOT", str(tmp_path))
    day = "2026-11-07"
    d = tmp_path / day
    d.mkdir()
    old = d / "BHY_缺货与失败订单_20261107.xlsx"
    new = d / "BHY_缺货与失败订单_20261107_v2.xlsx"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    got = P.find_reports(day)
    assert os.path.basename(got["BHY"]) == new.name


def test_find_reports_ignores_other_days(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUT_ROOT", str(tmp_path))
    d = tmp_path / "2026-11-07"
    d.mkdir()
    (d / "BHY_缺货与失败订单_20261106.xlsx").write_bytes(b"x")
    assert "BHY" not in P.find_reports("2026-11-07")


# ---------------------------------------------------------------- 消息

def test_title_contains_the_keyword():
    """群机器人开了关键词校验，标题不含 TEMU 会回 19024。"""
    title, _ = P.build_message("2026-11-07", {}, {})
    assert "TEMU" in title


def test_message_lists_both_stores_and_links():
    files = {"BHY": "/tmp/a.xlsx", "BEL": "/tmp/b.xlsx"}
    title, msg = P.build_message("2026-11-07", files,
                                 {"BHY": "https://www.kdocs.cn/l/aaa"})
    assert "BHY" in msg and "BEL" in msg
    assert "https://www.kdocs.cn/l/aaa" in msg
    assert "2026-11-07" in title


def test_message_marks_missing_store():
    files = {"BHY": "/tmp/a.xlsx"}
    _, msg = P.build_message("2026-11-07", files, {})
    assert "BEL：未产出表格" in msg


# ---------------------------------------------------------------- 代理

def test_kdocs_env_forces_7897():
    """进程环境里的 10808 是废弃端口；必须显式覆盖成 7897。"""
    env = P.kdocs_env()
    assert env["HTTP_PROXY"] == P.KDOCS_PROXY
    assert env["HTTPS_PROXY"] == P.KDOCS_PROXY
    assert "10808" not in env["HTTPS_PROXY"]


def test_feishu_proxy_is_7897():
    assert P.FEISHU_PROXY.endswith(":7897")


def test_kdocs_env_does_not_depend_on_inherited(monkeypatch):
    """哪怕父进程塞了 10808，也要被压成 7897。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10808")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10808")
    env = P.kdocs_env()
    assert "7897" in env["HTTPS_PROXY"]


# ---------------------------------------------------------------- 不改结论

def test_main_returns_zero_when_no_reports(tmp_path, monkeypatch, capsys):
    """没有表 ⇒ 不推、也不算失败。"""
    monkeypatch.setattr(P, "OUT_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["push", "--date", "1999-01-01"])
    assert P.main() == 0


def test_main_always_exits_zero(tmp_path, monkeypatch):
    """推送/上传都失败时也必须返回 0：发货已经做完了。"""
    monkeypatch.setattr(P, "OUT_ROOT", str(tmp_path))
    day = "2026-11-07"
    d = tmp_path / day
    d.mkdir()
    (d / "BHY_缺货与失败订单_20261107.xlsx").write_bytes(b"x")
    monkeypatch.setattr(P, "upload", lambda *a, **k: (False, "boom"))
    monkeypatch.setattr(P, "push_feishu_fixed", lambda *a, **k: (False, "boom"))
    monkeypatch.setattr(sys, "argv", ["push", "--date", day])
    assert P.main() == 0


# ---------------------------------------------------------------- 接入位置

def test_notify_is_last_pipeline_step():
    """用户要「最后完成后」推 ⇒ 必须是整链最后一步（在 report 之后）。"""
    import json  # noqa: PLC0415
    p = os.path.join(_PROJ, "scripts", "power", "data", "temu_fulfill_pipeline.json")
    if not os.path.exists(p):
        import pytest  # noqa: PLC0415
        pytest.skip("本机没有 pipeline json（data/ 被 gitignore）")
    with open(p, encoding="utf-8-sig") as fh:
        names = [s["name"] for s in json.load(fh)["steps"]]
    assert names[-1] == "notify", names
    assert names.index("report") < names.index("notify")


def test_notify_is_readonly():
    """它只读表 + 外部推送，不改平台状态 ⇒ 不该进写操作闸门。"""
    sys.path.insert(0, os.path.join(_PROJ, "scripts", "power"))
    import run_temu_fulfill as F  # noqa: PLC0415
    assert "notify" not in F.WRITE_STEPS
    assert F.gate_write("notify", confirm=False) == "run"
