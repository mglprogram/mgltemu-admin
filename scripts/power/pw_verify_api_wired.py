# -*- coding: utf-8 -*-
"""验证整链里的帕沃尔段全部走接口（`run_pw_fulfill.py --stage ...`）。

检查点（只读，不真跑整链）：
  ① 9 个步骤的接口版函数都在
  ② 开关默认值都是 `1`（接口版）
  ③ `PWApi` 各动作能真实返回
"""
import inspect
import os
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import run_pw_fulfill as R  # noqa: E402

print("=== ① 接口版函数是否就位 ===")
for fn in ("_update_orders_via_api", "_set_wh_via_api", "_hwc_order_via_api",
           "_fail_fix_via_api", "_pw_receive_via_api", "_pw_track_via_api",
           "_pw_api"):
    print("  %-26s %s" % (fn, "✅" if callable(getattr(R, fn, None)) else "❌"))

print("\n=== ② 各步骤走哪个分支（读源码） ===")
for step in ("step_update_orders", "step_set_wh", "step_hwc_order",
             "step_fail_fix", "step_pw_receive", "step_pw_track"):
    f = getattr(R, step, None)
    if not f:
        print("  %-22s ❌ 不存在" % step)
        continue
    src = inspect.getsource(f)
    api = "VIA_API" in src
    print("  %-22s %s" % (step, "✅ 接口优先" if api else "⚠ 仍 DOM"))

print("\n=== ③ 环境变量默认值 ===")
for k in ("TEMU_PW_ALL_VIA_API", "TEMU_PW_SYNC_VIA_API", "TEMU_PW_SHIP_VIA_API",
          "TEMU_PW_FIX_VIA_API"):
    print("  %-24s %s" % (k, os.environ.get(k, "(未设→默认1)")))

print("\n=== ④ PWApi 实测 ===")
import pw_api as A  # noqa: E402
api = A.PWApi().login()
print("  state:", api.state("GSU1AX42B0023LC"))
print("  pending pb=1:", len(api.pending("1")), " pb=2:", len(api.pending("2")))
print("  shops:", len(api.shop_map()))
