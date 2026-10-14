# -*- coding: utf-8 -*-
"""lib/preflight.py — 动手前状态探测闸门（工程控制强制度核心）。

对治：**基于过期状态做事 / 端口漂移 / 服务未起 → 503/超时/假死**。
原则：任何入口脚本（run_pull/run_score/run_publish 等）**起点强制调用**本闸门；
探测目标端口监听 / 服务存活 / 登录态 / 会话就绪，**发现异常即 HARD STOP**，
不让动作带着"环境未就绪 / 端口漂移 / 依赖过期"继续跑。这是程序级拦截，
与 agent 怎么写无关——如同 gate_check.js 对样品审批的强制。

用法（入口起点）：
    from lib import preflight
    preflight.require_env(store="SA", port=None)      # 抛 PreflightError -> HARD STOP
    preflight.require_port(port)                       # 端口必须监听
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

from lib import zport

__all__ = [
    "PreflightError", "preflight", "require_port", "require_env",
    "require_plot_env", "HARD_STOP_CODE",
    # 窗口占用闸门（2026-09-21）
    "WINDOW_DRIVER_KEYWORDS", "window_state", "window_free_problems", "require_window_free",
]

HARD_STOP_CODE = 42  # 对齐 gate_check.js 的 HARD_STOP(42)

# 紫鸟连窗脚本名：命令行命中即视为「有 worker 在动这个窗口」（窗口占用第二信号）。
# 用途：websocket 型 worker 会被 ESTABLISHED 抓住；请求式 worker 只产生短连接，
#       靠命令行兜住。**只在传了 window_hint（如 manifest/窗口关键词）时才判**，
#       避免「别的店窗口正在跑批」被误判成占用。
WINDOW_DRIVER_KEYWORDS = (
    "run_batch.py",
    "step_upload.py",
    "detect_limit.py",
    "cdp_dl_one_by_one.py",
    "ziniao_clear_refs.py",
    "run_ziniao_win.py",
    "run_product_crawl.py",
)

# 「紫鸟自己的工具进程」——**不算**占用者（2026-09-21 实测假阳性）：
# 客户端 `ziniao.exe` 会对自家浏览器实例常驻一条到调试端口的控制连接；
# 浏览器本体 `ziniaobrowser.exe` 则是 LISTENING 那一侧。二者都不是「别的活在抢窗口」，
# 混进来会让闸门在正常用窗时恒拦。真正的占用者是我们自己的 worker（python/node/playwright）。
WINDOW_SELF_TOOLS = (
    "ziniao.exe",
    "ziniaobrowser.exe",
    "ziniao-gateway.exe",
    "ziniaogateway.exe",
)


class PreflightError(RuntimeError):
    """闸门未通过：环境未就绪 / 端口漂移 / 依赖缺失。"""

    def __init__(self, problems: list[str], code: int = HARD_STOP_CODE) -> None:
        self.problems = problems
        self.code = code
        super().__init__("; ".join(problems))


def preflight(
    *,
    require_ports: Optional[list[int]] = None,
    store: Optional[str] = None,
    netstat_out: Optional[str] = None,
    getter: Optional[Callable] = None,
    require_cdp: Optional[bool] = True,
    require_window_free: bool = False,
    window_port: Optional[int] = None,
    window_hint: Optional[str] = None,
    allow_parallel_window: bool = False,
    self_pids: Optional[set[int] | list[int]] = None,
    proc_runner: Optional[Callable[[], str]] = None,
) -> list[str]:
    """探测环境，返回问题列表（空 = 就绪）。不抛异常，便于展示后决定。

    参数：
      require_ports: 必须处于监听的端口列表（缺省由 store 现探测）。
      store:           店铺名；传入则用 zport 探测该店 CDP 端口并校验在监听。
      netstat_out/getter: 测试注入（不真连外部）。
      require_cdp:      需识别到浏览器/CDP 环境。
      require_window_free: 需判「该窗口没有被别的活占用」（配套 window_port）。
      window_port:     要判占用的 CDP 端口（现取，禁写死）。
      window_hint:     窗口/manifest 关键词（如 "KLM窗"）：命令行命中才判占用（第二信号）。
      allow_parallel_window: 显式放行「同店多标签页并行」这一合法场景。
    """
    problems: list[str] = []

    if require_ports:
        for port in require_ports:
            if not zport.is_listening(port, netstat_out=netstat_out):
                problems.append(f"端口 {port} 未监听（可能漂移或服务未起，需先 L1 store open / 启动服务）")

    if store is not None:
        cdps = zport.find_cdp_port(netstat_out=netstat_out, getter=getter)
        store_hit = [e for e in cdps if e["env"] not in (None, "未知")]
        if require_cdp and not store_hit:
            problems.append(
                f"未探测到浏览器/CDP 环境（store={store}）。先开紫鸟店窗口/浏览器，勿用旧端口。"
            )

    if require_window_free and window_port is not None:
        problems.extend(
            window_free_problems(
                window_port,
                allow_parallel=allow_parallel_window,
                netstat_out=netstat_out,
                self_pids=self_pids,
                runner=proc_runner,
                window_hint=window_hint,
            )
        )

    return problems


def require_port(port: int, *, netstat_out: Optional[str] = None) -> int:
    """端口必须监听，否则 HARD STOP。返回端口供链式使用。"""
    problems = preflight(require_ports=[port], netstat_out=netstat_out)
    if problems:
        raise PreflightError(problems)
    return port


def require_env(
    *,
    store: Optional[str] = None,
    require_ports: Optional[list[int]] = None,
    netstat_out: Optional[str] = None,
    getter: Optional[Callable] = None,
    require_cdp: bool = True,
) -> dict[str, Any]:
    """环境必须就绪，否则 HARD STOP；返回探测摘要供入口展示。"""
    problems = preflight(
        store=store, require_ports=require_ports,
        netstat_out=netstat_out, getter=getter, require_cdp=require_cdp,
    )
    if problems:
        raise PreflightError(problems)
    return {"ok": True, "store": store}


def _safe_proc_table(runner: Optional[Callable[[], str]] = None) -> dict[int, dict]:
    """取进程表；环境取不到就返回空表（宁少一个信号，不因探测失败拦住所有活）。"""
    try:
        return zport.proc_table(runner=runner)
    except Exception:
        return {}


def _short(text: str, limit: int = 140) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _proc_name(table: Optional[dict[int, dict]], pid: int) -> str:
    """取进程名（小写，带/不带 .exe 都归一到小写原名）；取不到返回空串。"""
    return str(((table or {}).get(int(pid)) or {}).get("name", "")).strip().lower()


def window_state(
    port: int,
    *,
    netstat_out: Optional[str] = None,
    samples: Optional[list[str]] = None,
    self_pids: Optional[set[int] | list[int]] = None,
    runner: Optional[Callable[[], str]] = None,
    interval: float = 3.0,
    window_hint: Optional[str] = None,
    proc_table: Optional[dict[int, dict]] = None,
) -> dict[str, Any]:
    """判一个 CDP 端口是否**已被别的活占用**，返回证据（不抛异常）。

    两路信号并集：
      S1 holders —— 持续 ESTABLISHED 在客户端侧的**外部** PID（websocket 型 worker）。
      S2 workers —— 命令行命中 WINDOW_DRIVER_KEYWORDS 且含 window_hint 的活进程
                    （请求式 worker 只产生短连接，S1 会漏；无 hint 时不判，防跨店误判）。
    """
    fam = set(self_pids) if self_pids is not None else zport.self_family(runner=runner)
    fam.add(os.getpid())
    holders = zport.foreign_holders(
        port, netstat_out=netstat_out, samples=samples, self_pids=fam, interval=interval
    )
    table = proc_table if proc_table is not None else _safe_proc_table(runner)
    # 剔除「紫鸟自己的工具进程」（客户端/浏览器本体常驻控制连接，不是别的活在抢窗）
    holders = [p for p in holders if _proc_name(table, p) not in WINDOW_SELF_TOOLS]
    holder_cmdlines = zport.cmdlines(holders, table=table) if holders else {}
    workers: list[dict] = []
    for pid, info in (table or {}).items():
        if pid in fam:
            continue
        cmd = str((info or {}).get("cmdline") or "")
        if not any(k in cmd for k in WINDOW_DRIVER_KEYWORDS):
            continue
        # 无 hint 时**不判**第二信号：别的店窗口正在跑批是合法并发，不该拦住本窗口。
        if not window_hint or window_hint not in cmd:
            continue
        workers.append({"pid": int(pid), "cmdline": _short(cmd)})
    return {
        "port": int(port),
        "holders": list(holders),
        "holder_cmdlines": {int(k): _short(v) for k, v in holder_cmdlines.items()},
        "workers": sorted(workers, key=lambda w: w["pid"]),
        "self_family": sorted(fam),
        "occupied": bool(holders) or bool(workers),
    }


def _window_problems(st: dict[str, Any]) -> list[str]:
    port = st.get("port")
    problems: list[str] = []
    for pid in st.get("holders") or []:
        cmd = (st.get("holder_cmdlines") or {}).get(pid) or "(命令行取不到)"
        problems.append(
            f"端口 {port} 已被 PID {pid} 持续占用：{cmd}"
            f" —— 同一窗口一次只许一个执行者；等它跑完，或另开独立店窗口"
        )
    holder_set = set(st.get("holders") or [])
    for w in st.get("workers") or []:
        if w["pid"] in holder_set:
            continue
        problems.append(
            f"端口 {port} 疑似被 PID {w['pid']} 占用（命令行命中紫鸟连窗脚本）：{w['cmdline']}"
        )
    return problems


def window_free_problems(
    port: int, *, allow_parallel: bool = False, **kw: Any
) -> list[str]:
    """窗口占用判定（返回问题列表，空 = 没被别人占用）。allow_parallel=True 直接放行。"""
    if allow_parallel:
        return []
    return _window_problems(window_state(port, **kw))


def require_window_free(
    port: int, *, allow_parallel: bool = False, **kw: Any
) -> dict[str, Any]:
    """窗口必须没被别的活占用，否则 HARD STOP(42)；返回占用证据摘要。

    用法（任何要操作某紫鸟店窗口的入口/脚本，**连窗前**）：
        from lib import preflight
        preflight.require_window_free(port, window_hint="KLM窗")
    同店多标签页并行是合法场景，需显式 allow_parallel=True（默认严）。
    """
    st = window_state(port, **kw)
    if allow_parallel:
        st["skipped"] = "allow_parallel"
        return st
    problems = _window_problems(st)
    if problems:
        raise PreflightError(problems)
    return st


def require_plot_env(
    *,
    modules: tuple[str, ...] = (),
    optional_modules: tuple[str, ...] = (),
    paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """plot 域起点闸门：探测绘图依赖与输入文件，失败 HARD STOP（code=42）。

    对治「依赖缺失才报错 / 输入文件路径错才跑空」。与 require_env（端口/服务）
    不同——选图域不涉端口/CDP，只需确认绘图库可导入 + 输入数据存在。
    用法（入口起点）：
        preflight.require_plot_env(modules=("pandas", "matplotlib"),
                                   optional_modules=("seaborn",),
                                   paths=[str(src_abs)])
    """
    problems: list[str] = []
    optional_problems: list[str] = []
    for m in modules:
        try:
            __import__(m)
        except Exception:
            problems.append(f"依赖模块 {m} 缺失或不可导入（需 pip install）")
    for m in optional_modules:
        try:
            __import__(m)
        except Exception:
            optional_problems.append(f"可选模块 {m} 缺失（分组/统计类图将降级为 pandas.plot）")
    for p in paths:
        if p and not Path(str(p)).exists():
            problems.append(f"输入文件不存在: {p}")
    if problems:
        raise PreflightError(problems)
    return {"ok": True, "modules": list(modules), "warnings": optional_problems}


if __name__ == "__main__":
    import sys
    # 演示：探测当前环境（不强行 STOP，仅列出问题）
    store = sys.argv[1] if len(sys.argv) > 1 else None
    p = preflight(store=store)
    if p:
        print("Preflight 问题：", p, file=sys.stderr)
        sys.exit(HARD_STOP_CODE)
    print("Preflight OK")
