# -*- coding: utf-8 -*-
"""打印某个定时任务的 `dsh.inner`（入口脚本相对路径）。

为什么单独一个文件，而不是在 .cmd 里写 `python -c "..."`：
----------------------------------------------------------
`run_via_dsh.cmd` 的 kill-switch 路径要用 `for /f ... in (`...`)` 取回结果。
那里如果写成 `for /f ... in (`"%PY%" -c "import ..."`)`，交给 cmd /c 的命令行是

    "C:\\...\\python.exe" -c "import sys; ..."

**以引号开头、且总引号数 > 2** ⇒ cmd /c 会剥掉首尾两个引号，剩下

    C:\\...\\python.exe" -c "import ...

于是报 `'C:\\...\\python.exe" -c "import' is not recognized`，INNER 永远取不到，
kill switch 直接 exit /b 90。

这个 bug 从 kill switch 上线起就存在，直到 2026-10-28 手工跑一次才暴露
（平时走 agent 路径不经过这里，所以一直没被发现）。

改成调用一个**文件**，命令行以非引号字符开头，cmd /c 不再做剥离：

    for /f "usebackq delims=" %%A in (`%PY% "%~dp0_inner_of.py" "%TASK%"`) do ...

用法：
    python scripts/tasks/_inner_of.py TEMU-Goods-DailyList
    → scripts\\tasks\\run_temu_goods_topup.cmd
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]


def main() -> int:
    if len(sys.argv) < 2:
        print("", end="")
        return 0
    task = sys.argv[1]

    sys.path.insert(0, str(PROJ / "scripts"))
    try:
        import dsh_task  # noqa: PLC0415
        entry = dsh_task.load_entry(task) or {}
    except Exception:  # noqa: BLE001
        print("", end="")
        return 0

    inner = (entry.get("dsh") or {}).get("inner") or ""
    print(inner, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
