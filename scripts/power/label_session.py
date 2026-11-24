# -*- coding: utf-8 -*-
"""TEMU 会话自愈：下单前自检登录态 → 掉了就自动重登 → 断点续跑。

★ 2026-11-24 用户问：「如何保证会话、缓存和签名都同时存在」。
  实测结论：**不需要缓存/签名** —— `/kirogi/bg/mms/*` 只要 `mallid`，
  今天那个 403 是**页面停在 `/auth/authentication` 授权页**，与签名无关。
  真正依赖只有一个：**店铺窗口的登录态**（`seller_temp` 24 小时硬到期、
  窗口重开即丢）。

本模块把它做成自动：
  · `ensure_db_ready(store)`  —— 探针：拉一单看通不通（不通就尝试授权）
  · `ensure_login(store)`     —— 调 run_temu_auth.py 走授权（幂等，已登录秒过）
  · `run_with_retry(fn)`      —— 中途掉线 → 重登 → 从断点续跑

⚠ 授权入口（紫鸟 skill `temu-login-auth`）：`python scripts/power/run_temu_auth.py --store X`。
   它**不自己开窗**，窗口没开时要先 `ziniao_store.open_store`。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

AUTH_SCRIPT = os.path.join(_HERE, "run_temu_auth.py")
WINDOWS = {"BHY": "TM-BHY-YJF", "BEL": "TM-BEL-MGL",
           "KINGLAMANSPORT": "TM-KinglamanSport"}
#: `run_temu_auth.py` 的 `--store` 后缀（KLS 要用全名）
AUTH_SUFFIX = {"KINGLAMANSPORT": "KinglamanSport"}


def which_page(store: str) -> str:
    """当前窗口里最该用的页（订单列表优先），返回 URL 便于日志。"""
    import ziniao_store as zs  # noqa: PLC0415
    port = zs.probe_port_by_window(WINDOWS.get(store.upper(), store))
    if not port:
        return ""
    for t in zs.list_page_targets(int(port)):
        if "orders.html" in str(t.get("url") or ""):
            return str(t.get("url"))
    return ""


def probe(store: str) -> tuple:
    """探一次订单列表接口 → `(ok, reason)`。

    `reason` 取值：`ok` / `no_window` / `auth_page` / `forbidden` / `error:<...>`
    """
    try:
        import order_list_api as O  # noqa: PLC0415
        r = O.fetch_page(store, size=1)
        if r.get("ok"):
            return True, "ok"
        raw = str(r.get("raw") or "")
        if "400020037" in raw or "403" in raw:
            # 再看页面是不是停在授权页
            if "auth/authentication" in which_page(store) or not which_page(store):
                return False, "auth_page"
            return False, "forbidden"
        return False, "error:" + raw[:120]
    except Exception as ex:  # noqa: BLE001
        return False, "error:" + str(ex)[:120]


def ensure_window(store: str, log=print) -> bool:
    """窗口没开就开一个（`ziniao_store.open_store`），返回是否有端口。"""
    import ziniao_store as zs  # noqa: PLC0415
    win = WINDOWS.get(store.upper(), store)
    if zs.probe_port_by_window(win):
        return True
    log("   [自愈] 窗口 %s 未开 → 尝试打开" % win)
    try:
        sid = zs.store_id_by_name(win) if hasattr(zs, "store_id_by_name") else None
        zs.open_store(sid) if sid else log("   [自愈] 拿不到 storeId，请手动开窗")
        import time as _t  # noqa: PLC0415
        for _ in range(30):
            _t.sleep(3)
            if zs.probe_port_by_window(win):
                return True
    except Exception as ex:  # noqa: BLE001
        log("   [自愈] 开窗失败：%s" % str(ex)[:120])
    return False


def ensure_login(store: str, log=print) -> bool:
    """跑一次授权（幂等）。返回是否已登录。

    ★ 顺序（紫鸟 skill 铁律）：**先有窗口，再授权** —— `run_temu_auth.py` 自己不开窗。
    """
    if not ensure_window(store, log=log):
        return False
    suffix = AUTH_SUFFIX.get(store.upper(), store)
    log("   [自愈] 调授权：run_temu_auth.py --store %s" % suffix)
    try:
        p = subprocess.run([sys.executable, AUTH_SCRIPT, "--store", suffix],
                           capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace")
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-3:]
        for line in tail:
            log("      %s" % line[:160])
        if p.returncode == 0:
            return True
    except Exception as ex:  # noqa: BLE001
        log("   [自愈] 授权异常：%s" % str(ex)[:120])
    # 授权脚本返回非 0 也可能其实成功了（skill 里记过这个坑）→ 再用探针确认
    ok, why = probe(store)
    log("   [自愈] 授权后探针：%s（%s）" % ("通" if ok else "不通", why))
    return ok


def ensure_db_ready(store: str, log=print, retries: int = 1) -> bool:
    """下单前自检：能拉到订单就算就绪；不通则重登一次再试。"""
    ok, why = probe(store)
    if ok:
        log("   [自检] 订单接口正常")
        return True
    log("   [自检] 订单接口不通（%s）→ 尝试重登" % why)
    for i in range(retries):
        if ensure_login(store, log=log):
            ok2, why2 = probe(store)
            if ok2:
                log("   [自检] 重登后恢复正常")
                return True
            log("   [自检] 第 %d 次重登后仍不通（%s）" % (i + 1, why2))
    return False


def run_with_retry(store: str, fn, log=print, max_retry: int = 1):
    """跑 `fn()`；若因登录掉线抛错/返回 failed，重登后**再跑一次**（断点续跑由 fn 自己保证幂等）。

    `fn` 约定：返回 `dict` / `list`；抛异常视为需要重试。
    幂等性：`place_one` 对已下单的单会回「发货订单重复」，不会重复花钱。
    """
    try:
        return fn()
    except Exception as ex:  # noqa: BLE001
        log("   [自愈] 执行异常：%s" % str(ex)[:150])
        if max_retry <= 0:
            raise
        if ensure_login(store, log=log):
            log("   [自愈] 已重登 → 续跑")
            return fn()
        raise
