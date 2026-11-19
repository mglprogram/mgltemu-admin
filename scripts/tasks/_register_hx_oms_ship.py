# -*- coding: utf-8 -*-
"""把 hx_oms_ship.py 登记进 test_auth_guard_coverage 的 EXCLUDED（并写清理由）。

为什么不加 guard：它不是独立入口，是 `run_hx_fulfill.py` import 进来的实现模块，
登录态由链路头部统一兜底（`oms_ship_one.py` 是同一个模式，先例已在 EXCLUDED 里）。
用法：python scripts/tasks/_register_hx_oms_ship.py [--apply]
"""
import io
import sys

P = r"D:\AI-workplace\TEMU\scripts\tests\test_auth_guard_coverage.py"
KEY = '"scripts/power/oms_ship_one.py":'
NEW = ('    # 2026-11-12：`hx_oms_ship.py` 是**实现模块**（不是独立入口），由\n'
       '    # `run_hx_fulfill.py` import 使用；登录态在链路头部统一兜底，\n'
       '    # 与 `oms_ship_one.py` 同一个模式。\n'
       '    "scripts/power/hx_oms_ship.py": "领星发货实现模块（被 run_hx_fulfill import），'
       '链头已统一兜底登录态",\n')

lines = io.open(P, encoding="utf-8").read().splitlines(keepends=True)
if any('"scripts/power/hx_oms_ship.py"' in ln for ln in lines):
    raise SystemExit("已登记过，不重复改")

idx = None
for i, ln in enumerate(lines):
    if KEY in ln:
        idx = i
        break
if idx is None:
    raise SystemExit("找不到锚点行 oms_ship_one.py")

lines.insert(idx + 1, NEW)
out = "".join(lines)
compile(out, P, "exec")
if "--apply" in sys.argv:
    io.open(P, "w", encoding="utf-8", newline="\n").write(out)
    print("已写入")
else:
    print("预览（加 --apply 生效）")
