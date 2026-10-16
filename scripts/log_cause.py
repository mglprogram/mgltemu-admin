# -*- coding: utf-8 -*-
"""log_cause.py — 从任务的日志里**抽出真正的失败原因**（一句话）。

为什么需要它（2026-10-16）：
    飞书告警和面板目前只会说「结果码：1（脚本自报失败）」/「需看日志」——
    **等于没解释**。真实原因（例如 `CERT_HAS_EXPIRED`、`The request to
    Cloudflare's API timed out`）躺在任务的日志文件里，得自己去翻。

    本模块把「翻日志」这一步自动化：给定任务名，查 scheduled-tasks 注册表拿到
    它的 `log` 路径，读尾部若干行，挑出最像"错误"的一行，压成一句话。

用法：

    from log_cause import cause_for
    cause_for("TEMU-OMS-Orders-Daily")     # -> "X [ERROR] The request to Cloudflare's API timed out."  或 ""

刻意保持**无依赖、永不抛异常**：它只服务于"把告警说清楚"，绝不能因为读不到日志
而把告警本身搞崩。
"""
from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path

#: scheduled-tasks 技能里的注册表（任务的 log 路径写在这里）
REGISTRY = (Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json")

#: ANSI 转义（日志里有彩色输出）
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

#: 强特征：命中即认为是"原因行"，按优先级从高到低
_STRONG = (
    re.compile(r"X\s*\[ERROR\]", re.I),
    re.compile(r"CERT_[A-Z_]+", re.I),
    re.compile(r"certificate has expired", re.I),
    re.compile(r"HARD STOP", re.I),
    re.compile(r"已有作业在跑", re.I),
    re.compile(r"\[FAIL\]", re.I),
    re.compile(r"\bError:\s*\S", re.I),
    re.compile(r"timed out", re.I),
    re.compile(r"fetch failed", re.I),
    re.compile(r"Traceback", re.I),
    re.compile(r"refused|ECONNRESET|ETIMEDOUT|ENOTFOUND", re.I),
    re.compile(r"失败", re.I),
)

#: 这些行**不是原因**，是脚本自己的记账（命中就跳过，别把 "publish rc=1" 当原因）
_NOISE = (
    re.compile(r"^\s*(REM|#|;|--)", re.I),
    re.compile(r"\bexit=\d+\s*$"),
    re.compile(r"^\s*\[?(END|START|RUN)\]?", re.I),
    re.compile(r"rc=\d+\s*$", re.I),
    re.compile(r"^\s*=+\s*$"),
)


def _clean(s: str) -> str:
    return _ANSI.sub("", s).strip()


def registry_log_path(task_name: str) -> str:
    """从注册表查该任务的 `log` 路径；查不到返回 ""。"""
    try:
        reg = json.loads(io.open(REGISTRY, encoding="utf-8-sig").read())
    except Exception:                                        # noqa: BLE001
        return ""
    for t in (reg.get("tasks") or []):
        if t.get("name") == task_name:
            return str(t.get("log") or "").strip()
    return ""


def _tidy(s: str) -> str:
    """砍掉无意义的头尾：wrangler 的「Logs were written to ...」、我自己文案里的
    「最后一次输出：」前缀、结尾的全角括号等。"""
    s = re.split(r"\s*🪵|\s*Logs were written to", s)[0]
    s = re.sub(r"^\s*(最后一次输出|最后输出|输出)\s*[:：]\s*", "", s)
    return s.strip().rstrip("）)").strip()


def last_cause(log_path: str, max_lines: int = 400, max_len: int = 160) -> str:
    """在日志尾部找最像"原因"的一行 → 压成一句话（找不到返回 ""）。永不抛异常。

    `log_path` 可以是**文件**，也可以是**目录**（注册表里有些条目只写到目录，
    例如 `TEMU流量加速周更` 写的是 `...\\_logs\\`）—— 目录时取里面最新的 .log。
    """
    if not log_path:
        return ""
    try:
        p = Path(log_path)
        if p.is_dir():
            logs = sorted(p.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
            if not logs:
                return ""
            p = logs[0]
        if not p.is_file():
            return ""
        with io.open(p, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-max_lines:]
    except Exception:                                        # noqa: BLE001
        return ""

    cands = [_clean(x) for x in lines]
    cands = [x for x in cands if x and not any(n.search(x) for n in _NOISE)]
    if not cands:
        return ""

    # 从后往前扫：越靠近末尾越可能是本次失败的原因
    def _best(line: str) -> str:
        """一"行"里可能有多个用 ` | ` 拼起来的片段（panel_refresh 会把几段并成一行）
        —— 挑出真正像报错的那一段，别把整行 300 字塞进告警。"""
        segs = [s.strip() for s in line.split(" | ") if s.strip()]
        for p in _STRONG:
            for s in reversed(segs):
                if p.search(s):
                    return s
        return line

    for pat in _STRONG:
        for line in reversed(cands):
            if pat.search(line):
                return _tidy(_best(line))[:max_len]
    return ""


def cause_for(task_name: str) -> str:
    """任务名 → 原因一句话（查注册表拿 log，再抽）。失败/查不到一律返回 ""。"""
    try:
        return last_cause(registry_log_path(task_name))
    except Exception:                                        # noqa: BLE001
        return ""


if __name__ == "__main__":                                    # 手工排查用
    import sys
    for n in sys.argv[1:]:
        print("%-32s %s" % (n, cause_for(n) or "(没找到原因行)"))
