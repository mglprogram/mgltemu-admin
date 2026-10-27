# -*- coding: utf-8 -*-
"""run_temu_auth_pipeline.py — TEMU 登录/授权 **分阶段 pipeline**（可单步、可验证、可恢复）。

为什么要有它（2026-10-27 立）：
    原来的 `run_temu_auth.py` 是一条**黑盒链**：进去要么 rc=0、要么 rc=42，
    中间走到哪一步、卡在哪一步、为什么卡，全在日志里靠人翻。
    真实链路其实是**三段六步**，而且**每一步都可能"点了没反应"**：

        ① authentication   点「中国地区·商家中心」
                          ↓ 新开一个 tab（当前页 URL 根本不变 ← 最容易误判）
        ② seller-login     勾授权条款 + 点「确认授权并前往」
                          ↓ 这里 JS click 无效，必须真实鼠标事件（带 buttons）
        ③ /mmsos/orders    落地才算成功

    BEL 2026-10-27 实测：旧实现第 ② 步「JS 点了就当成功」，于是日志写着
    `[授权] JS 点「确认授权并前往」`、URL 一点没变、继续报「授权未完成」，
    每天挂着。⇒ 本 pipeline 把每一步**单独暴露 + 单独验证**，点不动立刻报出来。

用法（对齐本仓库既有 pipeline 口径）：
    python scripts/power/run_temu_auth_pipeline.py --store BEL
    python scripts/power/run_temu_auth_pipeline.py --store BEL --list
    python scripts/power/run_temu_auth_pipeline.py --store BEL --stage check
    python scripts/power/run_temu_auth_pipeline.py --store BEL --from region --to confirm
    python scripts/power/run_temu_auth_pipeline.py --store BEL --dry-run

退出码：
    0   成功（窗口已落在商家中心可用页，或本来就已登录）
    1   某阶段跑完但判定失败（例如按钮点不动）
    42  HARD STOP（无窗口/无端口/未知店）—— 对齐 preflight.HARD_STOP_CODE
    43  某个阶段抛异常
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime

# ⚠ 只在**直接运行**时重包装 stdout，import 时不动。
#
# 2026-10-27 实测踩到：模块顶层 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer,...)`
# 会在 import 时把**调用方**的 stdout 换掉，旧 wrapper 被回收 ⇒ 之后任何 print 都
# `ValueError: I/O operation on closed file`（pytest 的 capture 流直接被它关掉，
# 表现为「收集阶段就 11 errors」）。run_temu_auth.py 现在仍有这个毛病，别再学它。
if __name__ == "__main__" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(_SCRIPTS, "lib")
_POWER_LIB = os.path.join(_SCRIPTS, "power", "lib")
for _p in (_LIB, _POWER_LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HARD_STOP = 42
STAGE_FAIL = 1
STAGE_ERROR = 43

STATE_DIR = os.path.join(os.path.dirname(_SCRIPTS), "_logs", "temu-auth-pipeline")

#: 阶段定义（顺序即执行顺序）。每个阶段：(名字, 说明, 需要的输入)
STAGES = [
    ("port", "探测店铺窗口 CDP 端口", "需要紫鸟窗口已开"),
    ("check", "判断当前是否已登录（已在商家中心可用页就结束）", "需要 port"),
    ("region", "认证页点「中国地区·商家中心」", "需要 port + 认证页"),
    ("confirm", "授权框勾条款 + 点确认按钮", "需要 port + seller-login 页"),
    ("verify", "复核确实落在商家中心可用页", "需要 port"),
]


# ---------------------------------------------------------------- 基础设施
def _say(msg: str = "") -> None:
    print(msg, flush=True)


def _state_path(store: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in store) or "store"
    return os.path.join(STATE_DIR, "%s.json" % safe)


def _load_state(store: str) -> dict:
    try:
        with open(_state_path(store), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(store: str, state: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(_state_path(store), "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _record(store: str, stage: str, ok: bool, detail: str) -> None:
    """一行 JSONL 留痕（沿用 temu-sales 的口径，便于 grep）。"""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        with open(os.path.join(STATE_DIR, ts[:10] + ".jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"ts": ts, "store": store, "stage": stage,
                 "verdict": "ok" if ok else "fail", "detail": str(detail)[:400]},
                ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 窗口/端口
def _store_map() -> dict:
    path = os.path.join(_SCRIPTS, "power", "data", "ziniao_power_map.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _entry(store: str) -> dict | None:
    for m in _store_map().get("mapping", []):
        if m.get("export_suffix") == store or m.get("power_name") == store:
            return m
    return None


def _find_port(window_name: str) -> int:
    """按窗口名**前缀**探测 CDP 端口（授权后标题会变，故不能精确匹配）。

    ⚠ 不能 `import run_temu_auth`：那个模块在顶层重包装 `sys.stdout`
    （`sys.stdout = io.TextIOWrapper(...)`），import 它会把**本进程**的 stdout
    换成新 wrapper，旧 wrapper 被回收 ⇒ 之后每次 print 都
    `ValueError: I/O operation on closed file`（2026-10-27 实测踩到）。
    所以这里独立实现同一口径，不 import 那个入口。
    """
    import subprocess

    def _run(cmd, timeout):
        try:
            return subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                  ).stdout or ""
        except Exception:  # noqa: BLE001
            return ""

    out = _run(["netstat", "-ano", "-p", "tcp"], 15)
    ps = _run(["powershell", "-NoProfile", "-Command",
               "Get-Process ziniaobrowser | Where-Object { $_.MainWindowTitle -like '%s*' } "
               "| Select-Object -ExpandProperty Id" % window_name], 20)
    pids = {int(x) for x in ps.split() if x.strip().isdigit()}
    if not pids:
        return 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            try:
                if int(parts[4]) not in pids:
                    continue
                port = int(parts[1].rsplit(":", 1)[1])
            except (IndexError, ValueError):
                continue
            if not (10000 < port < 70000):
                continue
            import urllib.request
            try:
                v = json.loads(urllib.request.urlopen(
                    "http://127.0.0.1:%s/json/version" % port, timeout=2).read())
                if v.get("webSocketDebuggerUrl"):
                    return port
            except Exception:  # noqa: BLE001
                continue
    return 0


# ---------------------------------------------------------------- 页面辅助
def _pages(port: int) -> list:
    import cdp_transport as t  # noqa: PLC0415
    try:
        return t.list_pages(port)
    except Exception:  # noqa: BLE001
        return []


def _is_seller_url(u: str) -> bool:
    """D4 判据：host=agentseller-us.temu.com 且 path 在 /mmsos/ 或 /main/ 下且非 auth/login。

    直接用驱动里的实现，保证与授权判定单点一致。
    """
    import auth_temu_driver as atd  # noqa: PLC0415
    return atd._url_is_seller_page(u)


def _find_page(port: int, *, kind: str):
    """按类型找页：kind 取 'seller' | 'auth' | 'login' | 'any'。返回 (CdpPage|None, url)。"""
    import cdp_transport as t  # noqa: PLC0415
    for x in _pages(port):
        u = str(x.get("url") or "")
        low = u.lower()
        if kind == "seller" and _is_seller_url(u):
            return t.CdpPage(port, x), u
        if kind == "auth" and "auth/authentication" in low:
            return t.CdpPage(port, x), u
        if kind == "login" and "settle/seller-login" in low:
            return t.CdpPage(port, x), u
    return None, ""


def _wait_for(port: int, kind: str, deadline_s: float, log=None) -> tuple:
    """轮询等待某类页出现（跳转有十几秒延迟，不能查一次就下结论）。"""
    waited = 0.0
    while True:
        pg, u = _find_page(port, kind=kind)
        if pg is not None:
            return pg, u
        if waited >= deadline_s:
            return None, ""
        time.sleep(3)
        waited += 3
        if log and int(waited) % 9 == 0:
            log("     …等待 %s 页 已等 %ds/%ds" % (kind, int(waited), int(deadline_s)))


# ---------------------------------------------------------------- 阶段实现
def stage_port(ctx: dict) -> tuple[bool, str]:
    store = ctx["store"]
    if ctx.get("port"):
        return True, "已指定端口 %s" % ctx["port"]
    ent = _entry(store)
    if not ent:
        return False, "未知店 %s（--list-stores 可查）" % store
    win = ent.get("ziniao_window_name", "")
    port = _find_port(win)
    if not port:
        return False, "未探测到窗口 %s 的 CDP 端口（窗口没开？）" % win
    ctx["port"] = port
    ctx["window"] = win
    return True, "窗口 %s → 端口 %s" % (win, port)


def stage_check(ctx: dict) -> tuple[bool, str]:
    """已在商家中心可用页 → 整个 pipeline 可以停在这里（幂等，秒过）。"""
    pg, u = _find_page(ctx["port"], kind="seller")
    if pg is not None:
        ctx["done"] = True
        return True, "已登录，无需授权（%s）" % u[:90]
    auth_pg, au = _find_page(ctx["port"], kind="auth")
    login_pg, lu = _find_page(ctx["port"], kind="login")
    if auth_pg is not None:
        ctx["kind"] = "auth"
        return True, "需要授权：认证页 %s" % au[:80]
    if login_pg is not None:
        ctx["kind"] = "login"
        return True, "需要授权：授权框页 %s" % lu[:80]
    ctx["kind"] = "none"
    return True, "窗口里既无可用页也无认证/授权页（需要先导航触发）"


#: 除 port 外，所有阶段都依赖一个**有效端口**。
#: ⚠ 2026-10-27 实测踩到：`--stage check` 单跑时没先跑 port，`ctx["port"]` 是 0 ⇒
#: `_pages(0)` 返回空 ⇒ check 判成「既无可用页也无认证页」，而同一窗口跑整链却
#: 正确识别为「已登录」。**阶段本身幂等，但不能带着空端口假装成功**。
_NEEDS_PORT = {"check", "region", "confirm", "verify"}


def _ensure_port(ctx: dict) -> tuple[bool, str]:
    """确保 ctx 里有可用端口；没有就现场探测一次。"""
    if ctx.get("port"):
        return True, "端口 %s" % ctx["port"]
    ok, detail = stage_port(ctx)
    if not ok:
        return False, detail
    return True, detail


def stage_region(ctx: dict) -> tuple[bool, str]:
    """认证页：点「中国地区·商家中心」。成功判据＝**窗口里出现 seller-login 页**。

    这一步的坑：点完当前页 URL **不变**，是**新开一个 tab**。
    只盯当前页会把成功误判成「没反应」。
    """
    import auth_temu_driver as atd  # noqa: PLC0415
    import cdp_transport as t  # noqa: PLC0415

    port = ctx["port"]
    pg, u = _find_page(port, kind="auth")
    if pg is None:
        # 没有认证页 → 可能已经推进过了，交给 confirm 阶段
        return True, "无认证页，跳过 region（可能已在授权框）"

    b = t.connect(port)
    try:
        ok = atd._click_region_goto(pg, b)
    finally:
        try:
            b.close()
        except Exception:  # noqa: BLE001
            pass
    if not ok:
        return False, "点「商家中心」没推进（弹窗遮挡/自动填充未完成？）"

    # 明确等 seller-login 出现，而不是只看当前页
    login_pg, lu = _wait_for(port, "login", 20.0, log=lambda m: _say(m))
    if login_pg is not None:
        return True, "已打开授权框 %s" % lu[:85]
    seller_pg, su = _find_page(port, kind="seller")
    if seller_pg is not None:
        ctx["done"] = True
        return True, "点完直接落到可用页 %s" % su[:85]
    return False, "点了商家中心，但没等到授权框（20s）"


def stage_confirm(ctx: dict) -> tuple[bool, str]:
    """授权框：勾条款 + 点确认。**每一步都验证真的推进了。**"""
    import auth_temu_driver as atd  # noqa: PLC0415

    port = ctx["port"]
    pg, u = _wait_for(port, "login", 10.0)
    if pg is None:
        seller_pg, su = _find_page(port, kind="seller")
        if seller_pg is not None:
            ctx["done"] = True
            return True, "已在可用页，无需点确认（%s）" % su[:85]
        return False, "找不到授权框页（seller-login）"

    _say("     授权框：%s" % u[:95])

    # ① 勾条款
    checked = atd._check_auth_checkbox(pg)
    _say("     条款勾选：%s" % ("已勾上" if checked else "没勾上（继续尝试点确认）"))

    # ② 同步紫鸟自动填入的账密到 React state（否则按钮处理器空转）
    try:
        n = atd._sync_input_state(pg)
        if n:
            _say("     已同步 %d 个输入框到前端状态" % n)
    except Exception:  # noqa: BLE001
        pass

    # ③ 点确认（内部会 JS → 真实鼠标 → locator 三种方式逐个验证）
    advanced = atd._click_auth_confirm(pg)
    if not advanced:
        return False, "确认按钮点了没推进（三种点击方式都试过）"
    return True, "确认按钮已触发推进"


def stage_verify(ctx: dict) -> tuple[bool, str]:
    pg, u = _wait_for(ctx["port"], "seller", 30.0, log=lambda m: _say(m))
    if pg is not None:
        ctx["done"] = True
        return True, "已落在商家中心可用页：%s" % u[:95]
    return False, "30s 内未落到商家中心可用页"


STAGE_FN = {
    "port": stage_port,
    "check": stage_check,
    "region": stage_region,
    "confirm": stage_confirm,
    "verify": stage_verify,
}


# ---------------------------------------------------------------- 主流程
def _order(names: list) -> list:
    idx = {n: i for i, (n, _, _) in enumerate(STAGES)}
    return sorted(names, key=lambda n: idx.get(n, 999))


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="TEMU 登录/授权分阶段 pipeline")
    ap.add_argument("--store", default="", help="店后缀（BEL / BHY …）")
    ap.add_argument("--port", type=int, default=0, help="已知 CDP 端口（跳过探测）")
    ap.add_argument("--stage", default="", help="只跑某一个阶段")
    ap.add_argument("--from", dest="frm", default="", help="从某阶段开始")
    ap.add_argument("--to", dest="to", default="", help="跑到某阶段为止")
    ap.add_argument("--list", action="store_true", help="列出阶段")
    ap.add_argument("--list-stores", action="store_true", help="列出 TEMU 店")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要做的事")
    a = ap.parse_args(argv)

    if a.list:
        _say("阶段（按执行顺序）：")
        for i, (n, d, need) in enumerate(STAGES, 1):
            _say("  %d. %-8s %-40s %s" % (i, n, d, need))
        return 0

    if a.list_stores:
        for m in _store_map().get("mapping", []):
            if m.get("power_platform") == "TEMU" or m.get("power_type") == "本土店":
                _say("%-10s window=%-20s" % (
                    m.get("export_suffix") or m.get("power_name"),
                    m.get("ziniao_window_name")))
        return 0

    if not a.store:
        _say("缺 --store（或用 --list / --list-stores）")
        return HARD_STOP

    names = [n for n, _, _ in STAGES]
    if a.stage:
        if a.stage not in names:
            _say("未知阶段 %s；可用：%s" % (a.stage, ", ".join(names)))
            return HARD_STOP
        names = [a.stage]
    else:
        if a.frm:
            if a.frm not in names:
                _say("未知阶段 %s" % a.frm)
                return HARD_STOP
            names = names[names.index(a.frm):]
        if a.to:
            if a.to not in [n for n, _, _ in STAGES]:
                _say("未知阶段 %s" % a.to)
                return HARD_STOP
            names = names[:names.index(a.to) + 1]

    ctx = {"store": a.store, "port": a.port or 0, "done": False}
    state = _load_state(a.store)
    _say("=" * 66)
    _say("TEMU 授权 pipeline  store=%s  阶段=%s" % (a.store, " → ".join(names)))
    if state.get("last_stage"):
        _say("上次跑到：%s（%s）" % (state.get("last_stage"), state.get("last_ts", "")))
    _say("=" * 66)

    if a.dry_run:
        for n in names:
            _say("  [dry] 会跑 %-8s %s" % (n, dict((x[0], x[1]) for x in STAGES)[n]))
        return 0

    rc = 0
    for n in names:
        _say("")
        _say("--- [%s] %s" % (n, dict((x[0], x[1]) for x in STAGES)[n]))
        t0 = time.time()
        try:
            # 单跑某阶段（--stage）时没有前置 port 阶段 ⇒ 现场补探测。
            if n in _NEEDS_PORT and not ctx.get("port"):
                okp, dp = _ensure_port(ctx)
                _say("      （前置）%s %s" % ("✓" if okp else "✗", dp))
                if not okp:
                    _say("      ✗ 阶段 [%s] 需要有效端口，但探测失败" % n)
                    _record(a.store, n, False, "无端口：" + dp)
                    _save_state(a.store, {"last_stage": n,
                                          "last_ts": datetime.now().isoformat(timespec="seconds"),
                                          "last_ok": False, "detail": "无端口", "port": 0})
                    return HARD_STOP
            ok, detail = STAGE_FN[n](ctx)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, "异常：%r" % (exc,)
            _say("      ✗ %s" % detail[:200])
            _record(a.store, n, False, detail)
            _save_state(a.store, {"last_stage": n, "last_ts": datetime.now().isoformat(timespec="seconds"),
                                  "last_ok": False, "detail": detail[:300], "port": ctx.get("port")})
            return STAGE_ERROR
        dt = round(time.time() - t0, 1)
        _say("      %s %s（%ss）" % ("✓" if ok else "✗", detail[:170], dt))
        _record(a.store, n, ok, detail)
        _save_state(a.store, {"last_stage": n, "last_ts": datetime.now().isoformat(timespec="seconds"),
                              "last_ok": ok, "detail": detail[:300], "port": ctx.get("port")})
        if not ok:
            rc = STAGE_FAIL
            _say("")
            _say("停在 [%s]。修复后可 --from %s 续跑。" % (n, n))
            break
        if ctx.get("done") and n in ("check", "region", "confirm", "verify"):
            _say("")
            _say("窗口已在商家中心可用页，后续阶段无需执行。")
            break

    _say("")
    _say("=" * 66)
    _say("结果：%s" % ("成功" if rc == 0 else "失败（rc=%s）" % rc))
    _say("状态文件：%s" % _state_path(a.store))
    _say("=" * 66)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
