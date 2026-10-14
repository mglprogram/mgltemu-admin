# -*- coding: utf-8 -*-
"""批处理包装脚本的两条硬规矩（2026-10-10 排障立，两次踩到）。

背景
----
这些 `.cmd` 是给 Windows 计划任务调用的（`cmd /c <bat>`）。它们有两个**静默**坑，
出错时不会报错、只会让日志或行为莫名其妙：

1. **必须纯 ASCII**。cmd.exe 按**本地代码页（本机 cp936/GBK）**读文件；UTF-8 中文注释
   会被读成乱码字节，可能破坏解析 —— 症状是 `The syntax of the command is incorrect`
   或 bat 提前退出（`Result=2` 且什么都没做）。
   2026-10-10 **两次**踩到：`run-daily-apply.cmd` 与 `run_overdue_daily.cmd`（后者文件头
   本就写着 "ASCII only on purpose"，仍被中文注释破坏）。

2. **`%VAR%` 不能紧贴 `>>`**。cmd 把 `N>>` 解析为**句柄 N 重定向**：
   `echo exit=%RC%>> "log"` 在 RC=0 时变成 `exit=0>> "log"` ⇒ 数字被吃掉、日志写残。
   实测症状：`[BHY] exit=` / `[BEL] exit=` 为空，而同一行里**不紧贴** `>>` 的变量（`BHY=0`）正常。
   ⇒ 写法固定为 `echo ... %VAR% >> "log"`（**留空格**）。

本测试把这两条钉住，防止以后又在注释/回显里踩回去。
"""
import os
import re

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)                     # scripts/power
_SCRIPTS = os.path.dirname(_POWER)                  # scripts


def _bat_files():
    out = []
    for root, _dirs, files in os.walk(_SCRIPTS):
        for f in files:
            if f.lower().endswith((".cmd", ".bat")):
                out.append(os.path.join(root, f))
    return out


def test_bat_files_exist():
    """先确认真的扫到了文件（防「静默恒绿」：扫不到就永远通过）。"""
    files = _bat_files()
    assert len(files) >= 5, "只扫到 %d 个 .cmd/.bat，路径可能不对：%s" % (len(files), _SCRIPTS)


def test_bat_files_are_ascii_only():
    """.cmd/.bat 必须纯 ASCII（cmd.exe 按 GBK 读，UTF-8 中文会破坏解析）。"""
    bad = []
    for p in _bat_files():
        raw = open(p, "rb").read()
        n = sum(1 for b in raw if b > 127)
        if n:
            bad.append("%s（非 ASCII 字节 %d）" % (os.path.basename(p), n))
    assert not bad, ("批处理含非 ASCII 字符，cmd.exe 按 GBK 读会乱码/报错：\n  "
                     + "\n  ".join(bad)
                     + "\n⇒ 注释与 echo 一律写英文。")


def test_no_variable_adjacent_to_redirect():
    """`%VAR%>>` 不许紧贴（cmd 会把 `0>>` 当句柄重定向，吃掉数字）。"""
    # ⚠ 必须**无空格**才算违规：`%VAR%>>` 才是句柄重定向陷阱；
    #   `%VAR% >>`（有空格）正是我们要的写法，不能误报。
    pat = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%>>")
    bad = []
    for p in _bat_files():
        for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
            s = line.rstrip("\n")
            if s.lstrip().upper().startswith("REM"):
                continue
            if pat.search(s):
                bad.append("%s:%d  %s" % (os.path.basename(p), i, s.strip()[:80]))
    assert not bad, ("`%VAR%` 紧贴 `>>` —— 变量为数字时 cmd 会当句柄重定向，日志会被写残：\n  "
                     + "\n  ".join(bad)
                     + "\n⇒ 改成 `echo ... %VAR% >> \"log\"`（留一个空格）。")
