# -*- coding: utf-8 -*-
"""net_refresh_cn_cidrs.py — 生成「国内 IP 段」表，供 `net_route.py` 判断直连还是走代理。

用户 2026-11-07 口径：
    「国内 ip 都直连，其他域名才走代理」

为什么需要一张表
----------------
本机代理（Clash Verge，mixed-port 7897）是**规则模式**，但它只认域名规则。
而我们的脚本常常是先拿域名再连，压根不知道对面在国内还是国外 —— 以前的写法是
每个脚本各自拍板（飞书走代理、MeoW 直连、领星直连、kdocs 走代理…），
于是同一台机器上三套口径并存，出问题只能一处一处试。

这里把「谁在国内」固化成数据：从 **APNIC 官方委派文件**解析 `CN|ipv4` 段，
合并相邻段、压成 CIDR，落到 `scripts/assets/cn_ipv4.txt`。
`net_route.py` 运行时把这个文件读进内存，对目标域名做一次 DNS 解析，
命中即直连、不命中即走代理。

数据来源与时效
--------------
    https://ftp.apnic.net/apnic/stats/apnic/delegated-apnic-latest

APNIC 每天更新，我们的表**按季度**刷一次就够（`--max-age-days`，默认 180 天）。
`net_route` 发现表超过 `STALE_DAYS` 会在结果里带一句提示，但**不阻断**业务 ——
IP 段漂移的影响是「偶发多走一次代理」，不该让发货任务因此失败。

用法
----
    python scripts/net_refresh_cn_cidrs.py              # 重新下载并写表
    python scripts/net_refresh_cn_cidrs.py --check      # 只看现状（时效/条数），不改文件
    python scripts/net_refresh_cn_cidrs.py --from-file x.txt   # 用本地已下载的委派文件
"""
from __future__ import annotations

import argparse
import io
import ipaddress
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ASSET = os.path.join(HERE, "assets", "cn_ipv4.txt")
META = os.path.join(HERE, "assets", "cn_ipv4.meta.json")

APNIC_URL = "https://ftp.apnic.net/apnic/stats/apnic/delegated-apnic-latest"
#: 表超过这个天数就该刷了（APNIC 每天更新，但 IP 段漂移很慢）
STALE_DAYS = 180


def _proxy() -> str:
    """下载也要代理 —— 但只在这一个脚本里显式用，不从环境继承（环境里可能是死端口）。"""
    return (os.environ.get("TEMU_PROXY") or "http://127.0.0.1:7897").strip()


def download(timeout: int = 90) -> str:
    """取 APNIC 委派文件正文。代理不通就直连重试一次（这份文件国内外都能取）。"""
    req = urllib.request.Request(APNIC_URL, headers={"User-Agent": "temu-net-route/1.0"})
    errs = []
    for tag, opener in (("proxy", urllib.request.build_opener(
                             urllib.request.ProxyHandler({"http": _proxy(), "https": _proxy()}))),
                        ("direct", urllib.request.build_opener(
                             urllib.request.ProxyHandler({})))):
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as ex:                       # noqa: BLE001
            errs.append("%s: %s: %s" % (tag, type(ex).__name__, str(ex)[:120]))
    raise RuntimeError("下载 APNIC 委派文件失败 → " + " | ".join(errs))


