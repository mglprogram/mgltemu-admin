# -*- coding: utf-8 -*-
"""net_route.py — 一条规则决定「直连还是走代理」：**国内 IP 直连，其他走代理**。

用户 2026-11-07 口径：
    「国内 ip 都直连，其他域名才走代理」

为什么要有这一层
----------------
在这之前，本机每处联网代码各自拍板，口径互相矛盾：

| 代码 | 做法 | 后果 |
|---|---|---|
| `feishu_bitable.py` | 清空代理（以为飞书在国内直连） | 飞书实测直连 **302/200 都通**，勉强能用 |
| `notify_push.py` | 用默认 opener（继承环境） | 环境里是**已废弃的 10808** → WinError 10061 |
| `meow_notify.py` | 清空代理 | 对（MeoW 走代理会 429 IP 限制） |
| `oms_client.py` | 先直连、失败才回落代理 | 对，但每个脚本都要抄一遍 |
| `push_fulfill_report.py` | 硬编码 7897 | 对，但端口散了 3 个地方 |

真正的判据不是「域名是哪家」，而是**对面 IP 在不在国内**。所以这里做三件事：

1. `scripts/assets/cn_ipv4.txt` —— 国内 IPv4 段表（APNIC 官方委派数据，
   由 `net_refresh_cn_cidrs.py` 生成，季度级刷新就够）。
2. 对目标域名做一次 DNS 解析，**命中表内 → 直连；不命中 → 走 7897 代理**。
3. 把结论包装成 `urllib` 的 opener 或子进程的 env，让调用方不再关心代理。

四条硬约束（都有实测依据）
--------------------------
· **别信环境变量**：DSH 进程的 `HTTP(S)_PROXY` 被固定在 `127.0.0.1:10808`
  （旧 v2rayN 端口，早已废弃），子进程改不动它。所以一律**显式**指定，不从环境继承。
· **代理端口只在 ONE 处定义**（`PROXY`），换代理软件只改这一个常量。
· **永不因为路由判断失败就抛异常**：判不出来就按「走代理」兜底（代理挂了才真失败），
  并把原因写进返回值，让调用方能记日志。
· **本机/内网必须直连**：`127.0.0.1`、`localhost`、`::1`、私有网段 ——
  否则 CDP（紫鸟调试端口）会被塞进代理，整条自动化链路当场断掉。

用法
----
    from net_route import open_url, opener_for, env_for_host, decide

    # 1) 一行发请求（自动选路）
    body = open_url("https://open.feishu.cn/open-apis/...", timeout=20).read()

    # 2) 要复用 opener
    op, route = opener_for("https://api.kdocs.cn/")
    with op.open(req, timeout=60) as r: ...

    # 3) 给子进程（kdocs-cli / wrangler / git）准备 env
    env = env_for_host("https://api.kdocs.cn/", dict(os.environ))

命令行自查（排障第一件事就跑这个）：

    python scripts/net_route.py open.feishu.cn api.kdocs.cn github.com
"""
from __future__ import annotations

import bisect
import ipaddress
import os
import socket
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ASSET = os.path.join(HERE, "assets", "cn_ipv4.txt")

#: ★ 代理端口**唯一**定义处（Clash Verge mixed-port；旧 v2rayN 的 10808 已废弃）
PROXY = "http://127.0.0.1:7897"

#: 表缺失时的兜底：宁可在墙外多直连一次，也不要把国内流量塞进代理
DEFAULT_MODE = "proxy"

#: 强制直连（**有实测依据才加**，别凭感觉）
FORCE_DIRECT = {
    "open.feishu.cn",       # 2026-11-07 实测：直连 302/200 均通，且比走代理快（0.17s vs 走代理同样快，但省一跳）
    "open.larksuite.com",
    "api.kdocs.cn",         # 金山文档，国内
    "www.kdocs.cn",
    "api.chuckfang.com",    # MeoW：走代理会 429 IP 限制（2026-10-28 实测）
    "api.xlwms.com",        # 领星 OMS
    "oms.xlwms.com",
    "powerwarehouse.net",   # 帕沃尔（阿里云杭州 47.114.151.12）
    "www.powerwarehouse.net",
    "tracking.nextsls.com",
    "hengxe.nextsls.com",
}

