# -*- coding: utf-8 -*-
"""test_temu_sales_push.py — TEMU 导出→企微推送 的纯单测（离线，不调 node/不发消息）。

守四条不变量（都是"错了会真发错东西"的地方）：
  ① **发群硬闸门**（AGENTS.md T1）：`发送.enabled` / `发送.group_enabled` 任一关着 → HARD STOP(42)，
     且**一个字节都不发**（send 函数被调用即算失败）。
  ② **出口自检**：附件缺/0 字节/列数不符/「订单创建时间」含非目标日 → 不发（exit 1）。
  ③ **成功判据**：本租户 aibot send 成功响应**没有 errcode**，只有 `success: true`
     （2026-10-08 实测：只看 errcode 会把成功读成失败 → 重复补发）。两者都要认，且 errcode!=0 要判失败。
  ④ **目标唯一命中**：按群名精确匹配，未命中 / 命中多个 → 停下不发。

运行: python -m pytest scripts/power/tests/test_temu_sales_push.py -q
"""
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
sys.path.insert(0, _POWER)

import run_temu_sales_push as p  # noqa: E402

DAY = "2026-10-07"


# ------------------------------------------------------------------ 夹具
def _mk_xlsx(path, day, cols=39, rows=3, extra_day=None):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    header = ["订单号", "订单创建时间"] + ["c%d" % i for i in range(cols - 2)]
    ws.append(header)
    for i in range(rows):
        ws.append(["no%d" % i, day] + ["x"] * (cols - 2))
    if extra_day:
        ws.append(["bad", extra_day] + ["x"] * (cols - 2))
    wb.save(path)


def _cfg(tmp_path, day=DAY, enabled=True, group_enabled=False, cols=39, files=None, dry=False):
    temu = tmp_path / "每日导出销量" / day / "TEMU"
    temu.mkdir(parents=True, exist_ok=True)
    names = [t.format(mmdd=p._mmdd(day)) for t in (p._load_cfg(os.path.join(_POWER, "data", "temu_sales_push.json"))["附件"]["文件模板"])]
    for i, n in enumerate(names):
        if files and i in files:
            continue
        _mk_xlsx(str(temu / n), day, cols=cols)
    cfg = {
        "发送": {"enabled": enabled, "to": "group", "group_enabled": group_enabled, "dry_run": dry},
        "目标": {"chat_name": "TEMU订单", "chat_type": "group"},
        "企微": {"node_path": "node", "wecom_js": "wecom.js"},
        "附件": {"目录模板": "每日导出销量/{date}/TEMU",
                 "文件模板": ["{mmdd}BHY.xlsx", "{mmdd}KinglamanSport.xlsx", "{mmdd}BEL.xlsx"],
                 "校验": {"订单创建时间列": "订单创建时间", "列数": cols}},
        "文案": {"模板": "【TEMU 订单导出 · 数据日期 {date}】x"},
        "幂等": {"状态文件": "_logs/temu_sales_push_state.json"},
    }
    pth = tmp_path / "cfg.json"
    pth.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return str(pth)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """把模块的工作目录/外部调用全部换成假的，并记录"有没有真的发"。"""
    monkeypatch.setattr(p, "WS", str(tmp_path))
    sent = {"text": [], "file": []}
    monkeypatch.setattr(p, "_resolve_node", lambda cfg: "node")
    monkeypatch.setattr(p, "_resolve_wecom", lambda cfg: "wecom.js")
    monkeypatch.setattr(p, "_sessions", lambda n, w: ([
        {"chat_id": "g1", "chat_name": "TEMU订单", "chat_type": "group", "last_msg_time": "x"},
        {"chat_id": "u1", "chat_name": "李卓然", "chat_type": "single", "last_msg_time": "x"},
    ], ""))
    monkeypatch.setattr(p, "_send_markdown", lambda n, w, c, t: (sent["text"].append(c) or True, "ok"))
    monkeypatch.setattr(p, "_send_file", lambda n, w, c, f: (sent["file"].append(f) or True, "ok"))
    return sent


def _run(monkeypatch, cfg, *extra):
    monkeypatch.setattr(sys, "argv", ["run_temu_sales_push.py", "--config", cfg] + list(extra))
    return p.main()


# ------------------------------------------------------------------ ③ 成功判据
def test_success_judgement_accepts_success_flag_and_zero_errcode():
    assert p._is_success({"success": True}) is True
    assert p._is_success({"errcode": 0, "errmsg": "ok"}) is True
    assert p._is_success({"success": False}) is False
    assert p._is_success({"errcode": 850003, "errmsg": "authorization expired"}) is False
    assert p._is_success(None) is False


def test_pick_json_survives_extra_noise():
    """CLI 输出里混着 stderr 警告行（2026-10-08 实测 `[wecom] json repair: ...`）也要能抠出 JSON。"""
    txt = '[wecom] json repair: 输入 JSON 已自动修复\n{"success": true}\n'
    assert p._pick_json(txt) == {"success": True}
    assert p._pick_json('{"type":"file","media_id":"MID"} trailing') == {"type": "file", "media_id": "MID"}
    assert p._pick_json("") is None


