# -*- coding: utf-8 -*-
"""lib/quiet.py — 子进程**静默起进程**封装（Windows 不弹黑框）。

对治问题（用户 2026-09-12 反馈"中途的 CMD 弹出影响我这边"）：
    DSH / Caddy / 看板执行服务起的父进程**没有控制台**。这时再 spawn 控制台程序
    （python.exe / powershell.exe / node.exe / cmd.exe / *.cmd），Windows 会**给子进程
    新建一个控制台窗口**（黑框一闪或常驻），即使 stdout/stderr 已被重定向到管道也一样。
    带 `CREATE_NO_WINDOW` 才不弹。

用法（改一处就静默一处，别自创 flag 数字）：
    from lib import quiet

    quiet.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    quiet.popen(cmd, cwd=..., stdout=f, stderr=f)          # 需要句柄时
    quiet.hidden_run(["python", "另一个入口.py"], ...)      # 子进程**还会派生子进程**时用这个
    subprocess.run(cmd, creationflags=quiet.NO_WINDOW)     # 想自己传 flags 时

注意：
- `NO_WINDOW` 只对 Windows 有意义，其它平台返回 0，可无脑传。
- **不要用** `DETACHED_PROCESS`（⚠ 2026-10-14 更正，详见 `detached()`）：它的语义是
  "完全没有控制台"，会逼着**子孙**进程新建一个**可见**的控制台 ⇒ 弹黑框；
  且它与 `NO_WINDOW` 同用时 `NO_WINDOW` 会被忽略。要"脱离父进程"就用 `NO_WINDOW`。
- **会再派生**子进程的调用一律用 `hidden_run/hidden_popen`（隐藏控制台，后代继承）；
  只会自己跑完的**叶子进程**才用 `run/popen`。判据与实测三态见下方「隐藏控制台」那段。
- 本模块只做"隐藏窗口"，不改子进程语义（超时/管道/环境变量照旧由调用方决定）。
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

IS_WINDOWS = os.name == "nt"

#: 隐藏控制台窗口（Windows；非 Windows = 0，可无脑传）
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if IS_WINDOWS else 0
#: 后台常驻、脱离父进程（**本身无窗口**；与 NO_WINDOW 互斥）
DETACHED_PROCESS: int = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) if IS_WINDOWS else 0
#: 独立进程组（Ctrl+C 不串到子进程；常与 DETACHED 同用）
NEW_PROCESS_GROUP: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) if IS_WINDOWS else 0

#: `shell=True` 或某些 shell 场景下，creationflags 不够，需再给 STARTUPINFO 兜底
SW_HIDE = 0


def startupinfo() -> Any:
    """`STARTUPINFO(wShowWindow=SW_HIDE)` 兜底（非 Windows 返回 None）。"""
    if not IS_WINDOWS:
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = SW_HIDE
    return si


def detached() -> int:
    """后台常驻进程用的 flags。

    ⚠ **2026-10-14 更正：不要用 `DETACHED_PROCESS`**（本函数旧版返回的就是它）。
    `DETACHED_PROCESS` 的语义是「这个进程**完全没有控制台**」，**不是**「没有窗口」。
    父进程没有控制台时，它的**子孙**进程（node / Playwright driver / 另一个 python）
    会各自新建一个**可见**的控制台窗口 ⇒ 用户看到黑框（2026-10-14 真实报障）。

    回看：本模块 2026-09-22 就记过这个现象，但当时用 python 探针**没复现**，于是保守记成
    "没复现"。2026-10-14 用**带孙进程**的探针稳定复现了 —— 关键：只 spawn 一个"睡着的
    叶子子进程"是复现不出来的，必须有孙辈（对照见下方"隐藏控制台"段）。

    改用 `NO_WINDOW`：它给进程一个**存在但无窗口**的控制台，且该控制台是**新建的、不依赖
    父进程**（所以同样满足"常驻/脱离父进程"），后代全部继承这个隐藏控制台 ⇒ 全链不弹窗。
    另注：`NO_WINDOW` 与 `DETACHED_PROCESS` 同用时会被**忽略**，故两者只能二选一。

    现无任何调用方（已核）；保留此函数只是让"想脱离父进程"的人不必自己拼 flags 拼错。
    """
    if not IS_WINDOWS:
        return 0
    return NEW_PROCESS_GROUP | NO_WINDOW


def _inject(kw: dict) -> dict:
    """给 subprocess 的 kw 补上"不弹窗"（不覆盖调用方显式给的值）。"""
    flags = kw.get("creationflags") or 0
    if IS_WINDOWS and not (flags & DETACHED_PROCESS):
        kw["creationflags"] = flags | NO_WINDOW
        kw.setdefault("startupinfo", startupinfo())
    return kw


# ---------------------------------------------------------------- 隐藏控制台（后代也不弹窗）
# 三种起法的**现场实测**（Windows 11 / 2026-09-22 本机探针，子进程自报 GetConsoleWindow + IsWindowVisible）：
#   hidden_run  → hwnd != 0 且 IsWindowVisible=False  ← 有控制台、不可见（后代可继承，不会被"没有控制台"逼着新建）
#   run         → hwnd == 0                           ← 没有控制台（叶子进程够用）
#   裸 CREATE_NEW_CONSOLE（不带 SW_HIDE） → hwnd != 0 且 IsWindowVisible=True  ← 真的弹黑框
# 来历（2026-09-14 控制面用户报障「B机每次发消息弹终端」）：父进程没有控制台时，它的**后代**
# （node / Playwright driver）会新建可见控制台。该"后代新建"现象 2026-09-22 用 python 探针**没复现**
# （控制台缺失的父进程下，裸 spawn 的孙子 hwnd=0），故本库口径按**保守**记：
# **会再派生**的调用一律 hidden_run（隐藏的控制台能被继承），**叶子进程**才用 run。
#
# ★ 2026-10-14 **复现了**（用户报「为什么又弹出 cmd」）：panel_refresh.py 的后台发布进程用
#   DETACHED_PROCESS 起，链条是 python → publish_admin.py → node/pnpm；用
#   `user32!EnumWindows + IsWindowVisible + GetWindowText` **枚举可见顶层窗口**，当场抓到
#   一个标题为 python.exe 全路径的新窗口（截图与用户报障一致）。探针要带**孙进程**才复现
#   （只 spawn 一个睡着的叶子子进程复现不出来 —— 这正是 09-22 那次漏掉的原因）。
#   ⇒ 上面那条"保守口径"即日起升级为**硬规则**：任何"父进程无控制台"的起法都可能让子孙弹窗，
#     一律用隐藏控制台（`hidden_*`）或 `NO_WINDOW`，**不要用 `DETACHED_PROCESS`**。
def hidden_flags() -> int:
    """隐藏控制台的 creationflags（非 Windows 返回 0，可无脑传）。"""
    if not IS_WINDOWS:
        return 0
    return (getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))


def hidden_startupinfo() -> Any:
    """配合 `hidden_flags()` 的 STARTUPINFO(wShowWindow=SW_HIDE)（非 Windows 返回 None）。"""
    return startupinfo()


def _inject_hidden(kw: dict) -> dict:
    flags = kw.get("creationflags") or 0
    if IS_WINDOWS and not (flags & (DETACHED_PROCESS | getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10))):
        kw["creationflags"] = flags | hidden_flags()
        kw.setdefault("startupinfo", hidden_startupinfo())
    return kw


def hidden_run(cmd, **kw):
    """等价 `subprocess.run`，但子进程带**存在且隐藏**的控制台（后代继承 ⇒ 全链不弹窗）。

    用在"子进程还会再起控制台程序"的场合（调另一个 python 入口 / powershell shim / node）。
    """
    return subprocess.run(cmd, **_inject_hidden(dict(kw)))


def hidden_popen(cmd, **kw):
    """等价 `subprocess.Popen`，同上（隐藏控制台，后代不弹窗）。"""
    return subprocess.Popen(cmd, **_inject_hidden(dict(kw)))


def run(cmd, **kw):
    """等价 `subprocess.run`，只是不弹黑框（**叶子进程**用；会派生后代的改用 hidden_run）。"""
    return subprocess.run(cmd, **_inject(dict(kw)))


def popen(cmd, **kw):
    """等价 `subprocess.Popen`，只是不弹黑框。"""
    return subprocess.Popen(cmd, **_inject(dict(kw)))


def check_output(cmd, **kw):
    """等价 `subprocess.check_output`，只是不弹黑框。"""
    return subprocess.check_output(cmd, **_inject(dict(kw)))
