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


# ---------------------------------------------------------------- 一行接入（2026-11-02 加）
#: 店后缀 → 紫鸟窗口名。集中在这里，业务脚本不必各自抄一遍。
STORE_WINDOWS = {
    "BEL": "TM-BEL-MGL",
    "BHY": "TM-BHY-YJF",
}


def guard_store(store: str, log=print, timeout: float = 300.0,
                domain: str = "us", enabled: bool = True,
                window: str = "") -> dict:
    """**一行接入的登录态兜底**：给会碰紫鸟窗口的业务脚本用。

    为什么加这个（2026-11-02 全量审计）
    ----------------------------------
    登录态问题反复出现，根因不是"授权代码不好"，而是**绝大多数业务脚本根本
    没接授权兜底**：

        会碰紫鸟窗口的脚本         有兜底的
        ------------------------  ----------
        watch_focus.py      (限流监控)     ✗   ← 历史上失败最多的一个
        refresh_today_sales.py (今日销量)  ✗
        run_goods_export.py (在售款导出)   ✗
        run_temu_sales.py   (销量导出)     ✗
        run_temu_buylabel.py               ✓
        run_temu_abnormal.py               ✓

    Python 侧只有 2/6 接了。于是窗口一重开（登录态必掉），那 4 个脚本
    照旧失败 —— 每次都只能靠人工补授权。

    用法（业务脚本里，端口探到之后、真正干活之前）：

        from lib import auth_guard
        port = 50787
        auth_guard.guard_store("BEL", port=port, log=print)

    与 `ensure_auth()` 的分工：本函数自己**探端口 + 填 window + 按 domain 选入口**，
    调用方只需要报一个店名。已登录时秒过（幂等），失败**不阻断**。
    """
    # 延迟导入 + 绝对导入：既避免循环依赖，也支持「lib 目录直接在 sys.path 上」
    # 的调用方式（watch_focus.py / run_goods_export.py 都是那样，用不了相对导入）。
    try:
        import auth_temu_driver      # noqa: PLC0415
    except ImportError:
        from . import auth_temu_driver  # noqa: PLC0415
    res = {"ran": False, "ok": False, "skipped": True, "reason": "", "out": ""}
    if not enabled:
        res["reason"] = "--no-auth-guard"
        return res
    if not store:
        res["reason"] = "无店名"
        return res

    win = window or STORE_WINDOWS.get(str(store).upper(), "")
    port = 0
    if win:
        try:
            import ziniao_store as zs            # noqa: PLC0415
            port = zs.probe_port_by_window(win) or 0
        except Exception as e:                    # noqa: BLE001
            log("[授权兜底] 探端口失败（%s）→ 跳过" % str(e)[:80])
    if not port:
        # 端口都没有时授权也无从谈起；交给业务自己报它遇到的真实错误。
        res["reason"] = "无端口"
        return res

    # ⚠ 先做**真请求探针**，别只看窗口里有没有订单页（那是残影）。
    #   已登录就直接返回，不触发任何 UI 操作（避免不必要的风控暴露）。
    try:
        state, landed = auth_temu_driver.probe_login_state(port, domain=domain, log=log, shop=store)
    except Exception as e:                        # noqa: BLE001
        state, landed = auth_temu_driver.LOGIN_UNKNOWN, ""
        log("[授权兜底] 探针异常（%s）→ 保守按已登录处理" % str(e)[:80])

    if state == auth_temu_driver.LOGIN_OK:
        log("[授权兜底] ✅ %s 登录态有效，无需授权（%s）" % (store, landed[:90]))
        return {"ran": True, "ok": True, "skipped": True, "reason": "already", "out": landed}
    if state == auth_temu_driver.LOGIN_UNKNOWN:
        log("[授权兜底] %s 探针未定论 → 保守按已登录处理，不乱触发授权" % store)
        return {"ran": False, "ok": False, "skipped": True, "reason": "unknown", "out": landed}

    # LOGIN_LOST：真的要补授权
    log("[授权兜底] %s 登录态已失效（%s）→ 触发授权" % (store, landed[:90]))
    extra = ["--domain", domain] if domain and domain != "us" else []
    return ensure_auth(store, port=port, log=log, timeout=timeout, extra_args=extra)


# ---------------------------------------------------------------- 开局自愈（2026-11-02 加）
def heal_own_hung_pages(port: int, log=print, window: str = "") -> int:
    """清掉**我们自己登记过**的卡死页，必要时重探端口。返回可用的 port。

    为什么和登录态兜底放在一起（2026-11-02 实测）
    --------------------------------------------
    无人值守任务反复失败的另一大类原因是 **渲染进程挂死**：

        [TEMU] 「已售罄」→ 5 行（chip 计数 5）
        [warn] 端口 50070 读取失败：server rejected WebSocket connection: HTTP 500

    `HTTP 500` 不是登录态问题，也不是网络问题 —— 是那个 **target 的渲染进程
    已经死了**，CDP 连不上。`build_presale_data` / `temu_presale` 完全没有自愈，
    于是每次都退化成读缓存（数据看着正常、其实是旧的）。

    这个函数原本只在 `fetch_focus_spus._heal_hung()` 里有一份，现在提取成
    **共享能力**，让每个碰窗口的入口都能在开跑前自愈一次。

    ⚠ **只清理我们自己登记过的页**（`cdp_transport.registered_targets()`）。
      用户自己开的页永不关闭、永不判定 —— 哪怕它真的卡了（2026-10-29 教训：
      旧实现遍历窗口所有页判 hung，把用户的 3 个页面关掉了）。

    返回：可用的端口（清理后重探；探不到返回 0，调用方按"窗口没开"处理）。
    """
    try:
        import cdp_transport as cdp            # noqa: PLC0415
    except Exception as e:                     # noqa: BLE001
        log("[开局自愈] cdp_transport 不可用（跳过）：%s" % str(e)[:80])
        return port
    if not port:
        return port

    mine = set()
    try:
        mine = set(cdp.registered_targets(port) or ())
    except Exception:                          # noqa: BLE001
        mine = set()
    if not mine:
        return port                            # 没有可清理的，什么也不做

    healed = 0
    try:
        for t in cdp.list_pages(port):
            tid = t.get("targetId") or t.get("id")
            if tid not in mine:
                continue                       # 不是我们开的 → 用户的页，连判都不判
            try:
                if cdp.probe_page_alive(port, t) != "hung":
                    continue
            except Exception:                  # noqa: BLE001
                continue
            try:
                cdp.recover_hung_page(port, t, log=log)
                healed += 1
                log("[开局自愈] 关掉卡死页 %s（%s）" % (str(tid)[:8], (t.get("url") or "")[:60]))
            except Exception:                  # noqa: BLE001
                pass
    except Exception as e:                     # noqa: BLE001
        log("[开局自愈] 枚举页面失败（不阻断）：%s" % str(e)[:80])

    if healed and window:
        # 关页后端口可能变（渲染进程重启会给新端口），重探一次
        try:
            import ziniao_store as zs          # noqa: PLC0415
            new_port = zs.probe_port_by_window(window) or 0
            if new_port and new_port != port:
                log("[开局自愈] 端口 %s → %s（关页后重探）" % (port, new_port))
                return new_port
        except Exception:                      # noqa: BLE001
            pass
    return port