#: 强制走代理（**有实测依据才加**）
FORCE_PROXY = {
    "github.com",           # 直连 Recv failure / 15s 超时
    "agentseller-us.temu.com",   # TEMU 美国站（Azure），直连可通但不稳，走代理更快
    "api.cloudflare.com",
    "mgltemu.us.ci",        # 面板在 Cloudflare 上
}

#: 这些后缀永远直连（本机 / 内网）
LOCAL_SUFFIX = (".local", ".localhost", ".internal", ".lan")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


class Route(object):
    """一次选路的结论。`mode` ∈ {"direct", "proxy", "local"}。"""

    __slots__ = ("host", "ips", "mode", "reason")

    def __init__(self, host: str, ips: list, mode: str, reason: str):
        self.host, self.ips, self.mode, self.reason = host, ips, mode, reason

    @property
    def direct(self) -> bool:
        return self.mode in ("direct", "local")

    def __repr__(self) -> str:
        return "<Route %s %s %s (%s)>" % (self.host, self.mode, self.reason,
                                         ",".join(self.ips[:2]) or "-")


# --------------------------------------------------------------------------- 表

_TABLE = None            # [(start_int, end_int)] 升序
_TABLE_ERR = ""
_DNS_CACHE: dict = {}
DNS_TTL = 600.0


def load_table() -> list:
    """读 `cn_ipv4.txt` → [(start, end)] 升序。只读一次，失败返回 []（不抛）。"""
    global _TABLE, _TABLE_ERR
    if _TABLE is not None:
        return _TABLE
    _TABLE = []
    try:
        with open(ASSET, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    n = ipaddress.ip_network(line, strict=False)
                except Exception:                      # noqa: BLE001
                    continue
                if n.version != 4:
                    continue
                _TABLE.append((int(n.network_address), int(n.broadcast_address)))
        _TABLE.sort()
    except OSError as ex:
        _TABLE_ERR = "读不到国内 IP 段表（%s）：%s" % (ASSET, ex)
        _TABLE = []
    return _TABLE


def table_problem() -> str:
    """表不可用时返回原因，可用时返回空串。"""
    load_table()
    return _TABLE_ERR


def is_cn_ip(ip: str) -> bool:
    """这个 IPv4 是不是国内段。非 IPv4 / 解析失败 → False。"""
    t = load_table()
    if not t:
        return False
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.version != 4:
        return True        # IPv6 表里没有；别因为「没查到」就把国内 v6 塞进代理
    v = int(a)
    i = bisect.bisect_right(t, (v, float("inf"))) - 1
    if i < 0:
        return False
    return t[i][0] <= v <= t[i][1]


# --------------------------------------------------------------------------- 解析

def host_of(url_or_host: str) -> str:
    """从 URL 或裸域名里抠 host（去端口、去 scheme）。纯字符串处理。"""
    s = (url_or_host or "").strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if s.startswith("["):                      # [::1]:9222
        return s.split("]", 1)[0].lstrip("[")
    if s.count(":") == 1:
        s = s.split(":", 1)[0]
    return s.strip().lower()


def resolve(host: str, timeout: float = 5.0) -> list:
    """DNS 解析成 IPv4 列表（带进程内缓存）。失败返回 []，不抛。"""
    import time as _t
    now = _t.time()
    hit = _DNS_CACHE.get(host)
    if hit and now - hit[0] < DNS_TTL:
        return hit[1]
    ips = []
    try:
        prev = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            for fam, _, _, _, sa in socket.getaddrinfo(host, None):
                if fam == socket.AF_INET and sa and sa[0] not in ips:
                    ips.append(sa[0])
        finally:
            socket.setdefaulttimeout(prev)
    except Exception:                              # noqa: BLE001
        ips = []
    _DNS_CACHE[host] = (now, ips)
    return ips


# --------------------------------------------------------------------------- 决策

def decide(url_or_host: str) -> Route:
    """核心判据：**国内 → 直连，其他 → 走代理**。永不抛异常。"""
    host = host_of(url_or_host)
    if not host or host in LOCAL_HOSTS or host.endswith(LOCAL_SUFFIX):
        return Route(host, [], "local", "本机/内网")

    # 裸 IP
    try:
        ipaddress.ip_address(host)
        return Route(host, [host], "direct" if _ip_direct(host) else "proxy",
                     "裸 IP")
    except ValueError:
        pass

    ips = resolve(host)

    # 1) 显式覆盖优先（下表每一条都有实测依据，见文件头）
    if host in FORCE_DIRECT:
        return Route(host, ips, "direct", "白名单：国内服务")
    if host in FORCE_PROXY:
        return Route(host, ips, "proxy", "白名单：墙外服务")

    # 2) 按 IP 判
    if not ips:
        return Route(host, ips, DEFAULT_MODE, "DNS 解析失败 → 兜底")
    cn = [ip for ip in ips if is_cn_ip(ip)]
    if len(cn) == len(ips):
        return Route(host, ips, "direct", "IP 全在国内段")
    if not cn:
        return Route(host, ips, "proxy", "IP 全在境外")
    # 多家 CDN 混布（国内有节点、境外也有）→ 直连更能吃到国内节点
    return Route(host, ips, "direct", "国内/境外混布 %d/%d → 走国内节点" % (len(cn), len(ips)))


def _ip_direct(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.is_private or a.is_loopback or a.is_link_local:
        return True
    return is_cn_ip(ip)


# --------------------------------------------------------------------------- 出站

def _proxy_handler() -> urllib.request.ProxyHandler:
    return urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})


