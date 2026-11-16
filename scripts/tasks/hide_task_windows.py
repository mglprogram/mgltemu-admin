# -*- coding: utf-8 -*-
"""把「会弹黑框」的计划任务统一改成 wscript 隐藏包装（run_hidden.vbs）。

为什么要这个工具（2026-11-16 又来一次）：
  · 计划任务里 `Settings.Hidden=True` **不会**隐藏控制台窗口，它只让任务在任务计划程序
    列表里不显示；只要 Action 的 Execute 是控制台程序（cmd / powershell / python /
    *.cmd / *.bat），每次触发就会闪一个黑框。
  · 本项目的标准做法：Execute = `wscript.exe`，
    Arguments = `//nologo <run_hidden.vbs 路径> <cmd /c 真实命令行>`（各层用双引号包住）。
    WScript.Shell.Run(cmd, 0, True) 既隐藏窗口**又保留真实退出码**（实测 exit /b 7 → 7）。
  · 新任务常常是临时手搓的（例如某次 one-shot 排障），忘了套这层，于是「又弹窗」。
    本工具 = 一次扫描 + 一次修正 + 可重复执行的体检。

用法：
  python scripts/tasks/hide_task_windows.py             # 只体检（默认 dry-run）
  python scripts/tasks/hide_task_windows.py --apply     # 真正改写会弹框的任务
  python scripts/tasks/hide_task_windows.py --json      # 机器可读
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

VBS = r"C:\Users\saing\.agents\skills\scheduled-tasks\scripts\run_hidden.vbs"

#: 会开控制台窗口的可执行文件（按 basename 比对，不含 .exe 后缀也认）
CONSOLE_EXES = {
    "cmd", "powershell", "pwsh", "python", "python3", "py",
    "cscript", "node", "npx", "npm", "java", "perl", "ruby",
}
#: 这些后缀本身就是控制台脚本
CONSOLE_EXT = (".cmd", ".bat", ".ps1", ".vbs")

SCAN_PS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$out = foreach ($t in (Get-ScheduledTask)) {
  $p = [string]$t.TaskPath
  if ($p -like '\Microsoft\*') { continue }
  foreach ($a in $t.Actions) {
    [pscustomobject]@{
      Name    = [string]$t.TaskName
      Path    = $p
      Exec    = [string]$a.Execute
      Args    = [string]$a.Arguments
      Logon   = [string]$t.Principal.LogonType
      State   = [string]$t.State
    }
  }
}
$out | ConvertTo-Json -Compress -Depth 3
"""

REWRAP_PS = r"""
$name = $env:HIDE_TASK_NAME
$arg  = $env:HIDE_TASK_ARG
$t = Get-ScheduledTask -TaskName $name -ErrorAction Stop
if ($t.Actions.Count -ne 1) { throw "多 Action，需人工处理：$name" }
$new = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $arg
Set-ScheduledTask -TaskName $name -Action $new | Out-Null
$r = Get-ScheduledTask -TaskName $name
[pscustomobject]@{ Name = $name; Exec = [string]$r.Actions[0].Execute; Args = [string]$r.Actions[0].Arguments } |
  ConvertTo-Json -Compress
"""


def build_arg(exec_path: str, args: str) -> str:
    """拼出隐藏包装的 Arguments（不写在 PS 里，免得三重引号打架）。"""
    exe = (exec_path or "").strip().strip('"')
    inner = ('"%s" %s' % (exe, (args or "").strip())).strip()
    return '//nologo "%s" "cmd /c ""%s"""' % (VBS, inner)


def run_ps(script: str, *args: str, timeout: int = 120) -> str:
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
    cmd += list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "").strip()[:500])
    return (p.stdout or "").strip()


def scan() -> list:
    raw = run_ps(SCAN_PS)
    if not raw:
        return []
    data = json.loads(raw)
    return data if isinstance(data, list) else [data]


def is_console(exec_path: str) -> bool:
    p = (exec_path or "").strip().strip('"').lower()
    if not p:
        return False
    base = p.replace("/", "\\").rsplit("\\", 1)[-1]
    if base.endswith(".exe"):
        base = base[:-4]
    if base in ("wscript", "mshta"):          # 已经是隐藏包装/无需控制台
        return False
    if (exec_path or "").strip('"').lower().endswith(CONSOLE_EXT):
        return True
    return base in CONSOLE_EXES


def risky(tasks: list) -> list:
    out = []
    for t in tasks:
        if t.get("Logon") != "Interactive":
            continue                          # 非交互（服务账户）本来就不显示窗口
        if str(t.get("State") or "") == "Disabled":
            continue                          # 停用的任务不会触发，也就不会弹
        if is_console(t.get("Exec") or ""):
            out.append(t)
    return out


def rewrap(name: str) -> dict:
    # 目标任务的原始 Action 从 scan 结果里拿（避免在 PS 里再拼一次引号）
    t = next((x for x in scan() if x.get("Name") == name), None)
    if not t:
        raise RuntimeError("任务不存在：%s" % name)
    import os
    env = dict(os.environ)
    env["HIDE_TASK_NAME"] = name
    env["HIDE_TASK_ARG"] = build_arg(t.get("Exec") or "", t.get("Args") or "")
    p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", REWRAP_PS],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "").strip()[:400])
    raw = (p.stdout or "").strip()
    return json.loads(raw) if raw else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的改写（默认只体检）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--delete-dead", action="append", default=[],
                    metavar="TASK", help="顺手删掉指定的已失效任务（如指向已删除 .cmd 的 one-shot）")
    a = ap.parse_args()

    tasks = scan()
    bad = risky(tasks)

    deleted = []
    for name in a.delete_dead:
        try:
            subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"],
                           capture_output=True, text=True, timeout=60, check=False)
            deleted.append(name)
        except Exception as e:  # noqa: BLE001
            print("[warn] 删除失败 %s: %s" % (name, e), file=sys.stderr)

    results = []
    if a.apply:
        for t in bad:
            try:
                results.append(rewrap(t["Name"]))
            except Exception as e:  # noqa: BLE001
                results.append({"Name": t["Name"], "error": str(e)[:300]})

    if a.json:
        print(json.dumps({"risky": bad, "rewrapped": results, "deleted": deleted},
                         ensure_ascii=False, indent=1))
        return 0

    if deleted:
        print("[删任务] " + ", ".join(deleted))
    if not bad:
        print("[OK] 没有会弹黑框的计划任务")
        return 0
    print("[会弹黑框] %d 个：" % len(bad))
    for t in bad:
        print("  - %s | %s %s" % (t["Name"], t["Exec"], (t["Args"] or "")[:90]))
    if not a.apply:
        print("（只体检；加 --apply 改写为 wscript + run_hidden.vbs）")
        return 0
    print("[已改写]")
    for r in results:
        if r.get("error"):
            print("  [fail] %s：%s" % (r["Name"], r["error"]))
        else:
            print("  [ok] %s → %s %s" % (r["Name"], r["Exec"], (r["Args"] or "")[:90]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
