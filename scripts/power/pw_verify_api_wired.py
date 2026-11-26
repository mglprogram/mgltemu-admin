# -*- coding: utf-8 -*-
"""校验帕沃尔段 **全部 8 步**都是接口优先（含 query / fail_scan）。

2026-11-26 补上 query / fail_scan 之后，这里把检查清单扩到全量，
避免再出现「汇报说 9/9 实际只有 6/8」的情况：
直接检查函数源码里 **第一段可执行逻辑** 是不是 `_xxx_via_api`。
"""
import inspect
import os
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import run_pw_fulfill as R  # noqa: E402

#: 步骤 → 期望调用的接口版函数
EXPECT = {
    "step_update_orders": "_update_orders_via_api",
    "step_query": "_query_via_api",
    "step_set_wh": "_set_wh_via_api",
    "step_hwc_order": "_hwc_order_via_api",
    "step_fail_scan": "_fail_scan_via_api",
    "step_fail_fix": "_fail_fix_via_api",
    "step_pw_receive": "_pw_receive_via_api",
    "step_pw_track": "_pw_track_via_api",
}


def first_gate(src: str) -> str:
    """取函数体里第一个 `if` 段调用的 `_xxx_via_api`（没有返回空串）。"""
    for line in src.splitlines():
        line = line.strip()
        if line.startswith("return _") and "_via_api(" in line:
            return line.split("return ")[1].split("(")[0]
    return ""


def main():
    ok = bad = 0
    print("=== 帕沃尔段 8 步：第一分支是否接口版 ===\n")
    for step, want in EXPECT.items():
        f = getattr(R, step, None)
        if not f:
            print("  ❌ %-20s 函数不存在" % step)
            bad += 1
            continue
        src = inspect.getsource(f)
        got = first_gate(src)
        if got == want:
            print("  ✅ %-20s → %s()" % (step, want))
            ok += 1
        else:
            print("  ❌ %-20s 期望 %s()，实际第一分支 %s"
                  % (step, want, got or "(DOM/未知)"))
            bad += 1

    print("\n=== 开关默认值 ===")
    for k in ("TEMU_PW_ALL_VIA_API", "TEMU_PW_SYNC_VIA_API",
              "TEMU_PW_SHIP_VIA_API", "TEMU_PW_FIX_VIA_API"):
        print("  %-24s %s" % (k, os.environ.get(k, "(未设→默认1)")))

    print("\n结果：接口优先 %d 步 / 未接 %d 步" % (ok, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
