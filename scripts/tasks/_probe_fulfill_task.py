# -*- coding: utf-8 -*-
"""_probe_fulfill_task.py — 用**真定时任务**验收「会话落在发货工作区」。

做法：临时把注册表里 TEMU-Fulfill-Daily 的 `dsh.inner` 换成探针，跑一次
`schtasks /Run`，回读会话与 workspace.json，然后把 inner 换回业务入口。

⚠ 为什么要换 inner：真 inner（run_fulfill_daily.cmd）会**真下单、真买面单**。
为了看一眼工作区归属而花钱不划算。整条链路（wscript → run_via_dsh →
dsh_task.py → dsh headless → 会话）**完全一样**，只换最后那一步业务。

用法：
    python scripts/tasks/_probe_fulfill_task.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGISTRY = os.path.join(os.path.expanduser("~"), ".agents", "skills",
                        "scheduled-tasks", "tasks.json")
WORKSPACE_JSON = os.path.join(os.path.expanduser("~"), ".dsh", "storages", "workspace.json")
TASK = "TEMU-Fulfill-Daily"
WS = r"D:\AI-workplace\发货"
PROBE = os.path.join(PROJ, "scripts", "tasks", "_inner_probe.py")
REAL_INNER = r"scripts\tasks\run_fulfill_daily.cmd"


def _load() -> dict:
    with io.open(REGISTRY, encoding="utf-8-sig") as fh:
        return json.load(fh)


def _save(reg: dict) -> None:
    with io.open(REGISTRY, "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write(json.dumps(reg, ensure_ascii=False, indent=2) + "\n")


def _entry(reg: dict) -> dict:
    for t in reg.get("tasks") or []:
        if t.get("name") == TASK:
            return t
    raise SystemExit("注册表里没有 %s" % TASK)


def _ship_sessions() -> list:
    with io.open(WORKSPACE_JSON, encoding="utf-8") as fh:
        reg = json.load(fh)
    for w in (reg.get("tables") or {}).get("workspaces", {}).values():
        if os.path.normcase(w.get("path", "")) == os.path.normcase(WS):
            return list(w.get("sessionIds") or [])
    return []


def _session_cwds() -> dict:
    sys.path.insert(0, os.path.join(PROJ, "scripts", "tasks"))
    import probe_ws                                   # noqa: PLC0415
    return probe_ws._session_cwds()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                  # noqa: BLE001
        pass

    reg = _load()
    e = _entry(reg)
    dsh = e.get("dsh") or {}

    ship0 = set(_ship_sessions())
    sess0 = set(_session_cwds())
    print("[probe] 基线：发货工作区 %d 个会话，全机 %d 个" % (len(ship0), len(sess0)))

    bak = REGISTRY + ".bak-probe-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(REGISTRY, bak)
    print("[probe] 注册表已备份：%s" % os.path.basename(bak))

    # 把 inner 临时换成探针
    dsh["inner"] = PROBE
    e["dsh"] = dsh
    _save(reg)
    print("[probe] inner 临时换成探针：%s" % os.path.basename(PROBE))

    try:
        p = subprocess.run(["schtasks", "/Run", "/TN", TASK],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print("[probe] schtasks /Run rc=%s %s"
              % (p.returncode, (p.stdout or "").strip()[:100]))

        # 等会话出现（最多 120s）
        new_hit, t0 = {}, time.time()
        while time.time() - t0 < 120:
            time.sleep(4)
            new = {k: v for k, v in _session_cwds().items() if k not in sess0}
            if new:
                new_hit = new
                print("[probe] 第 %.0f 秒发现新会话" % (time.time() - t0))
                break

        # 再等一会，让插件把它 attach 进去（插件的 attach 是异步的）
        ship1, deadline = set(), time.time() + 60
        while time.time() < deadline:
            ship1 = set(_ship_sessions())
            if ship1 - ship0:
                break
            time.sleep(3)

        added = ship1 - ship0
        print("")
        print("[probe] 新会话 %d 个：" % len(new_hit))
        for k, v in new_hit.items():
            print("   %s  cwd=%s" % (k, v))
        print("[probe] 新增到发货工作区的会话 %d 个：" % len(added))
        for s in sorted(added):
            print("   ✅ %s" % s)

        ok = bool(added) and all(
            os.path.normcase(os.path.abspath(v)) == os.path.normcase(os.path.abspath(WS))
            for v in new_hit.values())
        print("")
        print("[RESULT] " + json.dumps({
            "new_sessions": len(new_hit),
            "attached_to_shipping_workspace": len(added),
            "ok": ok,
        }, ensure_ascii=False))
        return 0 if ok else 42
    finally:
        # 无论成败都换回业务入口
        reg2 = _load()
        e2 = _entry(reg2)
        e2.setdefault("dsh", {})["inner"] = REAL_INNER
        _save(reg2)
        print("[probe] inner 已换回业务入口：%s" % REAL_INNER)


if __name__ == "__main__":
    raise SystemExit(main())
