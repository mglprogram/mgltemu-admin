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
from datetime import datetime
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

#: 「某店失败」的汇总行（run_overdue_daily.cmd 收尾写 `[END] ... BHY=0 BEL=5`）。
#: 这类行被 _NOISE 的 `\[?END\]?` 规则滤掉了，但它恰恰是唯一能看出「哪个店、什么码」的线索，
#: 所以在扫原因前**先单独捞一次**（2026-10-26 实测：逾期任务只有这种行可读）。
_STORE_SUMMARY = re.compile(r"\b(?:BHY|BEL|BE)\s*=\s*(\d+)", re.I)


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

    # 先捞「哪个店失败」的汇总行（它会被 _NOISE 滤掉，但信息量最高）
    store_hit = ""
    for line in reversed(cands):
        m = _STORE_SUMMARY.search(line)
        if m and any(g != "0" for g in _STORE_SUMMARY.findall(line)):
            store_hit = line
            break

    cands = [x for x in cands if x and not any(n.search(x) for n in _NOISE)]
    if not cands:
        return _tidy(store_hit)[:max_len] if store_hit else ""

    # ★ 只认**最近 24 小时**的行（2026-10-26 实测踩到）：像 WorkBuddy 的 signin.log
    #   是**全量追加**、跨好几天的，尾部 400 行里既有今天的 401，也有 10-12 的老记录，
    #   从后往前扫会抽到**陈年旧行**，把当前失败说成别的样子。
    #   日志行普遍带 `[YYYY-MM-DD HH:MM:SS]`；带日期的行只在日期够新时才算候选。
    #   不带日期的行（多数 .cmd 日志）保持原样，不受影响。
    _STAMP = re.compile(r"\[?(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})")
    _now = datetime.now()

    def _fresh(line: str) -> bool:
        m = _STAMP.search(line)
        if not m:
            return True                                  # 无时间戳 → 不据此排除
        try:
            ts = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          int(m.group(4)), int(m.group(5)))
        except ValueError:
            return True
        return abs((_now - ts).total_seconds()) <= 24 * 3600

    fresh = [x for x in cands if _fresh(x)]
    if fresh:
        cands = fresh

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
    # 没有强特征行时，退回「哪个店失败」的汇总行（逾期任务就靠这个）
    if store_hit:
        return _tidy(store_hit)[:max_len]
    return ""


def agent_verdict(task_name: str, max_age_hours: float = 24.0) -> dict:
    """Read the DSH agent's verdict for this task, if it is recent. Never raises.

    Since 2026-10-26 every migrated task runs through `scripts/dsh_task.py`, which
    makes the agent write `_logs/dsh-sentinel/<task>/verdict.json`. That file
    already contains a one-line Chinese diagnosis written *after* the agent read
    the logs — strictly better than regex-scraping a log tail. Used first; the
    log scrape stays as the fallback (it is still the only source for tasks that
    were never migrated, and for runs where the agent died before writing).
    """
    import time as _time

    path = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"
    sentinel_root = Path(__file__).resolve().parent.parent / "_logs" / "dsh-sentinel"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in task_name) or "task"
    vp = sentinel_root / safe / "verdict.json"
    try:
        if not vp.is_file():
            return {}
        age_h = (_time.time() - vp.stat().st_mtime) / 3600.0
        if age_h > max_age_hours:
            return {}
        # utf-8-sig: an agent writing the file from PowerShell prepends a BOM.
        data = json.loads(io.open(vp, encoding="utf-8-sig", errors="replace").read())
    except Exception:                                        # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def cause_for(task_name: str) -> str:
    """任务名 → 原因一句话。

    Order: the DSH agent's own diagnosis first (it read the logs and said what
    went wrong), then the log scrape. Failure/not-found always returns "".
    """
    try:
        v = agent_verdict(task_name)
        diag = str(v.get("diagnosis") or "").strip()
        # A verdict that says "everything was fine" is not a cause - fall through
        # so a stale/over-optimistic verdict cannot mask a real log error.
        if diag and v.get("rc") not in (0, "0"):
            return _tidy(diag)[:200]
    except Exception:                                        # noqa: BLE001
        pass
    try:
        return last_cause(registry_log_path(task_name))
    except Exception:                                        # noqa: BLE001
        return ""


if __name__ == "__main__":                                    # 手工排查用
    import sys
    for n in sys.argv[1:]:
        print("%-32s %s" % (n, cause_for(n) or "(没找到原因行)"))
