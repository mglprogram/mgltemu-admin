# -*- coding: utf-8 -*-
"""TEMU 窗口登录/授权（CDP 版）——给无人值守的定时抓取用。

为什么不用 skill 里的 `run_temu_auth.py`：
  它走 Playwright `connect_over_cdp`，而指纹浏览器**不响应 browser-level 初始化**
  （仓库 `cdp_transport.py` 红线 RL-013 已记录），实测必然 15s 超时。
  这里改成 target-level 原生 CDP，并且：

  1) 认证页的区域入口**只认真实鼠标事件**（JS `.click()` 完全无响应）；
  2) 整组鼠标事件**必须在同一条 websocket 上发** —— 分连接发时浏览器不合成点击。

真实流程：认证页点「中国地区·商家中心」→ 授权确认页勾条款 → 点「授权登录 / 确认授权并前往」。

只做登录推进，不做任何业务写操作。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 授权确认按钮候选（文本因店而异）
# 「登录」放最后：BHY 的 settle/seller-login 实测按钮就是「登录」（2026-10-10），
# 账密由紫鸟自动填好后点它即完成授权；更具体的文案优先试，避免误点。
AUTH_BUTTONS = ["确认授权并前往", "授权登录", "授权并前往", "确认授权", "确认登录", "登录"]


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 端口发现
def find_store_port(window_name: str) -> int:
    """按紫鸟窗口名（进程 MainWindowTitle 前缀）探测 CDP 端口。

    前缀匹配：授权后窗口标题会变成「TM-BHY-YJF | 卖家中心」，精确匹配会找不到。
    """
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=20).stdout or ""
    except Exception:  # noqa: BLE001
        return 0
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process ziniaobrowser | Where-Object { $_.MainWindowTitle -like '%s*' } "
             "| Select-Object -ExpandProperty Id" % window_name],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=25).stdout or ""
    except Exception:  # noqa: BLE001
        return 0

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
            except Exception:  # noqa: BLE001
                continue
            if 10000 < port < 70000 and _is_cdp(port):
                return port
    return 0


def _is_cdp(port: int) -> bool:
    try:
        v = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%s/json/version" % port, timeout=2).read())
        return bool(v.get("webSocketDebuggerUrl"))
    except Exception:  # noqa: BLE001
        return False


def list_tabs(port: int) -> list[dict]:
    try:
        tabs = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%s/json/list" % port, timeout=4).read())
        return [t for t in tabs if t.get("type") == "page"]
    except Exception:  # noqa: BLE001
        return []


def find_tab(port: int, *needles: str) -> dict | None:
    """按 URL 子串找一个 tab（多个 needle 需全部命中）。"""
    for t in list_tabs(port):
        url = t.get("url") or ""
        if all(n in url for n in needles):
            return t
    return None


# ---------------------------------------------------------------- 底层：一条长连接
def _run_ws(ws: str, calls: list[tuple[str, dict]], timeout: float = 15.0) -> list:
    """在**同一条** websocket 上顺序执行若干 CDP 调用，返回各次结果。"""
    import websockets

    async def go():
        results = []
        async with websockets.connect(ws, max_size=40 * 1024 * 1024) as sock:
            for i, (method, params) in enumerate(calls, 1):
                await sock.send(json.dumps({"id": i, "method": method, "params": params}))
                while True:
                    m = json.loads(await asyncio.wait_for(sock.recv(), timeout=timeout))
                    if m.get("id") == i:
                        results.append(m)
                        break
        return results

    return asyncio.run(go())


def js(ws: str, expr: str):
    """在指定 tab 上求值（箭头函数字符串）。"""
    r = _run_ws(ws, [("Runtime.evaluate", {
        "expression": "(%s)()" % expr, "returnByValue": True, "awaitPromise": True,
        "userGesture": True})])
    res = (r[0].get("result") or {}).get("result") or {}
    return res.get("value")


def real_click(ws: str, x: float, y: float) -> None:
    """真实鼠标点击：moved/pressed/released 同连接顺序发。"""
    _run_ws(ws, [
        ("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}),
        ("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                                      "button": "left", "buttons": 1, "clickCount": 1}),
        ("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                      "button": "left", "buttons": 0, "clickCount": 1}),
    ])


# ---------------------------------------------------------------- 页面判断
REGION_JS = """() => {
  const sels = ['div[class*=authentication_regionItem]',
                'div[class*=authentication_regionSuffix]',
                'div[class*=authentication_goto]'];
  for (const s of sels) {
    for (const el of document.querySelectorAll(s)) {
      if (String(el.className).includes('disabled')) continue;
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      return JSON.stringify({ x: Math.round(r.x + r.width / 2),
                              y: Math.round(r.y + r.height / 2),
                              txt: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 24) });
    }
  }
  return JSON.stringify({ err: 'no region entry' });
}"""

CHECK_TERMS_JS = """() => {
  const cb = document.querySelector('input[type=checkbox]');
  if (!cb) return JSON.stringify({ ok: false, msg: 'no checkbox' });
  if (!cb.checked) {
    let lbl = cb.closest('label');
    if (!lbl) {
      let p = cb.parentElement;
      for (let i = 0; i < 5 && p; i++) {
        if (/CBX_|checkbox/i.test(String(p.className))) { lbl = p; break; }
        p = p.parentElement;
      }
    }
    (lbl || cb.parentElement).click();
  }
  return JSON.stringify({ ok: true, checked: document.querySelector('input[type=checkbox]').checked });
}"""

CLICK_AUTH_JS = """() => {
  const CANDS = %s;
  const btns = Array.from(document.querySelectorAll('button,[class*=btn],[class*=Btn]'))
    .filter(e => e.offsetParent !== null && (e.innerText || '').trim());
  for (const txt of CANDS) {
    const b = btns.find(e => (e.innerText || '').trim() === txt);
    if (b) {
      const r = b.getBoundingClientRect();
      const o = { bubbles: true, cancelable: true, clientX: r.x + r.width / 2,
                  clientY: r.y + r.height / 2, button: 0, view: window };
      b.dispatchEvent(new MouseEvent('mousedown', o));
      b.dispatchEvent(new MouseEvent('mouseup', o));
      b.dispatchEvent(new MouseEvent('click', o));
      return JSON.stringify({ ok: true, clicked: txt });
    }
  }
  return JSON.stringify({ ok: false, msg: 'no auth button',
    seen: btns.map(e => (e.innerText || '').trim().slice(0, 20)).slice(0, 8) });
}""" % json.dumps(AUTH_BUTTONS, ensure_ascii=False)


def is_seller_page(url: str) -> bool:
    """是否已登录（页面不是认证/登录页）。

    判据用**否定式**：只要能打开任意 agentseller 卖家中心页面、且没被重定向到
    auth/login，就算登录有效。之前用肯定式（只认 /mmsos/ 或 /main/）会漏判 ——
    例如 `agentseller.temu.com/goods/list`、`newon/product-select`、`lgst/**`
    都是已登录页，但都不在那两个前缀里，会被误判成"未登录"而去做无谓的授权
    （2026-10-12 实测：BHY 实际是登录态，却反复走授权并失败）。
    """
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if u.netloc not in ("agentseller-us.temu.com", "agentseller.temu.com"):
        return False
    p = (u.path or "").lower()
    if not p or p == "/":
        return False
    # 认证/登录类路径一律不算
    if "auth" in p or "login" in p:
        return False
    return True


def ensure_logged_in(port: int, store: str, timeout: float = 180.0) -> tuple[bool, str]:
    """确保该窗口已登录到商家中心（必要时走授权流程）。

    返回 (是否成功, 说明)。不做任何业务写操作。
    """
    deadline = time.time() + timeout

    def any_seller() -> str:
        for t in list_tabs(port):
            if is_seller_page(t.get("url") or ""):
                return t.get("url") or ""
        return ""

    url = any_seller()
    if url:
        return True, "已登录（%s）" % url[:70]

    # 找认证/授权页。注意：授权是**多步**的，可能已经停在
    # `settle/seller-login`（第 1 或第 2 步）——此时不需要回到区域选择页，
    # 直接从多步循环接管即可（之前只看认证页，停在中间步时会被误判成"没登录页"）。
    auth = find_tab(port, "auth/authentication")
    already_midway = bool(find_tab(port, "settle/seller-login"))
    if not auth and not already_midway:
        return False, "既不在商家中心页，也没有认证/授权页（窗口可能没打开）"

    if auth:
        log("  [auth] 认证页 → 点「中国地区·商家中心」")
        rc = js(auth["webSocketDebuggerUrl"], REGION_JS)
        try:
            coords = json.loads(rc) if isinstance(rc, str) else rc
        except Exception:  # noqa: BLE001
            coords = {}
        if not coords.get("x"):
            return False, "认证页找不到区域入口：%s" % rc
        real_click(auth["webSocketDebuggerUrl"], coords["x"], coords["y"])
        log("  [auth] 已点击「%s」" % coords.get("txt", ""))
    else:
        log("  [auth] 已停在授权确认页，直接从当前步骤继续")

    # 授权确认页出现（可能是同一个 tab 跳转，也可能开新 tab）
    seller = None
    while time.time() < deadline:
        time.sleep(2)
        if any_seller():
            return True, "授权后已进入商家中心"
        seller = find_tab(port, "settle/seller-login")
        if seller:
            break
    if not seller:
        return False, "点了区域入口后未出现授权确认页"

    # **授权是多步的**（2026-10-10 BHY 实测）：
    #   第 1 步「手机号/邮箱登录」→ 按钮「授权登录」
    #   第 2 步「即将前往 Seller Central (美国) 的「XXX」店铺」→ 勾条款 + 按钮「确认授权并前往」
    # 所以要在同一个 settle/seller-login tab 上循环：勾条款 → 点当前可用按钮 → 看是否推进，
    # 直到落到商家中心可用页或步数用尽。
    seen_buttons: list[str] = []
    for round_no in range(1, 5):
        if any_seller():
            return True, "授权成功（第 %d 步后进入商家中心）" % (round_no - 1)

        seller = find_tab(port, "settle/seller-login")
        if not seller:
            break
        ws = seller["webSocketDebuggerUrl"]

        # 每步都要重新勾（新步骤的复选框是未勾选状态）
        js(ws, CHECK_TERMS_JS)
        time.sleep(1)
        r = js(ws, CLICK_AUTH_JS)
        try:
            parsed = json.loads(r) if isinstance(r, str) else (r or {})
        except Exception:  # noqa: BLE001
            parsed = {}
        if not parsed.get("ok"):
            # 点完上一步后页面可能正在跳转（此时旧 tab 会短暂变成空白：
            # inputs/checkbox/button 全为 0）。不能立刻判失败，先给时间看是否已登录。
            log("  [auth] 第%d步暂未找到按钮，等待页面跳转…" % round_no)
            wait_until = min(deadline, time.time() + 30)
            while time.time() < wait_until:
                time.sleep(2)
                if any_seller():
                    return True, "授权成功（点了 %s）" % " → ".join(seen_buttons)
            # 页面可能又回到认证页（会话过期重来），交给上层重新导航
            return False, "第%d步无可点按钮（页面可能已跳转或回到认证页）：%s" % (
                round_no, parsed.get("seen") or parsed.get("msg"))
        seen_buttons.append(str(parsed.get("clicked")))
        log("  [auth] 第%d步 → 点了「%s」" % (round_no, parsed.get("clicked")))

        # 等这一步的跳转/重渲染
        step_deadline = min(deadline, time.time() + 45)
        while time.time() < step_deadline:
            time.sleep(2)
            if any_seller():
                return True, "授权成功（点了 %s）" % " → ".join(seen_buttons)

    if any_seller():
        return True, "授权成功（点了 %s）" % " → ".join(seen_buttons)
    return False, "已点 %s，但仍未进入商家中心" % " → ".join(seen_buttons or ["(无)"])


def navigate(ws: str, url: str) -> None:
    """在该 tab 上导航（CDP Page.navigate）。"""
    try:
        _run_ws(ws, [("Page.navigate", {"url": url})], timeout=20)
    except Exception as e:  # noqa: BLE001
        log("  [nav] 导航异常：%s" % str(e)[:80])


CARDS_JS = """() => document.querySelectorAll('.quick-overdue-filter_card__7x5wV').length"""


def ensure_flux_page(port: int, url: str, timeout: float = 120.0):
    """把窗口里某个 tab 导航到商品流量页并**验证真的渲染出来了**。

    为什么要验证：桥接的「当前标签页」与 CDP 找到的 tab 可能不是同一个，
    会出现「URL 是 flux-analysis 但内容是空白/别的页面」的情况 ——
    这时抓取脚本找不到快速筛选卡片，若还照写产物就会把好数据顶成空。

    授权是多步的（区域选择 → 登录 → 确认授权并前往），中途页面会跳转变空白；
    所以整轮「导航 → 需要则授权 → 等卡片」最多重试 `attempts` 次。

    返回 (targetId 或 "", 说明)。
    """
    last = "未开始"
    for attempt in range(1, 4):
        tid, last = _ensure_flux_once(port, url, timeout)
        if tid:
            return tid, last
        log("  [flux] 第%d轮未就绪：%s" % (attempt, last))
        time.sleep(5)
    return "", last


def _ensure_flux_once(port: int, url: str, timeout: float):
    # 优先复用已有 flux tab，否则拿第一个 page tab
    t = find_tab(port, "flux-analysis") or (list_tabs(port) or [None])[0]
    if not t:
        return "", "窗口里没有可用的 page tab"

    ws = t.get("webSocketDebuggerUrl")
    if not ws:
        t2 = find_tab_by_id(port, t.get("id") or "")
        ws = (t2 or {}).get("webSocketDebuggerUrl")
    if not ws:
        return "", "tab 没有 websocket 调试地址"

    navigate(ws, url)
    time.sleep(5)

    # 未登录 → 会跳到认证页，此时走授权
    cur = ""
    try:
        cur = js(ws, "() => location.href") or ""
    except Exception:  # noqa: BLE001
        pass
    if "auth/authentication" in cur or "login" in cur.lower():
        log("  [flux] 页面跳到登录/认证：%s" % cur[:70])
        ok, msg = ensure_logged_in(port, "", timeout=timeout)
        if not ok:
            return "", msg
        # 授权后重新定位并导航
        t = find_tab(port, "flux-analysis") or (list_tabs(port) or [None])[0]
        ws = (t or {}).get("webSocketDebuggerUrl")
        if not ws:
            return "", "授权后找不到可用 tab"
        navigate(ws, url)

    # 轮询等卡片出现
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        try:
            n = js(ws, CARDS_JS)
        except Exception:  # noqa: BLE001
            n = 0
        if n and int(n) > 0:
            # 重新按 URL 取 id（导航后可能已换 targetId）
            t2 = find_tab(port, "flux-analysis")
            tid = (t2 or {}).get("id") or t.get("id") or ""
            return tid, "页面就绪（%s 个快速筛选卡片）" % n
    return "", "等待快速筛选卡片超时（页面可能没真正加载）"


def find_tab_by_id(port: int, tid: str) -> dict | None:
    for t in list_tabs(port):
        if t.get("id") == tid:
            return t
    return None


if __name__ == "__main__":
    # 自测：python flux_auth.py <窗口名> [店铺后缀]
    win = sys.argv[1] if len(sys.argv) > 1 else "TM-BHY-YJF"
    p = find_store_port(win)
    print("port =", p)
    if p:
        for t in list_tabs(p):
            print("  ", (t.get("url") or "")[:110])
        ok, msg = ensure_logged_in(p, win)
        print(("OK: " if ok else "FAIL: ") + msg)