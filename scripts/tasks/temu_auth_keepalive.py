# -*- coding: utf-8 -*-
"""temu_auth_keepalive.py — 每天**主动**刷新 BHY / BEL 的 TEMU 登录态（换新票）。

为什么要有它（2026-11-15 用户报「这两天 temu 的定时任务全都是失败了」）
----------------------------------------------------------------------
TEMU 的会话票 `seller_temp` 有效期＝**上次授权 + 24 小时**，到点服务端把该店里
**所有页面**一起踢下线 —— 没有任何人操作也会发生。票一掉，当天所有碰这个店的
任务就全红：04:00 发货、08:00 在售款、08:30 异常单、08:50 预售、11:00 逾期确认……

实测（2026-11-14 / 11-15 的日报）就是这么一串失败，原因都写着「登录态失效 / 授权页卡住」。
⇒ 与其等任务失败再去兜底，不如**每天固定换一次票**（`run_temu_auth.py` 幂等：
已登录会秒过并续期），让白天所有任务都跑在新票上。

用法 / 行为
-----------
· 每店：窗口没开就先自动开窗（窗口开着绝不重开，避免杀掉用户页面）→ 跑一次授权；
· 任一店失败 → 退出码 1（交给 health / 巡检报出来），但**不影响另一店**；
· 只读 + 授权，不下单、不花钱。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))          # D:\AI-workplace\TEMU
_POWER = os.path.join(_PROJ, "scripts", "power")
sys.path.insert(0, os.path.join(_POWER, "lib"))

#: 要保鲜的店（TEMU 美国站两家；合作仓 BEL 的那套授权由异常单任务自己兜）
STORES = ("BHY", "BEL", "KinglamanSport")   # 2026-11-17 加入 KLS


def _map() -> list:
    p = os.path.join(_POWER, "data", "ziniao_power_map.json")
    try:
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f).get("mapping") or []
    except Exception:  # noqa: BLE001
        return []


def _entry(store: str) -> dict:
    for m in _map():
        if str(m.get("power_name") or "").upper() == store.upper():
            return m
    return {}


def _ensure_window(store: str) -> int:
    """窗口没开就自动开；返回端口（失败 0）。窗口开着**不重开**。"""
    import ziniao_store as zs  # noqa: PLC0415
    e = _entry(store)
    win = str(e.get("ziniao_window_name") or store)
    port = zs.probe_port_by_window(win)
    if port:
        print("[窗] %s 已在运行（端口 %s）" % (win, port))
        return int(port)
    if zs.window_running(win):
        print("[窗] %s 还在但端口未就绪 → 只等不重开" % win)
    else:
        sid = str(e.get("ziniao_window_store_id") or "")
        if not sid:
            print("[窗] %s 未运行且取不到 storeId → 放弃本店" % win)
            return 0
        print("[窗] %s 未运行 → 自动开窗" % win)
        try:
            zs.open_store(sid)
        except Exception as ex:  # noqa: BLE001
            print("[窗] 开窗失败：%s" % str(ex)[:120])
    t0 = time.time()
    while time.time() - t0 < 90:
        time.sleep(3)
        port = zs.probe_port_by_window(win)
        if port:
            print("[窗] %s 就绪（端口 %s）" % (win, port))
            return int(port)
    print("[窗] %s 90s 内没有端口" % win)
    return 0


def _auth(store: str) -> tuple[int, str]:
    """跑一次授权入口（子进程，避免 import 它改掉本进程 stdout）。"""
    script = os.path.join(_POWER, "run_temu_auth.py")
    if not os.path.exists(script):
        return 3, "找不到 %s" % script
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", script, "--store", store],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        return 124, "授权超时（600s）"
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    lines = [x for x in out.splitlines() if x.strip()]
    return p.returncode, "\n".join(lines[-12:])


def _login_state(port: int, store: str) -> str:
    """真请求判登录态（`LOGIN_OK` / `LOGIN_LOST` / `LOGIN_UNKNOWN`）。

    为什么不信「窗口里有没有授权页」：那页可能是上一次留下的**残影**
    （2026-11-02 实测过，会让 BEL 连续两天白跑）。这里一律用真探针。
    """
    try:
        from auth_temu_driver import probe_login_state  # noqa: PLC0415
        st, url = probe_login_state(int(port), "us", shop=store)
        print("[探针] %s 登录态=%s（落 %s）" % (store, st, str(url)[:110]))
        return str(st)
    except Exception as ex:  # noqa: BLE001
        print("[探针] %s 探测异常：%s" % (store, str(ex)[:120]))
        return "LOGIN_UNKNOWN"


def main() -> int:
    print("[keepalive] TEMU 登录态保鲜开始：%s" % ", ".join(STORES))
    bad = []
    for store in STORES:
        print("\n==== %s ====" % store)
        port = _ensure_window(store)
        if not port:
            bad.append((store, "窗口打不开/无端口"))
            continue
        state = _login_state(port, store)
        if state == "LOGIN_OK":
            # 已登录就别乱点授权：每次多余登录动作都会多一次被风控看到的概率
            print("   ✓ %s 登录态有效，无需授权" % store)
            continue
        rc, tail = _auth(store)
        for ln in tail.splitlines():
            print("   " + ln[:200])
        if rc == 0:
            print("   ✓ %s 登录态已刷新" % store)
            continue
        # ★ run_temu_auth 会在「残留授权页」上白点一遍然后报 FAIL（2026-11-15 BHY 实测：
        #   它报 rc=42，真探针却是 LOGIN_OK）。⇒ 失败后再用真探针复核一次，以探针为准。
        state2 = _login_state(port, store)
        if state2 == "LOGIN_OK":
            print("   ✓ %s 授权脚本报 rc=%s，但探针确认已登录 → 按成功处理" % (store, rc))
        else:
            bad.append((store, "授权 rc=%s（探针 %s）" % (rc, state2)))
    print("\n[keepalive] 结果：%d/%d 成功" % (len(STORES) - len(bad), len(STORES)))
    for store, why in bad:
        print("   ✗ %s：%s" % (store, why))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
