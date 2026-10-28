# -*- coding: utf-8 -*-
"""校验「定时任务注册表」里写的每个入口都真的存在，且都落在预期位置。

为什么需要它
------------
2026-10-28 把 15 个 `run_*.cmd` 从 `scripts\\` 根目录收进 `scripts\\tasks\\` 时，
注册表里有**三处**都要跟着改，漏一处就只在**下次定时触发**时才炸：

  1. `action.arguments`       —— 计划任务真正执行的命令行
  2. `_原action.arguments`    —— `migrate_registry_to_dsh.py --revert` 用它回滚；
                                 不改就会把任务还原成一个**已经不存在的路径**
  3. `dsh.inner`              —— DSH 会话里跑的入口 + kill switch 兜底

这三处都是字符串，没有任何编译期检查。本测试把它们钉住。

跑法：
    pytest scripts/tests/test_registry_entries.py -q
    python scripts/tests/test_registry_entries.py      # 直接跑也能看报告
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJ = Path(r"D:\AI-workplace\TEMU")
REGISTRY = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"

#: 允许出现在 action 里的可执行文件后缀
EXEC_EXT = (".cmd", ".bat", ".ps1", ".py", ".vbs", ".exe")

#: 2026-10-28 起，定时任务入口统一放在这里
TASKS_DIR = PROJ / "scripts" / "tasks"


def _load() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8-sig"))


def _paths_in(cmdline: str) -> list[str]:
    """从一条命令行里抠出「绝对路径 + 可执行后缀」的片段。

    只认 `X:\\...` 开头的，避免把参数里的相对路径误当文件。
    """
    out = []
    for m in re.finditer(r'[A-Za-z]:\\[^"\'<>|\r\n]*?\.(?:%s)' % "|".join(e.lstrip(".") for e in EXEC_EXT),
                         cmdline or ""):
        p = m.group(0).strip()
        # cmd 的 "" 转义会把引号留在末尾之外，这里已用 [^"] 排除
        out.append(p)
    return out


def _entries() -> list[tuple[str, str, str]]:
    """→ [(任务名, 字段, 路径)]，覆盖 action / _原action / dsh.inner。"""
    rows = []
    for t in _load().get("tasks") or []:
        name = t.get("name", "")
        for field in ("action", "_原action"):
            act = t.get(field) or {}
            for p in _paths_in(act.get("arguments") or ""):
                rows.append((name, field, p))
        inner = (t.get("dsh") or {}).get("inner")
        if inner:
            rows.append((name, "dsh.inner", str(PROJ / inner)))
    return rows


def test_registry_exists():
    assert REGISTRY.exists(), f"注册表不存在：{REGISTRY}"


def test_every_entry_point_exists():
    """注册表里写的路径必须真的存在 —— 否则任务会在触发那一刻静默失败。"""
    missing = [(n, f, p) for n, f, p in _entries() if not Path(p).exists()]
    assert not missing, (
        "注册表指向了不存在的文件（任务会在触发时静默失败）：\n  "
        + "\n  ".join(f"{n} [{f}] -> {p}" for n, f, p in missing)
    )


def test_task_launchers_live_in_tasks_dir():
    """仓库内的定时任务入口必须都在 scripts\\tasks\\ 下。

    允许的例外：`run_via_dsh.cmd` 自己、以及 `notify_panel.cmd`（都是入口层），
    它们本来就在 tasks\\ 里。这里只拦「又有人往 scripts\\ 根目录丢 run_*.cmd」。
    """
    strays = sorted(
        p.name for p in (PROJ / "scripts").glob("run_*.cmd")
    )
    assert not strays, (
        "scripts\\ 根目录又出现了 run_*.cmd，定时任务入口应统一放 scripts\\tasks\\：\n  "
        + "\n  ".join(strays)
    )


def test_no_stale_script_paths_in_registry():
    """本仓库的定时任务入口不该再指向 scripts\\run_xxx.cmd（没有 tasks 这一段）。

    只看 `D:\\AI-workplace\\TEMU\\scripts\\...` 开头的路径 ——
    `D:\\AI-workplace\\DSH\\scripts\\run_wb_sentinel.cmd` 是另一个仓库的文件，
    它的 `scripts\\` 根目录不是我们的问题。
    """
    pat = re.compile(
        r"D:\\AI-workplace\\TEMU\\scripts\\(?!tasks\\)[A-Za-z0-9_]+\.cmd", re.I
    )
    bad = []
    for t in _load().get("tasks") or []:
        for field in ("action", "_原action"):
            blob = (t.get(field) or {}).get("arguments") or ""
            for m in pat.finditer(blob):
                bad.append(f"{t.get('name')} [{field}] -> {m.group(0)}")
        inner = (t.get("dsh") or {}).get("inner") or ""
        for m in pat.finditer(inner):
            bad.append(f"{t.get('name')} [dsh.inner] -> {m.group(0)}")
    assert not bad, "注册表仍指向 scripts\\ 根目录的老入口：\n  " + "\n  ".join(bad)


def test_no_double_backslash_in_orig_action():
    """`_原action` 里不能有 `\\\\`（双反斜杠）。

    2026-10-28 发现 `TEMU-Goods-OnSale-Daily` / `TEMU-Goods-DailyList` 的
    `_原action` 被 JSON 往返编码了两次，路径成了 `D:\\\\AI-workplace\\\\...`。
    它只在 `migrate_registry_to_dsh.py --revert` 时生效 —— 也就是**回滚当天**
    才会炸，而回滚通常发生在 DSH 已经出问题的时候。这条测试把它钉住。
    """
    bad = []
    for t in _load().get("tasks") or []:
        for field in ("action", "_原action"):
            blob = (t.get(field) or {}).get("arguments") or ""
            if "\\\\" in blob:
                bad.append(f"{t.get('name')} [{field}]: {blob[:80]}")
    assert not bad, (
        "注册表里有重复转义的反斜杠（回滚会还原成打不开的路径）：\n  "
        + "\n  ".join(bad)
        + "\n⇒ python scripts/tasks/_fix_orig_action.py --apply"
    )


def test_every_task_has_category():
    """每条任务都要有 category，面板才能分组；值必须在已知集合里。"""
    known = {"temu-store", "panel", "ops", "pw", "reminder", "other"}
    bad = []
    for t in _load().get("tasks") or []:
        c = t.get("category")
        if c not in known:
            bad.append(f"{t.get('name')} -> {c!r}")
    assert not bad, "缺少或非法的 category：\n  " + "\n  ".join(bad)


def test_tasks_dir_scripts_are_ascii():
    """scripts\\tasks\\ 里的 .cmd 必须纯 ASCII（cmd.exe 按 GBK 读）。"""
    bad = []
    for p in TASKS_DIR.glob("*.cmd"):
        n = sum(1 for b in p.read_bytes() if b > 127)
        if n:
            bad.append(f"{p.name}（非 ASCII 字节 {n}）")
    assert not bad, "批处理含非 ASCII 字符：\n  " + "\n  ".join(bad)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = _entries()
    print(f"注册表：{REGISTRY}")
    print(f"待校验入口：{len(rows)} 个\n")
    bad = 0
    for n, f, p in rows:
        ok = Path(p).exists()
        if not ok:
            bad += 1
        print(f"  {'OK ' if ok else 'MISS'} {n:32s} [{f:10s}] {p}")
    print(f"\n{len(rows) - bad} ok, {bad} missing")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
