# -*- coding: utf-8 -*-
"""cdp_transport.py — **target-level CDP 传输层**（替代 Playwright `connect_over_cdp`）。

为什么必须换（红线 RL-013，原文）：
    「指纹浏览器(browser-level)不响应 Playwright browser 初始化 → ws connected 仍超时；
      正确 = target-level websockets 直连（`/json/list` 取 `webSocketDebuggerUrl`）；
      **禁止遇连接问题自创脚本**。」
2026-09-14 实测复现：`connect_over_cdp` 连紫鸟店窗口 **45s/180s 超时**（ws 都连上了，初始化不完成），
同一端口 `raw_cdp.js`（原生 CDP）**0.1s** 就连上。故本层照 `scripts/lingxing/lib/oms_cdp.py`
的形态实现（**每次调用新建一条 target-level websocket**，不留长连接、不会残留 attached）。

设计：对外暴露 **Playwright 同形的极薄壳**（`connect(port) -> CdpBrowser`），
只实现购面单驱动真正用到的那几个方法/属性：
    browser.contexts[0].pages / new_page() / new_cdp_session(page)
    page.url / evaluate(script, arg) / goto() / wait_for_load_state() / is_closed() / close()
    page.wait_for_timeout() / page.mouse.click(x, y) / page.keyboard.press(key)
    browser.new_browser_cdp_session()
这样驱动只需把 `sync_playwright().start() + connect_over_cdp` 换成 `cdp_transport.connect(port)`，
其余 1900 行不动 —— 迁移面最小、可回滚。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, Callable, Optional

import requests
import websockets


class CdpError(RuntimeError):
    """连不上调试端口 / 找不到标签页 / CDP 调用失败。"""


# ---------------------------------------------------------------- 低层：HTTP + 一次性 ws 调用
def http_targets(port: int, timeout: float = 10.0) -> list:
    """`/json/list` → 全部 target（页面/worker…）。"""
    try:
        return requests.get("http://127.0.0.1:%d/json/list" % int(port), timeout=timeout).json()
    except Exception as e:  # noqa: BLE001
        raise CdpError("连不上 CDP 端口 %d（窗口没开？）: %s" % (port, e)) from e


def http_version(port: int, timeout: float = 10.0) -> dict:
    """`/json/version` → 浏览器级信息（含 webSocketDebuggerUrl）。"""
    try:
        return requests.get("http://127.0.0.1:%d/json/version" % int(port), timeout=timeout).json()
    except Exception as e:  # noqa: BLE001
        raise CdpError("连不上 CDP 端口 %d（窗口没开？）: %s" % (port, e)) from e


def list_pages(port: int) -> list:
    return [t for t in http_targets(port) if t.get("type") == "page"]


def find_page(port: int, pred: Callable[[str], bool]) -> Optional[dict]:
    """按 URL 谓词找 page target（返回 target dict，含 targetId/webSocketDebuggerUrl/url）。"""
    for t in list_pages(port):
        try:
            if pred(t.get("url") or ""):
                return t
        except Exception:  # noqa: BLE001
            continue
    return None


class Cdp:
    """一条（每次调用新建的）CDP 连接；`send()` 同步语义。"""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url

    def send(self, method: str, params: Optional[dict] = None, timeout: float = 60.0) -> dict:
        async def go():
            async with websockets.connect(self.ws_url, max_size=80 * 1024 * 1024) as ws:
                await ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
                    if m.get("id") == 1:
                        if "error" in m:
                            raise CdpError("%s 失败: %s" % (method, str(m["error"])[:200]))
                        return m
        return asyncio.run(go())

    def detach(self):
        """与 Playwright 的 CDPSession.detach() 同形（本层无长连接，空实现）。"""
        return None


# ---------------------------------------------------------------- 壳：Page / Mouse / Keyboard
class _Mouse:
    def __init__(self, page: "CdpPage"):
        self._p = page

    def click(self, x: float, y: float):
        self._p.dispatch_mouse(float(x), float(y))


class _Keyboard:
    def __init__(self, page: "CdpPage"):
        self._p = page

    def press(self, key: str):
        self._p.dispatch_key(key)


# ---------------------------------------------------------------- locator 同形壳
#: ★ 2026-10-10 加：`auth_temu_driver` / `temu_sales_driver` 里大量使用 Playwright 的
#: `pg.locator(sel)` / `pg.get_by_text(t)` 链式 API（`.count()/.nth()/.first/.click()/
#: `.bounding_box()/.inner_text()/.is_visible()/.is_checked()/.input_value()/.evaluate()`）。
#: 原先只有 `auth_temu_driver.run_auth()` 走**真 Playwright**（`connect_over_cdp`）所以能跑；
#: 而 `connect_over_cdp` 连紫鸟窗口命中 **RL-013**（2026-10-10 实测 `Timeout 15000ms exceeded`，
#: ws 已连、初始化不完成）⇒ 授权整条链走不下去。
#: 现在把这些 API 用 `evaluate` 在 CdpPage 上补齐，驱动就能从 Playwright 切到 target-level。
#:
#: 设计取舍：**返回的是"命中元素的下标数组"的惰性壳**，每次动作都现取 DOM，
#: 不缓存 ElementHandle（CDP 直连没有 handle 概念，缓存反而会在页面跳转后失效）。
_LOCATOR_JS = r"""
(args) => {
  const [kind, sel, text, exact] = args;
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0
        && getComputedStyle(e).visibility !== 'hidden'; };
  let els = [];
  if (kind === 'css') {
    els = Array.from(document.querySelectorAll(sel));
  } else {                                  // 按文本
    const all = Array.from(document.querySelectorAll('*'));
    els = all.filter(e => {
      const t = (e.innerText || '').trim();
      if (!t) return false;
      if (exact) return t === text;
      return t.indexOf(text) >= 0;
    });
    // 只保留"最内层"命中（外层容器也含这段文本，不去掉会点到大框）
    els = els.filter(e => !els.some(o => o !== e && e.contains(o)));
  }
  window.__cdpLoc = els;
  return els.length;
}
"""


class _Locator:
    """Playwright `Locator` 的同形最小壳（只覆盖驱动用到的面）。"""

    def __init__(self, page: "CdpPage", kind: str, sel: str, text: str = "", exact: bool = False,
                 sub: str = ""):
        self._p = page
        self._kind = kind
        self._sel = sel                 # 主选择器 / 主文本
        self._sub = sub                 # `nth(i).locator('td')` 的子选择器（在主元素内再查）
        self._text = text
        self._exact = exact
        self._idx = None                # None=整个集合；int=第 N 个

    # ---- 集合操作
    def _count(self) -> int:
        try:
            return int(self._p.evaluate(_LOCATOR_JS,
                                        [self._kind, self._sel, self._text, self._exact]) or 0)
        except Exception:               # noqa: BLE001
            return 0

    def count(self) -> int:
        if self._idx is not None:
            return 1 if self._idx < self._count() else 0
        return self._count()

    def nth(self, i: int) -> "_Locator":
        o = _Locator(self._p, self._kind, self._sel, self._text, self._exact, sub=self._sub)
        o._idx = int(i)
        return o

    @property
    def first(self) -> "_Locator":
        return self.nth(0)

    def locator(self, sub: str) -> "_Locator":
        """子选择器（`rows.nth(i).locator('td')`）。"""
        o = _Locator(self._p, self._kind, self._sel, self._text, self._exact, sub=sub)
        return o

    # ---- 元素属性 / 动作（都按 _idx **现取 DOM**，不跨调用缓存 —— CDP 每次 evaluate 独立）
    def _js_el(self, body_js: str):
        """在「第 _idx 个命中元素」上跑一段 JS（body 里用 `el`），**每次现查**。

        ⚠ 为什么不用 `window.__cdpLoc` 缓存：每次 `Runtime.evaluate` 是独立执行，
        且页面可能跳转/重渲染，缓存会失效或指到旧节点（实测 bounding_box 恒 None）。
        这里把「查找 + 取值」合并进**同一次** evaluate，天然对应同一时刻的 DOM。
        """
        idx = 0 if self._idx is None else int(self._idx)
        sub = self._sub
        return self._p.evaluate(r"""
        (args) => {
          const [kind, sel, text, exact, idx, sub, body] = args;
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length > 0 && r.width > 0 && r.height > 0
                && getComputedStyle(e).visibility !== 'hidden'; };
          let els = [];
          if (kind === 'css') {
            els = Array.from(document.querySelectorAll(sel));
          } else {
            const all = Array.from(document.querySelectorAll('*'));
            els = all.filter(e => {
              const t = (e.innerText || '').trim();
              if (!t) return false;
              return exact ? (t === text) : (t.indexOf(text) >= 0);
            });
            els = els.filter(e => !els.some(o => o !== e && e.contains(o)));
          }
          let el = els[idx];
          if (!el) return null;
          if (sub) { el = el.querySelector(sub); if (!el) return null; }
          return (new Function('el', 'vis', body))(el, vis);
        }
        """, [self._kind, self._sel, self._text, self._exact, idx, sub, body_js])

    def bounding_box(self):
        return self._js_el(
            "const r = el.getBoundingClientRect();"
            "return {x: r.x, y: r.y, width: r.width, height: r.height};")

    def inner_text(self) -> str:
        return str(self._js_el("return (el.innerText || '').trim();") or "")

    def input_value(self) -> str:
        return str(self._js_el("return el.value || '';") or "")

    def is_visible(self) -> bool:
        return bool(self._js_el("return vis(el);"))

    def is_checked(self) -> bool:
        return bool(self._js_el("return !!el.checked;"))

    def evaluate(self, js: str):
        return self._js_el("return (%s)(el);" % js.strip())

    def scroll_into_view_if_needed(self, timeout: float = 3000):
        self._js_el("el.scrollIntoView({block:'center'}); return true;")
        return None

    def click(self, timeout: float = 30000, **kw):
        """真实鼠标点中心（对齐 `_real_click` 的口径：坐标现取、走 dispatch_mouse）。"""
        box = self.bounding_box()
        if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
            raise CdpError("locator.click: 元素不可见或无尺寸")
        self._p.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        return None


_KEYCODES = {"Escape": 27, "Enter": 13, "Tab": 9, "Backspace": 8, " ": 32}


class CdpPage:
    """Playwright `Page` 的极薄同形壳（只覆盖购面单驱动用到的面）。"""

    def __init__(self, port: int, target: dict):
        self.port = int(port)
        # 键名兼容（2026-09-21 修）：`list_pages()` 直出 `/json/list` 的原始项，键是 `id`；
        # 而早期写法只读 `targetId` ⇒ 经 `ctx.pages` 拿到的 `CdpPage.targetId` 恒为 None
        # （连带 `is_closed()` 恒 False、`close()` 静默失效）。两个键都认。
        self.targetId = target.get("targetId") or target.get("id")
        self._ws = target.get("webSocketDebuggerUrl")
        self._url = target.get("url") or ""
        self.mouse = _Mouse(self)
        self.keyboard = _Keyboard(self)

    # ---- Playwright 同形：locator / get_by_text（2026-10-10 加，见 _Locator 头注）
    def locator(self, sel: str) -> "_Locator":
        """CSS 选择器定位（同形 `Page.locator`）。"""
        return _Locator(self, "css", sel)

    def get_by_text(self, text: str, exact: bool = False) -> "_Locator":
        """按文本定位（同形 `Page.get_by_text`）。`exact=True` 时整串相等。"""
        return _Locator(self, "text", "", text=str(text), exact=bool(exact))

    # ---- 基础
    def _send(self, method: str, params: Optional[dict] = None, timeout: float = 60.0) -> dict:
        if not self._ws:
            t = self._refresh()
            if t is None:
                raise CdpError("标签页已关闭（targetId=%s）" % self.targetId)
        return Cdp(self._ws).send(method, params, timeout)

    def _refresh(self) -> Optional[dict]:
        try:
            for t in list_pages(self.port):
                if t.get("targetId") == self.targetId:
                    self._ws = t.get("webSocketDebuggerUrl")
                    self._url = t.get("url") or self._url
                    return t
        except CdpError:
            return None
        return None

    @property
    def url(self) -> str:
        try:
            return str(self.evaluate("() => location.href") or self._url)
        except Exception:  # noqa: BLE001
            return self._url

    def is_closed(self) -> bool:
        try:
            return not any(t.get("targetId") == self.targetId for t in list_pages(self.port))
        except CdpError:
            return True

    def close(self):
        try:
            Cdp(http_version(self.port)["webSocketDebuggerUrl"]).send(
                "Target.closeTarget", {"targetId": self.targetId})
        except Exception:  # noqa: BLE001
            pass

    def wait_for_timeout(self, ms: float):
        time.sleep(max(0.0, float(ms) / 1000.0))

    # ---- 导航 / 等待
    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: float = 60000):
        self._send("Page.navigate", {"url": url})
        self.wait_for_load_state(wait_until, timeout)
        return None

    def wait_for_load_state(self, state: str = "domcontentloaded", timeout: float = 20000):
        deadline = time.time() + float(timeout) / 1000.0
        want = ["interactive", "complete"] if state != "complete" else ["complete"]
        while time.time() < deadline:
            try:
                rs = self.evaluate("() => document.readyState")
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
                continue
            if str(rs) in want:
                return None
            time.sleep(0.5)
        return None

    # ---- 执行 JS（驱动里 script 一律是箭头函数；有 arg 就直接调用）
    def evaluate(self, script: str, arg: Any = None, timeout: float = 60.0) -> Any:
        expr = "(%s)()" % script if arg is None else "(%s)(%s)" % (
            script, json.dumps(arg, ensure_ascii=False))
        r = self._send("Runtime.evaluate",
                       {"expression": expr, "returnByValue": True,
                        "awaitPromise": True, "userGesture": True}, timeout)
        res = r.get("result") or {}
        if "exceptionDetails" in res:
            txt = str((res.get("exceptionDetails") or {}).get("text"))
            raise CdpError("JS 异常: %s" % txt[:200])
        return ((res.get("result") or {}) or {}).get("value")

    # ---- 原生 CDP（无图/点击/截图都走它）
    def cdp_session(self) -> Cdp:
        return Cdp(self._ws)

    def dispatch_mouse(self, x: float, y: float):
        s = Cdp(self._ws)
        s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                                            "button": "left", "clickCount": 1})
        s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                            "button": "left", "clickCount": 1})

    def dispatch_key(self, key: str):
        s = Cdp(self._ws)
        code = _KEYCODES.get(key, 0)
        for t in ("keyDown", "keyUp"):
            s.send("Input.dispatchKeyEvent", {"type": t, "key": key, "windowsVirtualKeyCode": code,
                                              "nativeVirtualKeyCode": code})

    def screenshot(self, timeout: float = 45.0) -> bytes:
        """截图（PNG）。`timeout` ＝**每次尝试**的预算（调用方可给更小值）。

        2026-09-22 实测坑一：合作仓那张重页上，裸 `{"format":"png"}` 的 `Page.captureScreenshot`
        会**偶发超时**（`str(TimeoutError())` 是空的，日志成了一句「截图失败 : 」）；
        同页改带 `fromSurface: True` 就成功 ⇒ 先带 `fromSurface` 试，失败再裸参数重试一次。
        实测坑二（更要命）：这张页上**截图本身稳定要 45 秒**（返回 280KB，不是超时），
        而同页普通 JS 调用 0.0s ⇒ 截图**绝不能放在主链路上**（见 driver 的 `evidence.screenshot`）。
        """
        last = None
        for params in ({"format": "png", "fromSurface": True}, {"format": "png"}):
            try:
                r = self._send("Page.captureScreenshot", params, max(float(timeout), 1.0))
                data = (r.get("result") or {}).get("data")
                if data:
                    return base64.b64decode(data)
                last = "返回里没有 data"
            except Exception as e:  # noqa: BLE001
                last = str(e)[:80] or type(e).__name__
        raise CdpError("截图失败（已重试）：%s" % last)


class CdpContext:
    """`BrowserContext` 壳：只到 pages / new_page / new_cdp_session。"""

    def __init__(self, port: int, default_url: str = "about:blank"):
        self.port = int(port)
        self.default_url = default_url

    @property
    def pages(self) -> list:
        try:
            return [CdpPage(self.port, t) for t in list_pages(self.port)]
        except CdpError:
            return []

    def new_page(self) -> CdpPage:
        """新开一个**后台**标签页（`Target.createTarget(background=true)`，不抢前台）。

        ⚠ 2026-10-10 修（无人值守实测踩到）：`create_target` 返回 targetId 后，新 target
        **不会立刻出现在 `/json/list`**（注册是异步的）。旧实现只查一次，查不到就回退成
        `{"webSocketDebuggerUrl": None}` 的占位 → 该页任何 `_send` 都抛
        「标签页已关闭（targetId=…）」⇒ 下游 `_navigate_to_orders` 直接失败、
        `run_auth` 报「未找到认证/授权页」⇒ 自动授权整条走不通。
        ⇒ 改成**轮询等待**该 target 出现（≤5s），拿到真正的 `webSocketDebuggerUrl` 再返回。
        """
        tid = create_target(self.port, self.default_url, background=True)
        deadline = time.time() + 5.0
        while True:
            for x in list_pages(self.port):
                # ⚠ `/json/list` 的键是 **`id`**（不是 `targetId`）——旧实现只比 `targetId`
                #    ⇒ 永远匹配不上 ⇒ 回退成 `webSocketDebuggerUrl: None` 的占位页。
                if (x.get("targetId") or x.get("id")) == tid:
                    return CdpPage(self.port, x)
            if time.time() >= deadline:
                break
            time.sleep(0.2)
        # 兜底：仍然没等到（极罕见）→ 返回占位页，由调用方按"标签页已关闭"处理
        return CdpPage(self.port, {"targetId": tid, "webSocketDebuggerUrl": None,
                                   "url": self.default_url})

    def new_cdp_session(self, page: CdpPage) -> Cdp:
        return page.cdp_session()


class CdpBrowser:
    """`Browser` 壳：contexts / close / new_browser_cdp_session。"""

    def __init__(self, port: int):
        self.port = int(port)
        self._ctx = CdpContext(port)

    @property
    def contexts(self) -> list:
        return [self._ctx]

    def new_browser_cdp_session(self) -> Cdp:
        return Cdp(http_version(self.port)["webSocketDebuggerUrl"])

    def close(self):
        return None


def create_target(port: int, url: str = "about:blank", background: bool = True) -> str:
    """浏览器级建标签页（后台）；返回 targetId。"""
    ws = http_version(port)["webSocketDebuggerUrl"]
    r = Cdp(ws).send("Target.createTarget", {"url": url, "background": bool(background)})
    return ((r.get("result") or {}).get("targetId")) or ""


def close_target(port: int, target_id: str) -> bool:
    """按 targetId 关标签页。返回 True 表示它已经不在了（关掉了，或本来就没有）。

    **幂等且永不抛**：这是收尾清理。端口已关 / 页已被别人关掉 / 浏览器正在退出，
    都不该让调用方失败。`CdpPage.close()` 只发 `send` 不看结果，这里补上判定，
    因为调用方需要知道"到底还有没有残留"。
    """
    if not target_id:
        return True
    try:
        ws = http_version(port)["webSocketDebuggerUrl"]
    except Exception:  # noqa: BLE001 - 端口没了 = 窗口已关 = 页自然也没了
        return True
    try:
        Cdp(ws).send("Target.closeTarget", {"targetId": target_id})
    except Exception:  # noqa: BLE001
        pass
    # 复核一次：target 的注册/注销都是异步的，给一点时间再判
    try:
        for _ in range(10):
            if not any((t.get("targetId") or t.get("id")) == target_id
                       for t in list_pages(port)):
                return True
            time.sleep(0.2)
    except Exception:  # noqa: BLE001
        return True
    return False


def connect(port: int, **_kw) -> CdpBrowser:
    """与 `p.chromium.connect_over_cdp(url)` 同形：`connect(port)`。会先探一次端口（快速失败）。"""
    http_version(port)          # 探活：连不上立刻抛，不静默
    return CdpBrowser(port)
