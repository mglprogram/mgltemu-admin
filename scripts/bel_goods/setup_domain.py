# -*- coding: utf-8 -*-
"""检查/绑定 temu-dashboard 的自定义域名（Cloudflare Pages）。

用法：
  python setup_domain.py --list            # 看当前绑定与状态
  python setup_domain.py --add mgltemu.us.ci   # 绑定自定义域名
  python setup_domain.py --check mgltemu.us.ci # 查 DNS 解析 + 绑定状态

前置：域名所在区（zone）必须已加入同一个 Cloudflare 账户，
否则域名会一直停在 pending（zone_tag 为空）。
本机 API Token 只有 Pages 权限，**没有 zone:create**，
所以「把 us.ci 加为站点」这一步必须在 Cloudflare 控制台手动做。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "91c4d2f7d1b53f4465e6e442ce5f7481")
PROJECT = os.environ.get("CF_PROJECT_NAME", "temu-dashboard")
API = "https://api.cloudflare.com/client/v4"


def token() -> str:
    tok = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if tok:
        return tok
    try:
        import winreg  # noqa: PLC0415
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, "CLOUDFLARE_API_TOKEN")[0]
    except Exception:  # noqa: BLE001
        return ""


def call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path,
        method=method,
        headers={
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def cmd_list() -> int:
    r = call("GET", f"/accounts/{ACCOUNT_ID}/pages/projects/{PROJECT}/domains")
    if not r.get("success"):
        print("查询失败:", json.dumps(r.get("errors"), ensure_ascii=False))
        return 2
    if not r["result"]:
        print("（该项目还没有自定义域名）")
        return 0
    for d in r["result"]:
        print(f"  域名      : {d['name']}")
        print(f"  状态      : {d['status']}")
        print(f"  校验方式  : {d['validation_data']['status']} / {d['validation_data']['method']}")
        print(f"  所属 zone : {d.get('zone_tag') or '（空 —— 域名的区还没加到 Cloudflare）'}")
        print()
    return 0


def cmd_add(name: str) -> int:
    r = call("POST", f"/accounts/{ACCOUNT_ID}/pages/projects/{PROJECT}/domains",
             {"name": name})
    if r.get("success"):
        print(f"已绑定 {name}，状态：{r['result']['status']}")
        return 0
    print("绑定失败:", json.dumps(r.get("errors"), ensure_ascii=False))
    return 2


def cmd_check(name: str) -> int:
    import socket  # noqa: PLC0415
    print(f"DNS 解析 {name} …")
    try:
        infos = socket.getaddrinfo(name, None)
        ips = sorted({i[4][0] for i in infos})
        print("  ->", ", ".join(ips))
    except Exception as e:  # noqa: BLE001
        print(f"  -> 解析失败：{e}")
        print("     说明该域名还没接入 DNS（需要在 DNSHE 改 NS + Cloudflare 加站点）")

    r = call("GET", f"/accounts/{ACCOUNT_ID}/pages/projects/{PROJECT}/domains")
    for d in (r.get("result") or []):
        if d["name"] == name:
            print(f"Pages 绑定状态: {d['status']} / 校验 {d['validation_data']['status']}")
            if not d.get("zone_tag"):
                print("  ⚠️ zone_tag 为空 —— 该域名的区尚未加入本 Cloudflare 账户，永远不会生效")
            return 0
    print("Pages 上未绑定该域名（可用 --add 绑定）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--add")
    ap.add_argument("--check")
    a = ap.parse_args()
    if a.list:
        return cmd_list()
    if a.add:
        return cmd_add(a.add)
    if a.check:
        return cmd_check(a.check)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