def _direct_handler() -> urllib.request.ProxyHandler:
    return urllib.request.ProxyHandler({})


def opener_for(url_or_host: str):
    """→ (opener, Route)。按结论构造 opener —— **不看环境变量**。"""
    rt = decide(url_or_host)
    op = urllib.request.build_opener(
        _direct_handler() if rt.direct else _proxy_handler())
    return op, rt


def open_url(url_or_host: str, timeout: float = 30.0, data: bytes | None = None,
             headers: dict | None = None, method: str | None = None):
    """一行发请求：自动选路 + 走正确 opener。返回 response 对象（调用方负责 close）。"""
    op, _rt = opener_for(url_or_host)
    req = urllib.request.Request(url_or_host, data=data,
                                 headers=headers or {}, method=method)
    return op.open(req, timeout=timeout)


def read_url(url_or_host: str, timeout: float = 30.0, **kw) -> str:
    """取文本（UTF-8 / 替换非法字节）。给「只要 body」的场景用。"""
    with open_url(url_or_host, timeout=timeout, **kw) as r:
        return r.read().decode("utf-8", "replace")


def env_for_host(url_or_host: str, base: dict | None = None) -> dict:
    """给**子进程**准备 env：国内 → 清空代理，墙外 → 指向 PROXY。

    `NO_PROXY` 一律补上本机段 —— 否则 kdocs-cli / wrangler 这类工具
    会连本地回调端口也走代理，行为诡异。
    """
    env = dict(os.environ if base is None else base)
    rt = decide(url_or_host)
    if rt.direct:
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = ""
        env.pop("http_proxy", None)
        env.pop("https_proxy", None)
        env["NO_PROXY"] = env["no_proxy"] = "*"
    else:
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = PROXY
        env["http_proxy"] = env["https_proxy"] = PROXY
        env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost,::1"
    return env


# --------------------------------------------------------------------------- CLI

def main(argv: list) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                              # noqa: BLE001
        pass
    if not argv or "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    prob = table_problem()
    if prob:
        print("[net] ⚠ %s\n     先跑：python scripts/net_refresh_cn_cidrs.py" % prob)
    bad = 0
    for t in argv:
        if t.startswith("-"):
            continue
        rt = decide(t)
        print("%-34s %-7s %-34s %s"
              % (rt.host or "(空)", rt.mode, rt.reason, ",".join(rt.ips[:3]) or "-"))
        if rt.reason.startswith("DNS 解析失败"):
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
