# -*- coding: utf-8 -*-
"""lib/log.py — 标准日志接口（工程控制闸门层）。

对治：agent 的操作日志是自由文本散文，无法被 @经验飞轮 机读/统计、无法追溯。
原则：`log.emit(fields)` 是唯一写入口 —— 字段白名单校验、必填校验、JSONL 落地，
     让每条日志都结构化、可机读、可审计（对齐 experience-flywheel SKILL「标准日志接口」）。

用法：
    from lib import log
    log.emit(ts=..., agent="@经验飞轮", action="sweep_detect", scope="本周·全部agent",
             input={"sources": [...], "window": "7d"}, result="found:3", verdict="ok",
             anomaly=[], writeback=[], error=None, ref="taskboard:t-xxx", next=[])

字段：
    ts(ISO,本机时区) / agent / action(枚举白名单) / scope / input(结构化) /
    result / verdict(ok|warn|fail) / anomaly[] / writeback[] / error(null|*) /
    ref(证据) / next[]
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

__all__ = ["ALLOWED_ACTIONS", "ALLOWED_VERDICTS", "emit", "summary_line", "LOG_EXIT"]

LOG_EXIT = 42

# action 枚举白名单（扩新动作=加这里；不放开自由文本）
ALLOWED_ACTIONS = frozenset({
    "query", "register", "maintain", "dispatch",        # @aaa 域
    "collect", "detect", "attribute", "writeback", "report",  # @经验飞轮 S1-S5
    "sweep_detect", "accept_check", "stage",              # 通用阶段/验收
})
ALLOWED_VERDICTS = ("ok", "warn", "fail")

# 必填字段
MANDATORY = ("ts", "agent", "action", "verdict")

# 布尔字段（必须在，可为空列表/None）
LIST_FIELDS = ("anomaly", "writeback", "next")

_ACTION_RE = frozenset(ALLOWED_ACTIONS)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _validate(fields: dict[str, Any]) -> dict[str, Any]:
    """白名单/必填校验，非法即抛（HARD STOP 语义）。"""
    for k in MANDATORY:
        if k not in fields or fields[k] in (None, ""):
            raise ValueError(f"log.emit 缺必填字段: {k}")
    if fields["action"] not in ALLOWED_ACTIONS:
        raise ValueError(f"log.emit action 不在白名单: {fields['action']}（新增需加 ALLOWED_ACTIONS）")
    if fields["verdict"] not in ALLOWED_VERDICTS:
        raise ValueError(f"log.emit verdict 非法: {fields['verdict']}（应 ok|warn|fail）")
    for f in LIST_FIELDS:
        if f in fields and not isinstance(fields[f], (list, tuple)):
            raise ValueError(f"log.emit 字段 {f} 应为列表")
    return fields


def emit(fields: dict[str, Any], log_dir: Optional[str] = None) -> Path:
    """校验并追加一行 JSONL 到 <log_dir>/<YYYY-MM-DD>.jsonl（UTF-8，路径 ASCII）。"""
    fields = dict(fields)
    if "ts" not in fields or not fields["ts"]:
        fields["ts"] = _now_iso()
    _validate(fields)
    base = Path(log_dir) if log_dir else Path("_logs")
    day = fields["ts"][:10].replace(":", "-")  # 取日期做文件名（ASCII 安全）
    day_file = base / f"{day}.jsonl"
    day_file.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    with day_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return day_file


def summary_line(fields: dict[str, Any]) -> str:
    """返回写进 每日AI操作记录.md 的汇总行：日期|agent|动作|范围|结果|异常|备注。"""
    f = fields
    anomaly = ";".join(f.get("anomaly", [])) or "无"
    note = ";".join(f.get("next", [])) or ""
    return f"{f.get('ts','')[:10]}|{f.get('agent','')}|{f.get('action','')}|" \
           f"{f.get('scope','')}|{f.get('result','')}|{anomaly}|{note}"


if __name__ == "__main__":
    import sys
    # 自检：无参则回显用法；带 --selftest 则跑一遍样例并校验
    if "--selftest" in sys.argv:
        sample = {
            "ts": _now_iso(), "agent": "@经验飞轮", "action": "sweep_detect",
            "scope": "selftest", "input": {}, "result": "ok", "verdict": "ok",
            "anomaly": [], "writeback": [], "error": None, "ref": "selftest", "next": [],
        }
        _validate(sample)
        print("log.emit selftest ok")
        sys.exit(0)
    print("log.emit(fields) — 字段见模块 docstring; --selftest 自检")
    sys.exit(0)
