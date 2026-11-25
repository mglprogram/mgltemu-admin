# -*- coding: utf-8 -*-
"""开窗 + 授权（BEL / KLS 现在都关着）。

顺序按紫鸟 skill 铁律：**先开窗，再授权**（`run_temu_auth.py` 自己不开窗）。
"""
import subprocess
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ziniao_store as zs  # noqa: E402

PY = sys.executable
AUTH = r"D:\AI-workplace\TEMU\scripts\power\run_temu_auth.py"

TARGETS = [("BEL", "TM-BEL-MGL", "27677489075954"),
           ("KinglamanSport", "TM-KinglamanSport", "27364108847582")]


def ensure_window(win: str, sid: str) -> int:
    port = zs.probe_port_by_window(win)
    if port:
        print("  %s 已开，端口 %s" % (win, port))
        return int(port)
    print("  %s 未开 → 打开（storeId=%s）" % (win, sid))
    try:
        zs.open_store(int(sid))
    except Exception as ex:  # noqa: BLE001
        print("    开窗失败:", str(ex)[:120])
        return 0
    for _ in range(40):
        time.sleep(3)
        port = zs.probe_port_by_window(win)
        if port:
            print("    端口 %s" % port)
            return int(port)
    print("    40 次探测仍未拿到端口")
    return 0


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for name, win, sid in TARGETS:
        if only and only.upper() not in (name.upper(), win.upper()):
            continue
        print("\n=== %s ===" % name)
        port = ensure_window(win, sid)
        if not port:
            print("  跳过授权（无端口）")
            continue
        p = subprocess.run([PY, AUTH, "--store", name, "--domain", "both"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=480)
        for ln in ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-8:]:
            print("   ", ln[:140])
        print("   rc =", p.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
