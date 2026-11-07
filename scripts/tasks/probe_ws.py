# -*- coding: utf-8 -*-
"""probe_ws.py — 跑一次真实 DSH 会话，确认它落在「发货」工作区（验收用）。

用户 2026-11-06 的核心要求：「这个定时任务用 dsh 会话激活；激活的会话在
**发货这个工作区**出现」。光看代码不算验过 —— 必须真起一次会话，再去
`~/.dsh/storages/workspace.json` 与 session 缓存里回读它的 cwd。

用法：
    python scripts/tasks/probe_ws.py            # 跑探针并回读工作区
    python scripts/tasks/probe_ws.py --keep     # 保留探针会话（默认也保留，方便人看）
"""
from __future__ import annotations

import glob
import io
import json
import os
import subprocess
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WS = "D:\\AI-workplace\\发货"
TASK = "TEMU-Fulfill-WSProbe"
PROBE = os.path.join(PROJ, "scripts", "tasks", "_ws_probe.py")
DSH_HOME = os.path.join(os.path.expanduser("~"), ".dsh")


def _session_cwds() -> dict:
    """读 session 缓存 → {session_id: cwd}。"""
    out = {}
    root = os.path.join(DSH_HOME, "storages", "session_projcache", "sessions")
    for f in glob.glob(os.path.join(root, "*.json")):
        try:
            with io.open(f, encoding="utf-8-sig") as fh:
                d = json.load(fh)
            cwd = (d.get("record") or {}).get("identity", {}).get("cwd")
            if cwd:
                out[os.path.basename(f)[:-5]] = cwd
        except Exception:  # noqa: BLE001
            pass
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    before = set(_session_cwds())
    print("[probe] 现有会话 %d 条；起一次探针会话，cwd=%s" % (len(before), WS))

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(
        [sys.executable, os.path.join(PROJ, "scripts", "dsh_task.py"),
         "--task", TASK, "--inner", PROBE, "--cwd", WS, "--timeout", "4"],
        cwd=PROJ, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    print("[probe] dsh_task rc=%s" % p.returncode)
    for line in (p.stdout or "").strip().splitlines()[-8:]:
        print("   " + line[:200])
    if p.returncode != 0 and (p.stderr or "").strip():
        for line in (p.stderr or "").strip().splitlines()[-5:]:
            print("   [err] " + line[:200])

    time.sleep(3)
    after = _session_cwds()
    new = {k: v for k, v in after.items() if k not in before}
    print("\n[probe] 新增会话 %d 条：" % len(new))
    for k, v in new.items():
        mark = "✅" if os.path.normcase(v) == os.path.normcase(WS) else "✗"
        print("   %s %s  cwd=%s" % (mark, k[:44], v))

    hit = [k for k, v in new.items()
           if os.path.normcase(v) == os.path.normcase(WS)]
    print("\n[RESULT] " + json.dumps(
        {"new_sessions": len(new), "in_shipping_workspace": len(hit),
         "ok": bool(hit)}, ensure_ascii=False))
    return 0 if hit else 42


if __name__ == "__main__":
    raise SystemExit(main())
