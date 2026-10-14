# -*- coding: utf-8 -*-
"""lib/zport.py — 端口探测封装（工程控制闸门层）。

对治最高频反复错误：**硬编码端口漂移**（历史会话 PORT 关键词 16k+ 次）。
原则：CDP/服务端口**永不固化**，一律由本模块现场探测；缺失/漂移时安全失败。

本模块为纯函数 + 可注入 runner（测试不真连外部）。探测法参考历史 check_env.py：
netstat 扫监听端口 -> http://127.0.0.1:{port}/json/version 判 Chrome -> /json/list 匹配页面 URL。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Callable, Optional

__all__ = [
    "is_listening",
    "listen_ports",
    "probe_version",
    "probe_tabs",
    "probe_store_port",
    "find_cdp_port",
    "find_self_hub_port",
    "PORT_RULES",
    # 窗口占用检测（2026-09-21 · 事故根因闸门）
    "established_holders",
    "foreign_holders",
    "proc_table",
    "self_family",
    "cmdlines",
]


def _default_netstat() -> str:
    out = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
    )
    return out.stdout or ""


def is_listening(port: int, netstat_out: Optional[str] = None) -> bool:
    """给定端口是否处于 LISTENING（净 netstat 输出注入，测试可传样例）。"""
    text = netstat_out if netstat_out is not None else _default_netstat()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            try:
                local = parts[1].rsplit(":", 1)[1]
                if local.isdigit() and int(local) == int(port):
                    return True
            except Exception:
                continue
    return False


def listen_ports(netstat_out: Optional[str] = None) -> list[int]:
    """返回所有监听端口，过滤明显非浏览器段（10000<port<70000）。"""
    text = netstat_out if netstat_out is not None else _default_netstat()
    ports: set[int] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            try:
                local = parts[1].rsplit(":", 1)[1]
                if local.isdigit() and 10000 < int(local) < 70000:
                    ports.add(int(local))
            except Exception:
                continue
    return sorted(ports)


def _get_json(url: str, timeout: float = 2.0, getter: Optional[Callable] = None) -> Any:
    """请求 JSON，失败返回 None。getter 可注入（测试用），缺省 urllib。"""
    if getter is not None:
        try:
            return getter(url)
        except Exception:
            return None
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def probe_version(port: int, getter: Optional[Callable] = None) -> Optional[dict]:
    """http://127.0.0.1:{port}/json/version；非 Chrome（无调试端点）返回 None。"""
    v = _get_json(f"http://127.0.0.1:{port}/json/version", getter=getter)
    if isinstance(v, dict) and "Chrome" in str(v.get("Browser", "")):
        return v
    return None


def probe_tabs(port: int, getter: Optional[Callable] = None) -> list[dict]:
    """http://127.0.0.1:{port}/json/list，返回 page 类型 tab 列表。"""
    tabs = _get_json(f"http://127.0.0.1:{port}/json/list", getter=getter)
    if not isinstance(tabs, list):
        return []
    return [t for t in tabs if isinstance(t, dict) and t.get("type") == "page"]


# 页面特征 -> 用途标签；环境识别提示（与历史 check_env.py 一致）
PORT_RULES = [
    ("content-hub", "ContentHub投稿"),
    ("affiliate.tiktokshopglobalselling.com", "TK联盟"),
    ("creative/creativestudio", "Symphony生成"),
    ("image-to-video", "Symphony生成"),
    ("tiktok.com", "TikTok网页"),
    ("whatsapp", "自用hub"),
    ("bcajkjhhjlcicaefjhindadppcmbdjgk", "自用hub"),
    ("sample-request", "样品申请"),
]
ENV_HINTS = {
    "自用hub": ["whatsapp", "bcajkjhhjlcicaefjhindadppcmbdjgk"],
    "TK达人": ["content-hub", "creativestudio"],
    "TK联盟": ["affiliate.tiktokshopglobalselling.com"],
}


def _tag_pages(pages: list[dict]) -> list[str]:
    tags = []
    for t in pages:
        hay = (str(t.get("url", "")) + " " + str(t.get("title", ""))).lower()
        for kw, label in PORT_RULES:
            if kw in hay:
                tags.append(label)
                break
        else:
            tags.append("其他")
    return tags


def _env_name(pages: list[dict]) -> Optional[str]:
    hay = " ".join((str(t.get("url", "")) + " " + str(t.get("title", ""))).lower() for t in pages)
    for name, hints in ENV_HINTS.items():
        if any(h in hay for h in hints):
            return name
    return None


def probe_store_port(
    port: int, getter: Optional[Callable] = None
) -> Optional[dict]:
    """探测单端口：返回 {port, env, tags, pages}；非 Chrome 返回 None。"""
    if probe_version(port, getter=getter) is None:
        return None
    pages = probe_tabs(port, getter=getter)
    return {
        "port": port,
        "env": _env_name(pages),
        "tags": sorted(set(_tag_pages(pages))),
        "pages": [{"title": str(p.get("title", ""))[:40], "url": str(p.get("url", ""))[:80]} for p in pages],
    }


def find_cdp_port(
    netstat_out: Optional[str] = None, getter: Optional[Callable] = None, limit: int = 60
) -> list[dict]:
    """扫描监听端口，返回识别为浏览器/CDP 环境的端口详表。

    对治端口漂移：脚本应改用本函数返回的当前端口，而非固化旧值。
    """
    return [
        x for x in (
            probe_store_port(p, getter=getter) for p in listen_ports(netstat_out)[:limit]
        )
        if x is not None
    ]


