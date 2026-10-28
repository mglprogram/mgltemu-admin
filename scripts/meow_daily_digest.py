# -*- coding: utf-8 -*-
r"""meow_daily_digest.py — 每天 20:00 把「今天所有定时任务的结果」压成一条消息推到手机。

为什么要它（2026-10-28 用户要求）
---------------------------------
失败告警由 `watch_tasks.py` 实时推（每 30 分钟巡检一次，有异常才发）。
但"一切正常"的日子，手机上什么都不来 —— 用户想每天有个底：今天 20 多个任务
到底跑成了几个、有没有悄悄失败、有没有该跑没跑的。

口径（**复用 manage.py，别另起一套**）
------------------------------------
`C:\Users\saing\.agents\skills\scheduled-tasks\scripts\manage.py` 是注册表 + 返回码
人话的**唯一实现**（`rc_text()` / `rc_is_bad()`，尊重每个任务的 `ok_results`）。
这里直接 import 它，而不是自己再写一遍返回码表 —— SKILL.md 记着「三方口径必须一致」，
自写一份就会变成第四方，迟早一边说正常一边报警。

分类
----
正常 / 告警（ok_results 放行的非零码，如限流监控的 1）/ 失败 / 今日未运行 / 停用。
只有后三类（+告警）会逐条列出并附原因，正常的只给个数 —— 手机上别刷屏。

用法
----
    python scripts\meow_daily_digest.py            # 真发
    python scripts\meow_daily_digest.py --dry-run  # 只打印，不发
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_SCRIPTS = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "scripts"

sys.path.insert(0, str(HERE))            # meow_notify / log_cause
sys.path.insert(0, str(SKILL_SCRIPTS))   # manage（注册表 + 返回码口径）

import log_cause      # noqa: E402
import manage         # noqa: E402
import meow_notify    # noqa: E402

#: 手机通知点开 → 自动化面板
PANEL_URL = "https://mgltemu.us.ci/#/automation/index"

#: 这些返回码不是失败：正在跑 / 尚未运行过 / 就绪
RUNNING_CODES = {267009, 267008}


def is_daily(entry: dict) -> bool:
    """每天跑的任务才判「今日未运行」；周更/工作日跑的不能按天判（否则天天误报）。"""
    sch = entry.get("schedule") or {}
    return bool(sch.get("daily"))


def classify(entry: dict, act: dict | None) -> tuple[str, str]:
    """→ (状态, 说明)。状态 ∈ ok / alert / fail / todo / off / missing。"""
    rc = None if act is None else act.get("rc")
    if act is None:
        return "missing", "计划任务不存在"
    if "disabled" in str(act.get("state", "")).lower():
        return "off", "已停用"
    try:
        rc_i = int(rc)
    except (TypeError, ValueError):
        return "fail", "结果码异常：%r" % (rc,)
    if rc_i in RUNNING_CODES:
        return "ok", "运行中/就绪"
    text = manage.rc_text(rc_i, entry)
    if not manage.rc_is_bad(rc_i, entry):
        return ("alert" if text.startswith("告警") else "ok"), text
    return "fail", text


def build_message() -> tuple[str, dict]:
    reg = manage.load_registry()
    entries = [e for e in reg.get("tasks", []) if e.get("managed", True)]
    actual = manage.actual_tasks()
    now = datetime.now()
    today = now.date().isoformat()

    # 每条存 (label, name, detail, 最近运行时间)；正常/停用只计数，不逐条列
    buckets: dict[str, list] = {"ok": [], "alert": [], "fail": [],
                                "todo": [], "off": [], "missing": []}
    for e in entries:
        name = e.get("name") or ""
        act = actual.get(name)
        status, detail = classify(e, act)
        last = str((act or {}).get("last") or "")
        # 「已登记但今天没跑」再单独看一眼：只对每天跑的任务判（周更别按天判）
        if status in ("ok", "alert") and is_daily(e) and act and last \
                and not last.startswith(today):
            status, detail = "todo", "今天没跑（最近 %s）" % last
        buckets[status].append((e.get("label") or name, name, detail, last))

    total = len(entries)
    lines = ["定时任务日报 %s" % now.strftime("%m-%d %H:%M"),
             "共 %d 个：正常 %d · 告警 %d · 失败 %d · 未运行 %d · 停用 %d%s"
             % (total, len(buckets["ok"]), len(buckets["alert"]),
                len(buckets["fail"]), len(buckets["todo"]), len(buckets["off"]),
                (" · 缺任务 %d" % len(buckets["missing"])) if buckets["missing"] else "")]

    def block(title: str, items: list, with_cause: bool = False) -> None:
        if not items:
            return
        lines.append("")
        lines.append(title)
        for label, name, detail, last in items:
            # 从未运行过时 Task Scheduler 的 LastRunTime 是 1999-11-30，
            # 直接截 "11-30" 会被读成 11 月 30 日 —— 这种就干脆不出时间。
            stamp = ""
            if last and not last.startswith("1999"):
                stamp = "（%s）" % last[5:]
            lines.append("· %s — %s%s" % (label, detail, stamp))
            if with_cause:
                cause = (log_cause.cause_for(name) or "").strip()
                if cause:
                    lines.append("  ↳ %s" % cause[:120])

    # 今天的失败 / 告警 与「既往异常（非今日）」分开 —— 周更任务上周失败，
    # 混进今天的日报告诉用户"今天失败了 4 个"，是在制造焦虑而不是报事实。
    today_fail = [x for x in buckets["fail"] if x[3].startswith(today)]
    past_fail = [x for x in buckets["fail"] if not x[3].startswith(today)]
    today_alert = [x for x in buckets["alert"] if x[3].startswith(today)]
    past_alert = [x for x in buckets["alert"] if not x[3].startswith(today)]

    block("❌ 今日失败", today_fail, with_cause=True)
    block("⏰ 今日未运行", buckets["todo"])
    block("⚠ 今日告警（非故障）", today_alert)
    block("🕘 既往异常（非今日，供参考）", past_fail + past_alert, with_cause=True)
    block("🔕 已停用", buckets["off"])
    block("❓ 注册表有、机器上没有", buckets["missing"])

    if not today_fail and not buckets["todo"] and not buckets["missing"]:
        lines.append("")
        lines.append("✅ 今天没有失败，也没有漏跑。")

    msg = "\n".join(lines)
    return msg, {k: len(v) for k, v in buckets.items()}


def main() -> int:
    dry = "--dry-run" in sys.argv
    try:
        msg, counts = build_message()
    except Exception as e:                                     # noqa: BLE001
        print("[digest] 汇总失败：%s: %s" % (type(e).__name__, e))
        # 汇总本身挂了也要让手机知道（否则"没消息"看起来像"一切正常"）
        if not dry:
            meow_notify.send("定时任务日报失败", "汇总脚本自身出错：%s: %s"
                             % (type(e).__name__, str(e)[:200]), url=PANEL_URL)
        return 1

    print(msg)
    print("[digest] 分类计数：%s" % counts)
    if dry:
        print("[digest] --dry-run：未发送")
        return 0

    ok, detail = meow_notify.send("定时任务日报 " + datetime.now().strftime("%m-%d"),
                                  msg, url=PANEL_URL)
    print("[digest] 手机推送:%s %s" % ("OK" if ok else "FAIL", detail))
    return 0 if ok else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