# ------------------------------------------------------------------ ④ 目标匹配
def test_match_target_unique_miss_and_multi():
    ss = [{"chat_name": "TEMU订单", "chat_type": "group", "chat_id": "a"},
          {"chat_name": "李卓然", "chat_type": "single", "chat_id": "b"}]
    hit, why = p._match_target(ss, "TEMU订单", "group")
    assert hit and hit["chat_id"] == "a" and why == ""
    hit2, why2 = p._match_target(ss, "没有这个群", "group")
    assert hit2 is None and "没有名为" in why2
    hit3, why3 = p._match_target([dict(ss[0]), dict(ss[0])], "TEMU订单", "group")
    assert hit3 is None and "无法确定目标" in why3


# ------------------------------------------------------------------ ② 出口自检
def test_verify_xlsx_ok_and_header_not_counted(tmp_path):
    f = str(tmp_path / "a.xlsx")
    _mk_xlsx(f, DAY, rows=3)
    ok, detail = p._verify_xlsx(f, DAY, {"附件": {"校验": {"订单创建时间列": "订单创建时间", "列数": 39}}})
    assert ok and "3 行全部" in detail, detail


def test_verify_xlsx_rejects_wrong_day_and_wrong_cols(tmp_path):
    f = str(tmp_path / "a.xlsx")
    _mk_xlsx(f, DAY, rows=2, extra_day="2026-10-06")
    ok, detail = p._verify_xlsx(f, DAY, {"附件": {"校验": {"订单创建时间列": "订单创建时间", "列数": 39}}})
    assert (not ok) and "2026-10-06" in detail
    f2 = str(tmp_path / "b.xlsx")
    _mk_xlsx(f2, DAY, cols=36)
    ok2, detail2 = p._verify_xlsx(f2, DAY, {"附件": {"校验": {"订单创建时间列": "订单创建时间", "列数": 39}}})
    assert (not ok2) and "列数" in detail2


def test_missing_file_blocks_send(monkeypatch, wired, tmp_path):
    cfg = _cfg(tmp_path, files={1})          # 少一个店的文件
    rc = _run(monkeypatch, cfg)
    assert rc == 1 and wired["text"] == [] and wired["file"] == []


def test_wrong_day_file_blocks_send(monkeypatch, wired, tmp_path):
    cfg = _cfg(tmp_path)
    bad = tmp_path / "每日导出销量" / DAY / "TEMU" / ("%sBHY.xlsx" % p._mmdd(DAY))
    _mk_xlsx(str(bad), DAY, rows=1, extra_day="2026-10-01")
    rc = _run(monkeypatch, cfg)
    assert rc == 1 and wired["file"] == []


# ------------------------------------------------------------------ ① 硬闸门
def test_group_gate_hard_stop_and_nothing_sent(monkeypatch, wired, tmp_path):
    cfg = _cfg(tmp_path, enabled=True, group_enabled=False)
    rc = _run(monkeypatch, cfg)
    assert rc == p.preflight.HARD_STOP_CODE == 42
    assert wired["text"] == [] and wired["file"] == []


def test_master_switch_off_hard_stop(monkeypatch, wired, tmp_path):
    cfg = _cfg(tmp_path, enabled=False, group_enabled=True)
    rc = _run(monkeypatch, cfg)
    assert rc == 42 and wired["text"] == [] and wired["file"] == []


def test_dry_run_sends_nothing_and_exits_zero(monkeypatch, wired, tmp_path):
    cfg = _cfg(tmp_path, enabled=True, group_enabled=True)
    rc = _run(monkeypatch, cfg, "--dry-run")
    assert rc == 0 and wired["text"] == [] and wired["file"] == []


# ------------------------------------------------------------------ 正向 + 幂等
def test_open_gate_sends_caption_then_files(monkeypatch, wired, tmp_path):
    cfg = _cfg(tmp_path, enabled=True, group_enabled=True)
    rc = _run(monkeypatch, cfg)
    assert rc == 0, rc
    assert len(wired["text"]) == 1 and len(wired["file"]) == 3
    st = json.loads((tmp_path / "_logs" / "temu_sales_push_state.json").read_text(encoding="utf-8"))
    assert DAY in st


def test_same_day_skipped_without_force(monkeypatch, wired, tmp_path):
    cfg = _cfg(tmp_path, enabled=True, group_enabled=True)
    assert _run(monkeypatch, cfg) == 0
    wired["text"].clear()
    wired["file"].clear()
    assert _run(monkeypatch, cfg) == 0                     # 第二次：幂等跳过
    assert wired["text"] == [] and wired["file"] == []
    assert _run(monkeypatch, cfg, "--force") == 0          # --force：真重发
    assert len(wired["file"]) == 3


def test_target_not_found_stops(monkeypatch, wired, tmp_path):
    cfg = _cfg(tmp_path, enabled=True, group_enabled=True)
    monkeypatch.setattr(p, "_sessions", lambda n, w: ([{"chat_name": "别的群", "chat_type": "group",
                                                        "chat_id": "z", "last_msg_time": "x"}], ""))
    rc = _run(monkeypatch, cfg)
    assert rc == 1 and wired["text"] == []


def test_sessions_failure_stops(monkeypatch, wired, tmp_path):
    cfg = _cfg(tmp_path, enabled=True, group_enabled=True)
    monkeypatch.setattr(p, "_sessions", lambda n, w: ([], "errcode 850003 authorization expired"))
    rc = _run(monkeypatch, cfg)
    assert rc == 1 and wired["text"] == []
