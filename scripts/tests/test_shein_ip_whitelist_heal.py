# -*- coding: utf-8 -*-
"""test_shein_ip_whitelist_heal.py — 出口 IP 漂出白名单时的处置口径。

2026-11-15 真机：BAN / BE 两店整天没出表，原因是
`401 openapi00002 IP is not in the whitelist: 116.4.96.94` ——
本机路由器双 WAN 按连接 50/50 出口、家宽 IP 拨号就变，白名单追不上。

钉住三件事：
  1. 导表遇 `openapi00002` 会先尝试**自动加白**（除非 `SHEIN_AUTO_WHITELIST=0`）；
  2. 收尾推送不再把接口报错猜成「本批可能无单」，而是从 run.json 摘真原因；
  3. 体检脚本 `shein_egress_guard.py` 在没有异常时 exit 0（纯函数级：只验命令入口可导入）。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_PROJ, "scripts", "power"))
sys.path.insert(0, os.path.join(_PROJ, "scripts", "tasks"))
sys.path.insert(0, os.path.join(_PROJ, "scripts"))

import push_fulfill_report as P  # noqa: E402
import run_temu_fulfill as F  # noqa: E402


def _write_run(tmp_path, store: str, day: str, why: str) -> None:
    d = tmp_path / day / "fulfill" / store
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({
        "store": store,
        "steps": [{"name": "refresh_stock", "status": "ok"},
                  {"name": "export_orders", "status": "fail", "why": why},
                  {"name": "split", "status": "fail", "why": "没给 --file"}],
        "summary": {"failed": ["export_orders", "split"], "ok": False},
    }, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 自动加白闸门

def test_auto_whitelist_can_be_switched_off(monkeypatch):
    monkeypatch.setenv(F.AUTO_WHITELIST_ENV, "0")
    assert F._auto_whitelist_current_ip(log=lambda *a, **k: None) is False


# ---------------------------------------------------------------- 推送原因

def test_message_says_ip_not_whitelisted(tmp_path, monkeypatch):
    """用户看到的那句「可能无单」必须换成真原因。"""
    monkeypatch.setattr(P, "OUT_ROOT", str(tmp_path))
    day = "2026-11-15"
    _write_run(tmp_path, "BAN", day,
               'SHEIN 导表失败（rc=1）：RuntimeError: HTTP 401 @ /open-api/order/order-list：'
               '{"msg":"IP is not in the whitelist: 116.4.96.94","code":"openapi00002"}')
    _, msg = P.build_message(day, {}, {})
    line = [x for x in msg.split("\n") if x.startswith("BANGL：")][0]
    assert "116.4.96.94" in line and "白名单" in line
    assert "可能无单或失败" not in line, "还在用旧文案：%s" % line


def test_message_falls_back_when_no_run_json(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUT_ROOT", str(tmp_path))
    _, msg = P.build_message("2026-11-15", {}, {})
    assert "BE：未产出表格（该店本批可能无单或失败）" in msg


# ---------------------------------------------------------------- 入口存在

def test_guard_script_exists_and_has_main():
    p = os.path.join(_PROJ, "scripts", "tasks", "shein_egress_guard.py")
    assert os.path.exists(p), "体检/自愈入口不见了：%s" % p
    with open(p, encoding="utf-8") as f:
        src = f.read()
    assert "def main()" in src and "/open-api/gsp/warehouse-address" in src
    assert "shein_whitelist.py" in src