def parse_cn_ipv4(text: str) -> list:
    """从委派文件里抠 `apnic|CN|ipv4|start|count|date|status` → 合并后的 [(s, e)]。

    纯函数，方便测试。**只取 allocated/assigned**，`available`/`reserved` 不算。
    """
    nets = []
    for line in (text or "").splitlines():
        p = line.split("|")
        if len(p) < 7 or p[0] != "apnic" or p[1] != "CN" or p[2] != "ipv4":
            continue
        if p[6] not in ("allocated", "assigned"):
            continue
        try:
            start = int(ipaddress.IPv4Address(p[3].strip()))
            count = int(p[4])
        except Exception:                             # noqa: BLE001
            continue
        if count <= 0:
            continue
        nets.append((start, start + count - 1))
    nets.sort()
    merged = []
    for s, e in nets:
        if merged and s <= merged[-1][1] + 1:
            if e > merged[-1][1]:
                merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def to_cidrs(ranges: list) -> list:
    """[(s, e)] → CIDR 字符串列表。

    每个区间先用 `summarize_address_range` 拆成对齐的 CIDR（
    起止地址不必是对齐的网段，直接用 `ip_network` 会 ValueError），
    再整体 collapse 一遍去掉重复。
    """
    if not ranges:
        return []
    nets = []
    for s, e in ranges:
        try:
            nets.extend(ipaddress.summarize_address_range(
                ipaddress.IPv4Address(s), ipaddress.IPv4Address(e)))
        except Exception:                             # noqa: BLE001
            continue
    return [str(n) for n in ipaddress.collapse_addresses(nets)]


def write_asset(cidrs: list, ranges: int) -> None:
    os.makedirs(os.path.dirname(ASSET), exist_ok=True)
    head = [
        "# 国内 IPv4 段（CIDR，一行一个）—— 由 scripts/net_refresh_cn_cidrs.py 生成，勿手改。",
        "# 来源：%s" % APNIC_URL,
        "# 生成时间：%s    原始段数：%d    压缩后：%d"
        % (time.strftime("%Y-%m-%d %H:%M:%S"), ranges, len(cidrs)),
        "# 用途：net_route.py 判断「直连还是走代理」—— 命中即直连。",
    ]
    with io.open(ASSET, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(head) + "\n")
        fh.write("\n".join(cidrs) + "\n")
    with io.open(META, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"generated": int(time.time()),
                   "generated_text": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "source": APNIC_URL, "ranges": ranges,
                   "cidrs": len(cidrs)}, fh, ensure_ascii=False, indent=1)


def read_meta() -> dict:
    try:
        with io.open(META, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                 # noqa: BLE001
        return {}


def age_days() -> float:
    """表已生成多少天；没有表返回 -1。"""
    g = read_meta().get("generated")
    if not g:
        try:
            return (time.time() - os.path.getmtime(ASSET)) / 86400.0
        except OSError:
            return -1
    return (time.time() - float(g)) / 86400.0


def main() -> int:
    ap = argparse.ArgumentParser(description="刷新国内 IPv4 段表")
    ap.add_argument("--check", action="store_true", help="只看现状，不下载不写文件")
    ap.add_argument("--from-file", default="", help="用本地已下载的 APNIC 委派文件")
    ap.add_argument("--max-age-days", type=float, default=STALE_DAYS)
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                 # noqa: BLE001
        pass

    if a.check:
        m = read_meta()
        age = age_days()
        print("[cn-ip] 文件：%s" % ASSET)
        print("[cn-ip] 存在：%s" % os.path.isfile(ASSET))
        print("[cn-ip] 段数：%s → CIDR：%s" % (m.get("ranges", "?"), m.get("cidrs", "?")))
        print("[cn-ip] 生成：%s（%.0f 天前，阈值 %.0f 天）"
              % (m.get("generated_text") or "未知", age, a.max_age_days))
        print("[cn-ip] %s" % ("该刷新了" if age < 0 or age > a.max_age_days else "还新鲜"))
        return 0 if (age >= 0 and age <= a.max_age_days) else 1

    if a.from_file:
        with io.open(a.from_file, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        print("[cn-ip] 下载 %s ..." % APNIC_URL)
        text = download()

    ranges = parse_cn_ipv4(text)
    if len(ranges) < 1000:
        print("[cn-ip] ✗ 只解析出 %d 段，明显不对（文件结构变了？）→ 不写" % len(ranges))
        return 2
    cidrs = to_cidrs(ranges)
    write_asset(cidrs, len(ranges))
    print("[cn-ip] ✓ %d 段 → %d 条 CIDR → %s（%.1f KB）"
          % (len(ranges), len(cidrs), ASSET, os.path.getsize(ASSET) / 1024.0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
