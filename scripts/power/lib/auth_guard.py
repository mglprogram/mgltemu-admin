# -*- coding: utf-8 -*-
r"""TEMU 域**统一的登录态兜底**（2026-10-10 用户口径）。

背景
----
「窗口重开必掉登录态」——紫鸟店窗口一旦被关掉/重开，TEMU 商家中心（`agentseller-us.temu.com`）
的登录态就是空的，页面停在 `auth/authentication`。此时**单步动作**
（`overdue` / `confirm` / `tracking` / `record` / `widen` …）不会自己补授权，
只会报「订单列表页未就绪：页面停在授权页」，于是**无人值守每天失败**。

实测（2026-10-10）：BHY 窗口关着 → `overdue` 自动开窗成功，但落授权页 → 报错；
手动跑一次 `run_temu_auth.py` 就恢复。**当天 11:00 定时任务失败就是这个原因。**

用户口径
--------
「我认为可以给所有跟 temu 有关的脚本都可以兜底一次 auth。」

做法
----
在**业务动作跑之前**、**端口探测之后**，调一次唯一实现 `run_temu_auth.py --store <店>`：
  · 已登录 → 秒过（输出 `OK: 已登录，无需授权`），**不产生任何多余操作**（幂等）；
  · 未登录 → 自动点「商家中心」→ 勾条款 → 点「授权登录」→ 落到订单页；
  · 仍然失败 → **不阻断**业务（让业务自己报它真实遇到的错），但**如实打日志**。

为什么「不阻断」
    授权失败的原因可能多种（端口/窗口/网络）；业务动作往往能给出更准确的诊断
    （例如「页面停在授权页」比「授权失败」更有信息量）。兜底只负责**尽力恢复**，
    判断仍然交给业务那一步 —— 这符合本域「不猜、不替业务下结论」的一贯口径。

用法（在入口脚本里）
    from lib import auth_guard
    port = auth_guard.ensure_auth(args.store, port=port, log=log, enabled=not args.no_auth_guard)
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
POWER = os.path.dirname(HERE)                       # scripts/power
AUTH_ENTRY = os.path.join(POWER, "run_temu_auth.py")

#: 这些动作**本身就是授权**或**不需要店窗口** ⇒ 不跑兜底（避免自我递归 / 无意义开销）。
SKIP_ACTIONS = ("auth", "plan")


def should_guard(action: str, store: str = "", port: int = 0) -> bool:
    """要不要跑兜底（纯函数，可单测）。"""
    if not store:                 # 没有店 = 没有店窗口要授权
        return False
    if not port:                  # 端口都没有，授权也无从谈起（交给业务报错）
        return False
    if str(action or "") in SKIP_ACTIONS:
        return False
    return True


def ensure_auth(store: str, port: int = 0, log=print, timeout: float = 300.0,
                entry: str = "", extra_args: list | None = None) -> dict:
    """跑一次授权兜底。**永不抛异常**、**永不阻断**调用方。

    `entry` / `extra_args`：授权入口与其附加参数。**默认＝美国站**（`run_temu_auth.py --store X`）。
        ⚠ **授权分域**（skill 口径）：
          · 美国站 `agentseller-us.temu.com`（买面单/导出/逾期确认）→ `run_temu_auth.py`
          · 合作仓 `agentseller.temu.com`（异常出库单）→ `run_temu_abnormal.py --action auth`
        两者是**两套授权**，别拿 A 的入口去修 B 的未授权（2026-09-24 实测：`run_temu_auth.py`
        只会回「已在商家中心页无需授权」而合作仓问题依旧）。
        故调用方按**自己的域**传 entry + extra_args。

    返回 `{ran, ok, skipped, reason, out}`；`ran=False` 表示按判据跳过。
    """
    if not should_guard("__call__", store, port):
        return {"ran": False, "ok": False, "skipped": True,
                "reason": "无店或无端口，跳过授权兜底", "out": ""}

    auth_entry = entry or AUTH_ENTRY
    if not os.path.isfile(auth_entry):
        log("[授权兜底] ⚠ 找不到 %s → 跳过" % auth_entry)
        return {"ran": False, "ok": False, "skipped": True,
                "reason": "auth 入口不存在", "out": ""}

    cmd = [sys.executable, "-u", auth_entry, "--store", str(store)]
    if extra_args:
        cmd += [str(x) for x in extra_args]
    log("[授权兜底] 先跑一次授权（幂等；已登录会秒过）：%s" % " ".join(cmd[1:]))
    try:
        p = subprocess.run(cmd, cwd=POWER, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        rc = p.returncode
    except subprocess.TimeoutExpired:
        log("[授权兜底] ⚠ 超时（%ss）→ 不阻断，交业务自行报错" % int(timeout))
        return {"ran": True, "ok": False, "skipped": False, "reason": "超时", "out": ""}
    except Exception as ex:                                     # noqa: BLE001
        log("[授权兜底] ⚠ 调用失败（%s）→ 不阻断" % str(ex)[:100])
        return {"ran": True, "ok": False, "skipped": False, "reason": str(ex)[:100], "out": ""}

    tail = [l for l in out.splitlines() if l.strip()][-3:]
    if rc == 0:
        log("[授权兜底] ✅ 通过（rc=0）")
        return {"ran": True, "ok": True, "skipped": False, "reason": "", "out": out}
    log("[授权兜底] ⚠ 未通过（rc=%s）→ **不阻断**，交业务报它遇到的真实错误" % rc)
    for l in tail:
        log("            | " + l[:150])
    return {"ran": True, "ok": False, "skipped": False, "reason": "rc=%s" % rc, "out": out}
