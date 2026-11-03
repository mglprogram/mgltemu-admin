# -*- coding: utf-8 -*-
"""`scripts/power/tests` 的收集引导：先把 `lib` 钉成 `scripts/lib`（正规包）。

为什么需要（2026-11-03 实测）
--------------------------
本仓库有**两个 `lib`**：

    scripts/lib/            ← 正规包（有 `__init__.py`），含 preflight / quiet / uio
    scripts/power/lib/      ← **没有** `__init__.py`，是命名空间包（域 lib，含 auth_guard 等）

`run_temu_*.py` 里普遍是 `from lib import preflight`。按 import 规则，**正规包优先于
命名空间包**，所以正常情况下命中的是 `scripts/lib`。

但一旦某个测试先在只有 `scripts/power` 在 path 时碰到 `lib`，Python 会把那个
**命名空间包**缓存进 `sys.modules['lib']`（`lib.__file__` 为 None，报错显示
`unknown location`）；之后再往 path 里加 `scripts/` 也**换不回来** ——
表现为整目录收集时报：

    ImportError: cannot import name 'preflight' from 'lib' (unknown location)

⇒ 这里在**任何测试模块导入之前**（conftest 早于测试收集执行）先把 `scripts/`
放到 sys.path 最前，并主动 `import lib` 把正规包钉进 `sys.modules`。
这样一来，无论各测试文件自己再怎么 `sys.path.insert(0, scripts/power)`，
`lib` 都已经指向 `scripts/lib`，不会再被命名空间包顶掉。

> 幂等、无副作用：只动 `sys.path` 与 `sys.modules['lib']`，不碰任何业务状态。
"""
from __future__ import annotations

import os
import sys

_POWER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # scripts/power
_SCRIPTS = os.path.dirname(_POWER)                                     # scripts
_POWER_LIB = os.path.join(_POWER, "lib")

# 1) `scripts/` 放最前：`from lib import ...` 命中正规包 scripts/lib
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
else:
    sys.path.remove(_SCRIPTS)
    sys.path.insert(0, _SCRIPTS)

# 2) 主动导入，把正规包钉进 sys.modules（防止已被缓存成命名空间包）
_pinned = sys.modules.get("lib")
if _pinned is None or not getattr(_pinned, "__file__", None):
    for _k in [k for k in sys.modules if k == "lib" or k.startswith("lib.")]:
        del sys.modules[_k]
    try:
        import lib  # noqa: F401
    except Exception:                                                  # noqa: BLE001
        pass

# 3) 域 lib（power/lib）放最前，供 `import auth_guard` / `import cdp_transport` 等
if _POWER_LIB not in sys.path:
    sys.path.insert(0, _POWER_LIB)
