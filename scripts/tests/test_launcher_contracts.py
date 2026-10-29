#!/usr/bin/env python3
"""钉住「启动器契约」—— 注册表说什么，入口 .cmd 就必须做什么。

2026-10-29 全量审计发现的三个真实缺陷，每一个都**只在出问题那天**才暴露，
平时完全看不出来：

  1. `run_ops_archive_cleanup.cmd` 把 `exit /b %ERRORLEVEL%` 写在
     `call notify_panel.cmd` **之后**。notify_panel 结尾是 `exit /b 0`，
     于是 `%ERRORLEVEL%` 永远读到 0 ⇒ **所有失败都报成功**。
     实测复现：call 一个 exit 3 的桩，再 call notify_panel，%ERRORLEVEL% 变 0。

  2. `TEMU-Goods-OnSale-Daily` 注册表写了 `窗口锁: BHY+BEL`，但
     `run_temu_goods_onsale.cmd` 从来没上过锁 —— 它驱动的正是两扇紫鸟窗口。
     排班错开只是巧合，不是保证。

  3. `TEMU-Pending-Confirm-Daily` 同理（`紫鸟窗口: BHY+BEL` 却无锁）。

跑法：
    pytest scripts/tests/test_launcher_contracts.py -q
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
from pathlib import Path

PROJ = Path(r"D:\AI-workplace\TEMU")
TASKS_DIR = PROJ / "scripts" / "tasks"
REGISTRY = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"

#: 这些启动器不在本仓库（另一个 skill 目录），单独列出以便一起校验
EXTERNAL_LAUNCHERS = {
    "TEMU-Pending-Confirm-Daily":
        Path.home() / ".workbuddy" / "skills" / "temu-pending-confirm" / "run-pending-confirm.cmd",
}


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8-sig"))


def _launcher_text(task: dict) -> tuple[str, str]:
    """→ (启动器路径, 全文)。找不到返回 ("", "")。"""
    name = task.get("name") or ""
    if name in EXTERNAL_LAUNCHERS:
        p = EXTERNAL_LAUNCHERS[name]
        return (str(p), p.read_text(encoding="utf-8", errors="replace")) if p.is_file() else ("", "")
    inner = (task.get("dsh") or {}).get("inner") or ""
    if inner:
        p = PROJ / inner
        if p.is_file():
            return str(p), p.read_text(encoding="utf-8", errors="replace")
    args = (task.get("action") or {}).get("arguments") or ""
    for m in re.finditer(r"([A-Za-z]:\\[^\"]*?\.(?:cmd|bat))", args.replace('""', '"')):
        p = Path(m.group(1))
        if p.is_file():
            return str(p), p.read_text(encoding="utf-8", errors="replace")
    return "", ""


# ---------------------------------------------------------------- 缺陷 1
def test_no_launcher_reads_errorlevel_after_notify_panel():
    """`call notify_panel.cmd` 之后不能再用 `%ERRORLEVEL%`。

    notify_panel.cmd 的契约是「永远 rc=0、永远不阻塞」（它是各任务的收尾钩子，
    不能把业务退出码带坏）。所以它之后 `%ERRORLEVEL%` 恒为 0 —— 谁在那之后
    `exit /b %ERRORLEVEL%`，谁就把**所有失败都变成成功**。
    """
    bad = []
    files = sorted(TASKS_DIR.glob("*.cmd"))
    files += [p for p in EXTERNAL_LAUNCHERS.values() if p.is_file()]
    for p in files:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        idx = [i for i, l in enumerate(lines)
               if "notify_panel" in l and l.strip().lower().startswith("call")]
        if not idx:
            continue
        after = lines[idx[-1] + 1:]
        for l in after:
            if "%ERRORLEVEL%" in l.upper():
                bad.append(f"{p.name}: {l.strip()}")
    assert not bad, (
        "这些启动器在 notify_panel 之后读 %ERRORLEVEL%（恒为 0 ⇒ 失败会报成功）：\n  "
        + "\n  ".join(bad)
        + "\n⇒ 在 call 之前 `set \"RC=%ERRORLEVEL%\"`，最后 `exit /b %RC%`"
    )


# ---------------------------------------------------------------- 缺陷 2 / 3
def test_declared_window_lock_is_actually_taken():
    """注册表声明了 `窗口锁` 的任务，入口里必须真的调用锁包装器。

    2026-10-29 实测：`TEMU-Goods-OnSale-Daily` 与 `TEMU-Pending-Confirm-Daily`
    都声明用 BHY+BEL 窗口，但入口从没上过锁。窗口只有一个，双跑会互相读错列表、
    互相清障（2026-09-28 买面单域实测 47 次跑 / 24 次失败，其中 8 次是抢窗口）。
    """
    bad = []
    for t in _registry().get("tasks") or []:
        lock = (t.get("窗口锁") or "").strip()
        if not lock:
            continue
        path, txt = _launcher_text(t)
        if not path:
            bad.append(f"{t.get('name')}: 找不到启动器，无法校验")
            continue
        if "run_with_window_lock" not in txt and "window_lock" not in txt:
            bad.append(f"{t.get('name')} [{Path(path).name}]: 声明了锁 {lock} 但入口没上锁")
    assert not bad, "注册表与入口不一致：\n  " + "\n  ".join(bad)


def test_window_lock_action_is_mapped_to_its_task():
    """每个 `--action X` 都要在 window_lock 的映射表里，否则孤儿判据取错超时。

    `_task_timeout_minutes(action)` 用显式映射表查该任务自己的
    `ExecutionTimeLimit`；查不到就退回**全局最小超时**，于是 180 分钟的
    pending-confirm 会被当成 20 分钟 ⇒ 它还在跑，锁就被判陈旧并被别人接管。
    """
    import sys
    sys.path.insert(0, str(PROJ / "scripts"))
    import window_lock as W

    used = set()
    files = sorted(TASKS_DIR.glob("*.cmd"))
    files += [p for p in EXTERNAL_LAUNCHERS.values() if p.is_file()]
    for p in files:
        txt = p.read_text(encoding="utf-8", errors="replace")
        used.update(re.findall(r"--action\s+\"?([A-Za-z0-9_-]+)\"?", txt))

    names = {t.get("name") for t in _registry().get("tasks") or []}
    bad = []
    for a in sorted(used):
        # 直接读映射表，别调函数：函数查不到会静默退回最小值
        try:
            mapped = W._task_timeout_minutes(a)
        except Exception as exc:                             # noqa: BLE001
            bad.append(f"{a}: {exc}")
            continue
        # 用真实任务名反查，确认映射表里确实有它
        src = io.open(W.__file__, encoding="utf-8").read()
        if f'"{a}"' not in src:
            bad.append(f"{a}: 不在 _ACTION_TO_TASK 里（会退回全局最小超时 {mapped} 分钟）")
        elif mapped <= 0:
            bad.append(f"{a}: 查到 {mapped} 分钟")
    assert not bad, "窗口锁动作名映射有问题：\n  " + "\n  ".join(bad)


# ---------------------------------------------------------------- 超时契约
def test_agent_budget_is_always_under_the_windows_limit():
    """`agent_budget < ExecutionTimeLimit`，且 `ExecutionTimeLimit > 实测最坏`。

    两条都是 2026-10-29 两次假告警的直接原因：
      · 预算 > 外层 ⇒ agent 永远没机会写 verdict 就被杀（记 267014）
      · 外层 < 最坏耗时 ⇒ 恢复流程没跑完就被杀（同样记 267014）
    """
    import sys
    sys.path.insert(0, str(PROJ / "scripts"))
    import dsh_task as dt

    bad = []
    for t in _registry().get("tasks") or []:
        if not (t.get("dsh") or {}).get("enabled"):
            continue
        outer = int(t.get("timeout_minutes") or 30)
        budget = dt.agent_budget_seconds(outer)
        if budget >= outer * 60:
            bad.append(f"{t.get('name')}: 预算 {budget}s ≥ 外层 {outer} 分钟")
        worst = _worst_seconds(t.get("name") or "")
        if worst is not None and worst >= outer * 60:
            bad.append(
                f"{t.get('name')}: 实测最坏 {worst/60:.1f} 分钟 ≥ 外层 {outer} 分钟")
    assert not bad, "超时契约被破坏：\n  " + "\n  ".join(bad)


def _worst_seconds(name: str):
    """从 dsh-runner 日志取该任务最坏一次 agent 耗时（秒）；无记录返回 None。"""
    p = PROJ / "_logs" / "dsh-runner" / (name + ".log")
    if not p.is_file():
        return None
    txt = p.read_text(encoding="utf-8", errors="replace")
    secs = [float(m) for m in re.findall(r"elapsed=([0-9.]+)s", txt)]
    return max(secs) if secs else None


# ---------------------------------------------------------------- 通用卫生
def test_launchers_are_ascii_and_crlf():
    """`scripts\\tasks\\*.cmd` 必须纯 ASCII + CRLF。

    cmd.exe 按 OEM 代码页（这里是 GBK）读 .cmd，中文注释会破坏语法；
    LF-only 的文件在某些 cmd 版本里会把行尾并进命令。
    """
    bad = []
    for p in sorted(TASKS_DIR.glob("*.cmd")):
        raw = p.read_bytes()
        nonascii = sum(1 for b in raw if b > 127)
        if nonascii:
            bad.append(f"{p.name}: 非 ASCII 字节 {nonascii}")
        if b"\n" in raw and raw.count(b"\r\n") != raw.count(b"\n"):
            bad.append(f"{p.name}: 混用 CRLF/LF")
    assert not bad, "\n  ".join(bad)


def test_every_launcher_has_a_panel_hook_or_a_documented_exception():
    """跑完要刷面板；确实不该刷的要写明理由。

    `TEMU-TaskWatch-30min` 故意不挂钩子：watch_tasks.py 自己按「有新异常 +
    冷却」刷，无条件挂钩子会让它每 30 分钟发一次（实测 87/135 轮 = 64% 的
    部署来自 task-watch，每天约 48 次无意义部署）。
    `Ops-Daily-Digest` / `TEMU-Stock-Track-Daily` 是只读任务，不产生面板数据。
    """
    allowed = {"run_ops_task_watch.cmd", "run_meow_daily_digest.cmd",
               "run_temu_stock_track.cmd", "run_via_dsh.cmd", "notify_panel.cmd",
               "run_temu_goods_topup.cmd"}
    bad = []
    for p in sorted(TASKS_DIR.glob("*.cmd")):
        if p.name in allowed:
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        if "notify_panel" not in txt:
            bad.append(p.name)
    assert not bad, "这些启动器没有 notify_panel 钩子，也没写进允许清单：\n  " + "\n  ".join(bad)