def find_self_hub_port(
    netstat_out: Optional[str] = None, getter: Optional[Callable] = None, limit: int = 60
) -> Optional[int]:
    """定位「自用hub」环境端口（whatsapp / 自用hub 特征）。找不到返回 None。"""
    for e in find_cdp_port(netstat_out, getter=getter, limit=limit):
        if e["env"] == "自用hub":
            return e["port"]
    return None


# ---- 窗口占用检测（2026-09-21 · 事故根因闸门）----------------------------------
# 来历：会话未判「该紫鸟窗口是否已被别的活占用」就往里操作，抢了正在跑批的窗口
#       （批48 KLM 窗被 run_batch.py 占着，两条提交被判 silent-drop 后 SKIP）。
#       既有 preflight 只判「端口在不在监听 / 有没有 CDP 环境」，**占用态判不出来**。
# 地面真相：TCP ESTABLISHED 表（Chrome 的 /json/list 不暴露 attached 字段）。
#       同一对端 PID 在多次采样里都出现 = 持续持有（websocket 型 worker，如 run_batch）；
#       只出现一次的短连接（ziniao-cli 这种请求式调用）**不算**占用。

_DEFAULT_PS_PROC = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
    "Get-CimInstance Win32_Process | ForEach-Object { "
    "'{0}|{1}|{2}|{3}' -f $_.ProcessId, $_.ParentProcessId, $_.Name, "
    "($_.CommandLine -replace '\\|','/') }"
)


def _default_proc_runner() -> str:
    """取本机进程表（pid|ppid|name|cmdline）。powershell 不可用时退 pwsh。"""
    last_err: Optional[Exception] = None
    for exe in ("powershell", "pwsh"):
        try:
            out = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", _DEFAULT_PS_PROC],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            return out.stdout or ""
        except Exception as e:  # pragma: no cover - 环境相关
            last_err = e
            continue
    raise RuntimeError(f"取进程表失败（powershell/pwsh 均不可用）：{last_err}")


def _established_rows(netstat_out: str):
    """拆 netstat -ano 的 ESTABLISHED 行 → (local_port, remote_port, pid)。"""
    for line in netstat_out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[3].upper() != "ESTABLISHED":
            continue
        try:
            local_port = int(parts[1].rsplit(":", 1)[1])
            remote_port = int(parts[2].rsplit(":", 1)[1])
            pid = int(parts[4])
        except (IndexError, ValueError):
            continue
        yield local_port, remote_port, pid


def established_holders(port: int, netstat_out: Optional[str] = None) -> list[int]:
    """单次采样：谁在**客户端侧**持有该端口（发起连接的 PID，去重升序）。

    只认「本机临时端口 -> :port」那一侧；服务端侧（监听进程=浏览器本体）不算持有者。
    """
    text = netstat_out if netstat_out is not None else _default_netstat()
    pids = {
        pid for local_port, remote_port, pid in _established_rows(text)
        if remote_port == int(port) and local_port != int(port)
    }
    return sorted(pids)


def foreign_holders(
    port: int,
    *,
    netstat_out: Optional[str] = None,
    samples: Optional[list[str]] = None,
    self_pids: Optional[set[int] | list[int]] = None,
    interval: float = 3.0,
    sampler: Optional[Callable[[], str]] = None,
) -> list[int]:
    """**持续**持有该端口的外部 PID（多次采样交集 − 自身进程族）。

    samples 注入多份 netstat 文本（离线测试）；netstat_out 注入单份（= 单次采样）；
    都缺省则真跑 netstat 两次（间隔 interval）取交集。单次采样不做过期判定，调用方自负。
    """
    if netstat_out is not None:
        texts = [netstat_out]
    elif samples is not None:
        texts = list(samples)
    else:
        get = sampler or _default_netstat
        first = get()
        time.sleep(interval)
        texts = [first, get()]
    if not texts:
        return []
    per_sample = [{pid for pid in established_holders(port, netstat_out=t)} for t in texts]
    sustained = set.intersection(*per_sample) if per_sample else set()
    return sorted(sustained - {int(p) for p in (self_pids or ())})


def proc_table(runner: Optional[Callable[[], str]] = None) -> dict[int, dict]:
    """进程表 {pid: {ppid, name, cmdline}}（缺省经 PowerShell CIM 取一次；可注入，测试不真跑）。"""
    text = (runner or _default_proc_runner)()
    table: dict[int, dict] = {}
    for line in (text or "").splitlines():
        parts = line.strip().split("|", 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        table[pid] = {"ppid": ppid, "name": parts[2].strip(), "cmdline": parts[3].strip()}
    return table


def self_family(
    table: Optional[dict[int, dict]] = None, *, runner=None, pid: Optional[int] = None
) -> set[int]:
    """本进程 + 全部祖先 PID（把「我自己 / 我的批」排除在「外部占用」之外）。

    取不到进程表时退化为 {本进程}——宁少排除，不误放行别人的占用。走链路上限 64 层防环。
    """
    me = int(pid if pid is not None else os.getpid())
    fam = {me}
    try:
        tab = table if table is not None else proc_table(runner=runner)
    except Exception:
        return fam
    cur = me
    for _ in range(64):
        parent = int((tab.get(cur) or {}).get("ppid") or 0)
        if parent <= 0 or parent in fam:
            break
        fam.add(parent)
        cur = parent
    return fam


def cmdlines(
    pids, table: Optional[dict[int, dict]] = None, *, runner=None
) -> dict[int, str]:
    """取若干 PID 的完整命令行（供闸门报「谁在跑」）。"""
    tab = table if table is not None else proc_table(runner=runner)
    return {int(p): str((tab.get(int(p)) or {}).get("cmdline", "")) for p in pids}


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        print(json.dumps(find_cdp_port(), ensure_ascii=False, indent=1))
    else:
        print("使用：python -m lib.zport --demo")
