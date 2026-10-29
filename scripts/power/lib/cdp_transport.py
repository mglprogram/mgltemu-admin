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
import atexit
import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

import requests
import websockets


class CdpError(RuntimeError):
    """连不上调试端口 / 找不到标签页 / CDP 调用失败。"""


class ForeignPageError(CdpError):
    """试图改写**复用的（用户的）**标签页。见 `CdpPage.mark_foreign`。

    单独一个类，因为这不是"技术故障"，而是"越界操作" —— 调用方应当改成
    新开自己的页（`CdpPage.open_own_tab`），而不是重试。
    """


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

    #: 标记：这一页是**复用**来的（运行开始前就存在，不是本次新开的）。
    #: 见 `mark_foreign` / `goto` —— 复用的页**不许被导航改写**。
    _reused: bool = False

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

    def mark_foreign(self, why: str = ""):
        """把这个页标成「复用来的（不是本次新开）」→ 之后 `goto` 拒绝改写它。

        用户口径（2026-10-27）：「执行定时任务时打开新标签，不允许把我打开的
        标签覆盖，尤其是 BEL」。

        为什么需要它：驱动里普遍是「找到同类页就复用，没找到才新开」
        （`orders_page()` / `_find()` / `_page_or_open()`）。复用没问题，但紧接着
        常常跟一句 `page.goto(...)` 把 URL 纠正过去 —— 那一步会**改写用户正在看的
        那个标签页**：滚动位置没了、填了一半的表单没了、选中状态没了。
        用户看到的就��「我的页被顶掉了」。

        所以给「复用页」打标记，`goto()` 遇到带标记的页**直接拒绝**并抛
        `ForeignPageError`；调用方必须改为**新开一页**（`open_own_tab`）。
        """
        self._reused = True
        self._reused_why = why
        return self

    def is_foreign(self) -> bool:
        return bool(getattr(self, "_reused", False))

    def wait_for_timeout(self, ms: float):
        time.sleep(max(0.0, float(ms) / 1000.0))

    # ---- 导航 / 等待
    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: float = 60000):
        """导航到 url。**复用页（用户自己的页）上直接拒绝。**

        ⚠ 这是硬闸门，不是提醒：见 `mark_foreign` 的说明。
        需要导航到别处时，请用 `open_own_tab()` 开自己的页，不要改写别人的。
        """
        if self.is_foreign():
            raise ForeignPageError(
                "拒绝改写复用页（用户正在用的标签页）：%s%s"
                % (str(self._url or "")[:80],
                   ("（" + str(getattr(self, "_reused_why", "")) + "）")
                   if getattr(self, "_reused_why", "") else ""))
        self._send("Page.navigate", {"url": url})
        self.wait_for_load_state(wait_until, timeout)
        return None

    def open_own_tab(self, url: str, background: bool = True,
                     wait_until: str = "domcontentloaded",
                     timeout: float = 60000):
        """**新开**一个自己的后台标签页并导航（绝不动现有页）。

        返回 (CdpPage, targetId)。失败抛 `CdpError`。
        """
        tid = create_target(self.port, url or "about:blank", background=background)
        if not tid:
            raise CdpError("open_own_tab: createTarget 没返回 targetId")
        deadline = time.time() + 10.0
        while True:
            for x in list_pages(self.port):
                if (x.get("targetId") or x.get("id")) == tid:
                    pg = CdpPage(self.port, x)
                    pg._reused = False          # 自己开的，可以随便导航
                    try:
                        pg.wait_for_load_state(wait_until, timeout=min(timeout, 30000))
                    except Exception:           # noqa: BLE001
                        pass
                    return pg, tid
            if time.time() >= deadline:
                break
            time.sleep(0.3)
        raise CdpError("open_own_tab: 新页没在 /json/list 出现（targetId=%s）" % tid)

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
    """浏览器级建标签页（后台）；返回 targetId。

    ⚠ 建之前会先 `sweep_leaked_scratch(port)`：把**上一次被强杀**的运行残留的页清掉
    （那些页没人会再关它们了，见 `sweep_leaked_scratch` 的说明）。
    """
    try:
        sweep_leaked_scratch(port)
    except Exception:  # noqa: BLE001 - 清障失败不能影响建页
        pass
    ws = http_version(port)["webSocketDebuggerUrl"]
    r = Cdp(ws).send("Target.createTarget", {"url": url, "background": bool(background)})
    tid = ((r.get("result") or {}).get("targetId")) or ""
    if tid:
        _reg_add(tid, port, url)
    return tid


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
    _reg_drop(target_id)
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


# ---------------------------------------------------------------- 临时页登记表（抗强杀）
#: 为什么需要一张**落盘**的登记表（2026-10-27 用户投诉「你新开标签页用完不关」）：
#:   `finally` 只在**进程正常退出 / 抛异常**时执行。任务被计划任务超时强杀
#:   （结果码 0x41306 / `TerminateProcess`）时，Python 一行收尾代码都跑不到 ——
#:   于是临时页永远留在窗口里。实测证据：两个卡死的「销量分析」页，创建于任务运行时刻、
#:   对 `Runtime.evaluate` 完全不响应（那时进程早被杀了），累计到用户看不过去。
#: 解法：**建页就把 targetId 落盘**，进程死了登记表还在 → 下一次运行开工前扫一遍，
#:   把「登记过、但进程已经不在」的页关掉。这样无论怎么死，最多泄漏到下一次运行。
_SCRATCH_REG = Path(__file__).resolve().parent.parent / "data" / "scratch_tabs.json"


def registered_targets(port: int = 0) -> set:
    """登记表里**我们自己开过**的 targetId 集合（可按端口过滤）。

    用途（2026-10-29 用户事故「为什么覆盖我的页面，然后自己又开了一个新页」）：
      `fetch_focus_spus._heal_hung` 原本把窗口里**所有**判为 hung 的页都关掉 ——
      包括用户正开着的那几个（实测 BHY 窗口 5 个页里 3 个 hung：goods/list、
      一个 www.temu.com 商品页、一个销量分析页）。关完之后 `prepare()` 又新开一张
      销量分析页 ⇒ 用户看到的就是"我的页没了 + 凭空多了个一样的页"。

      判据必须回到归属：**只处理登记过的页**（`create_target` 建页时落盘的），
      登记表里没有的一律当用户的页，永不关闭。
    """
    out = set()
    for t in _reg_load():
        if port and int(t.get("port") or 0) != int(port):
            continue
        tid = str(t.get("tid") or "")
        if tid:
            out.add(tid)
    return out


def _reg_load() -> list:
    """读登记表；坏文件/不存在一律当空表（清障不该因为配置坏了而失败）。"""
    try:
        obj = json.loads(_SCRATCH_REG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    tabs = obj.get("tabs") if isinstance(obj, dict) else obj
    return [t for t in (tabs or []) if isinstance(t, dict) and t.get("tid")]


def _reg_save(tabs: list) -> None:
    try:
        _SCRATCH_REG.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SCRATCH_REG.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"tabs": tabs}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(_SCRATCH_REG)          # 原子替换：并发写不会读到半个文件
    except Exception:  # noqa: BLE001
        pass


def _reg_add(tid: str, port: int, url: str) -> None:
    tabs = _reg_load()
    tabs = [t for t in tabs if t.get("tid") != tid]
    tabs.append({"tid": tid, "port": int(port), "url": str(url or "")[:200],
                 "pid": os.getpid(), "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    _reg_save(tabs[-200:])                  # 只留最近 200 条，防止无限增长


def _reg_drop(tid: str) -> None:
    if not tid:
        return
    tabs = _reg_load()
    left = [t for t in tabs if t.get("tid") != tid]
    if len(left) != len(tabs):
        _reg_save(left)


def _pid_alive(pid: int) -> bool:
    try:
        pid = int(pid or 0)
    except Exception:  # noqa: BLE001
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)                      # Windows 上也会抛 OSError（进程不存在）
        return True
    except OSError:
        return False
    except Exception:  # noqa: BLE001
        return False


def sweep_leaked_scratch(port: int = 0, log=None) -> list:
    """关掉**上一次运行被强杀**时残留的临时页。返回 [(tid, url), …]。

    只关自己登记过的页（登记表里没有的，一律不碰 —— 用户的页永远安全）：
      · 登记表里 `pid` 已死 ⇒ 那是上次被强杀的运行留下的，没人会再关它 ⇒ 关；
      · `pid` 还活着 ⇒ 另一条流水线正在用 ⇒ 不碰（并发的两店抓取就是这样）；
      · 页已经不在了 ⇒ 从登记表删掉。

    三条保底：
      ① 只按 targetId 关，**不按 URL 猜**（用户可能自己开着同一个页面）；
      ② 窗口里至少留 1 个活着的页（关最后一个 ⇒ 整个店铺窗口消失，2026-09-24 事故）；
      ③ 全程 best-effort：探不到端口/关失败都只记日志，绝不抛。
    """
    tabs = _reg_load()
    if not tabs:
        return []
    closed, keep = [], []
    for t in tabs:
        tid, pid = t.get("tid"), t.get("pid")
        if _pid_alive(pid):
            keep.append(t)                   # 同机还有活的进程在用 → 不动
            continue
        # 上一次运行死了 ⇒ 这一页要清。端口可能变了，按登记端口优先、再试当前端口。
        done = False
        for p in ([int(t.get("port") or 0)] + ([int(port)] if port else [])):
            if not p:
                continue
            try:
                pages = list_pages(p)
            except Exception:  # noqa: BLE001
                continue
            if not any((x.get("targetId") or x.get("id")) == tid for x in pages):
                done = True                  # 这一页已经不在了 → 登记可以删
                break
            if len(pages) <= 1:              # 最后一个页：关掉窗口会消失
                keep.append(t)
                done = True
                break
            if close_target(p, tid):
                closed.append((tid, t.get("url") or ""))
                if log:
                    try:
                        log("[页] 清掉上次被强杀残留的临时页：%s" % str(t.get("url"))[:70])
                    except Exception:  # noqa: BLE001
                        pass
                done = True
                break
        if not done:
            keep.append(t)                   # 端口都连不上 → 留到下次
    _reg_save(keep)
    return closed


def _cleanup_own_scratch() -> None:
    """进程正常退出时，把自己还没关的临时页关掉（`finally` 漏了也不至于留）。

    ⚠ 只能覆盖「正常退出」与「可捕获的异常」。**强杀（0x41306）跑不到这里** ——
    那种情况靠 `sweep_leaked_scratch` 在下一次运行时兜底。
    """
    try:
        mine = os.getpid()
        for t in _reg_load():
            if t.get("pid") != mine:
                continue
            port = int(t.get("port") or 0)
            if port:
                try:
                    close_target(port, t.get("tid"))
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass


atexit.register(_cleanup_own_scratch)


def connect(port: int, **_kw) -> CdpBrowser:
    """与 `p.chromium.connect_over_cdp(url)` 同形：`connect(port)`。会先探一次端口（快速失败）。"""
    http_version(port)          # 探活：连不上立刻抛，不静默
    return CdpBrowser(port)


# ---------------------------------------------------------------- 页面卡死检测/恢复
def probe_page_alive(port: int, target: dict, timeout: float = 8.0) -> str:
    """判断一个标签页的**渲染进程**是否还活着。返回 `alive` / `hung` / `gone`。

    为什么需要它（2026-10-27 实测，BEL 逾期任务连续卡死 3 次）：
      紫鸟窗口里那个订单页的**渲染进程会卡死**：浏览器层命令照常 0.1s 返回
      （`Target.getTargetInfo` / `Page.getNavigationHistory` 都正常），但
      `Runtime.enable` 直接 `ConnectionClosedError`、任何 `Runtime.evaluate`
      都 60s `TimeoutError`。表现是：
          `[抽屉] 探测失败（TimeoutError）` → `[FAIL] action=overdue 失败: `（空消息）
          → 重试 3 次全一样 → 45 分钟超时被强杀。
      代码里**没有任何一处**检测这件事，所以只会一遍遍重试同一张死页。

    判据（两层，缺一不可）：
      ① 浏览器层能答（`Target.getTargetInfo`）→ 说明端口/浏览器是好的；
      ② 页面 JS 线程能答（`Runtime.evaluate` 一个常量表达式）→ 说明渲染进程活着。
      ①通②不通 ⇒ `hung`。这是"卡死"与"端口挂了/页被关了"的**唯一区分点**。
    """
    tid = (target or {}).get("targetId") or (target or {}).get("id")
    if not tid:
        return "gone"
    try:
        ws = http_version(port)["webSocketDebuggerUrl"]
    except Exception:  # noqa: BLE001
        return "gone"
    # ① 浏览器层：能答就继续判 ②
    try:
        Cdp(ws).send("Target.getTargetInfo", {"targetId": tid}, timeout=timeout)
    except Exception:  # noqa: BLE001
        return "gone"
    # ② 页面 JS 线程：只求值一个常量，不碰 DOM
    page_ws = (target or {}).get("webSocketDebuggerUrl")
    if not page_ws:
        return "hung"
    try:
        Cdp(page_ws).send("Runtime.evaluate",
                          {"expression": "1", "returnByValue": True}, timeout=timeout)
        return "alive"
    except Exception:  # noqa: BLE001
        return "hung"


def recover_hung_page(port: int, target: dict, log=None, timeout: float = 8.0,
                      require_other_tabs: int = 1) -> str:
    """把卡死的标签页**关掉**。返回 `closed` / `alive` / `gone` / `last-tab`。

    ⚠ 2026-10-27 实测的**严重副作用**：如果这是窗口里**最后一个**真实标签页，
      关掉它会连带把整个紫鸟店铺窗口带走（渲染进程回收 → 窗口消失 → 端口没了）。
      所以默认要求"关完之后窗口里还剩至少 require_other_tabs 个页"，
      否则**不关**，返回 `last-tab`，由调用方改用"导航修复"（`navigate_to`）。

    只关、不重开：重开由调用方决定。关掉之后浏览器的渲染进程会被回收，
    **端口号可能变化**（实测 58538 → 60091），调用方必须重探端口。
    """
    st = probe_page_alive(port, target, timeout=timeout)
    if st != "hung":
        return st
    tid = (target or {}).get("targetId") or (target or {}).get("id")
    u = str((target or {}).get("url") or "")
    # 保底：不能把窗口里最后一个页关掉
    try:
        others = [x for x in list_pages(port)
                  if ((x.get("targetId") or x.get("id")) != tid)]
    except Exception:  # noqa: BLE001
        others = []
    if len(others) < max(1, int(require_other_tabs)):
        if log:
            try:
                log("[页] ⚠ 卡死页是窗口里最后一个真实页 → **不关**（关掉会让整个店铺窗口消失）；"
                    "改用导航修复：%s" % u[:70])
            except Exception:  # noqa: BLE001
                pass
        return "last-tab"
    if log:
        try:
            log("[页] ⚠ 检测到渲染进程卡死（浏览器层正常、JS 线程无响应）→ 关掉重开：%s"
                % u[:76])
        except Exception:  # noqa: BLE001
            pass
    # `# noqa: tab-safety`：这是**底层原语**，只关调用方点名的那一个 target（`tid`
    # 来自参数，不是"遍历窗口随便挑"）。归属判定必须由调用方做 —— 见
    # `fetch_focus_spus._heal_hung`（已改为先 `registered_targets()` 过滤）。
    close_target(port, tid)  # noqa: tab-safety 原语，关的是调用方点名的 tid
    return "closed"


def replace_hung_page(port: int, target: dict, url: str, log=None,
                      wait_s: float = 15.0) -> str:
    """**先开新页、再关死页** —— 卡死页的安全替换法。返回 `replaced`/`alive`/`gone`/`failed`。

    为什么是这个顺序（2026-10-27 实测，三种方案都试过）：
      · `Page.navigate` 重载 → **无效**。它要渲染进程应答，而 JS 线程正是死的。
      · 直接关死页 → **危险**。若它是窗口里唯一一页，整个紫鸟店铺窗口会消失
        （实测：窗口没了、端口没了，得手工重开 + 重新授权）。
      · **先开新页 → 再关死页 → 窗口存活** ✅（实测端口仍在、页面正常）。
    原理：关页时窗口里已经有一个活的页，浏览器不会把整个渲染进程回收掉。

    返回 `replaced` 表示已换成一张可用的新页。
    """
    tid = (target or {}).get("targetId") or (target or {}).get("id")
    if not tid:
        return "gone"
    st = probe_page_alive(port, target)
    if st != "hung":
        return "alive" if st == "alive" else "gone"

    if log:
        try:
            log("[页] ⚠ 渲染进程卡死（浏览器层正常、JS 线程无响应）→ 先开新页再关死页：%s"
                % str(url)[:70])
        except Exception:  # noqa: BLE001
            pass

    # ① 先开新页
    try:
        new_tid = create_target(port, url or "about:blank", background=True)
    except Exception as e:  # noqa: BLE001
        if log:
            try:
                log("[页] 开新页失败：%s" % str(e)[:80])
            except Exception:  # noqa: BLE001
                pass
        return "failed"
    if not new_tid:
        return "failed"

    # ② 等新页可用（能答 JS）
    waited = 0.0
    ok = False
    while waited < wait_s:
        time.sleep(2)
        waited += 2
        cand = next((x for x in list_pages(port)
                     if (x.get("targetId") or x.get("id")) == new_tid), None)
        if cand is not None and probe_page_alive(port, cand, timeout=5) == "alive":
            ok = True
            break

    # ③ 再关死页（此时窗口里已有活页，不会连带把窗口搞没）
    # `# noqa: tab-safety`：底层原语，关的是参数点名的死页；归属由调用方判
    # （`_heal_hung_orders_page` 用 `self._own_targets()` 过滤后再调）。
    try:
        close_target(port, tid)  # noqa: tab-safety 原语，关参数点名的 tid
    except Exception:  # noqa: BLE001
        pass

    if log:
        try:
            log("[页] 死页已替换（新页 %s，可用=%s）" % (new_tid[:10], ok))
        except Exception:  # noqa: BLE001
            pass
    return "replaced" if ok else "failed"


def navigate_hung_page(port: int, target: dict, url: str, log=None,
                       wait_s: float = 12.0) -> bool:
    """**不关页**的修复：用浏览器级 `Page.navigate` 把卡死页重新加载。

    为什么需要它：关掉窗口里最后一个页会带走整个店铺窗口（见
    `recover_hung_page`）。而 `Page.navigate` 走的是**浏览器进程**，不需要
    渲染进程的 JS 线程 —— 卡死的页也能被它重新导航，于是 JS 线程跟着重建。

    返回 True 表示导航后页面恢复响应。
    """
    tid = (target or {}).get("targetId") or (target or {}).get("id")
    if not tid:
        return False
    try:
        ws = http_version(port)["webSocketDebuggerUrl"]
    except Exception:  # noqa: BLE001
        return False
    if log:
        try:
            log("[页] 用导航修复卡死页（不关页）：%s" % str(url)[:76])
        except Exception:  # noqa: BLE001
            pass
    try:
        Cdp(ws).send("Page.navigate", {"url": url}, timeout=15)
    except Exception:  # noqa: BLE001
        return False
    # 等它重建 JS 线程
    waited = 0.0
    while waited < wait_s:
        time.sleep(2)
        waited += 2
        t2 = next((x for x in list_pages(port)
                   if (x.get("targetId") or x.get("id")) == tid), None)
        if t2 is None:
            return False
        if probe_page_alive(port, t2, timeout=5) == "alive":
            return True
    return False


def find_port_after_recovery(window_name: str) -> int:
    """渲染进程被回收后端口会变，按窗口名重探一次。"""
    try:
        import subprocess
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or ""
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process ziniaobrowser | Where-Object { $_.MainWindowTitle -like '%s*' } "
             "| Select-Object -ExpandProperty Id" % window_name],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or ""
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
                p = int(parts[1].rsplit(":", 1)[1])
            except (IndexError, ValueError):
                continue
            if not (10000 < p < 70000):
                continue
            try:
                v = http_version(p, timeout=2)
                if v.get("webSocketDebuggerUrl"):
                    return p
            except Exception:  # noqa: BLE001
                continue
    return 0


#: 我们**有权清理**的域名（商家中心侧）。刻意**不含**消费站：
#:   · `www.temu.com`（买家购物页，用户常在开）
#:   · `us.shein.com` / 其它外部站（用户自己的页）
#: 也没包含扩展页 / about:blank。判据是 host 精确匹配，避免误伤。
SELLER_HOSTS = (
    "agentseller-us.temu.com",
    "agentseller.temu.com",
    "seller.kuajingmaihuo.com",
)

#: **不许清理**的落地页路径片段。这些是"可用页"，关掉就等于把成果扔掉。
#:
#: ⚠ 2026-10-27 实测事故：授权流程里 `_navigate_to_orders()` 开的订单页**正是**
#:   本次新开的页，清理器照规矩把它关掉了 ⇒ 窗口退回唯一的扫码登录页 ⇒
#:   授权"成功"了但窗口不可用，下一次又要重新授权。
#:   ⇒ 清理前必须排除这些"任务目标页"。
#:
#: ⚠ 2026-10-28 实测事故（用户口径）：用户在任务**运行期间**打开了「新建商品」
#:   （`agentseller.temu.com/goods/edit?productId=…`），收尾清理把它当成"本次新开的页"
#:   关掉了。原因是旧的判据用「启动时不存在 ⇒ 是我开的」，而用户中途开的页完全符合
#:   这个特征 —— 分不出来。⇒ 现在改成**只关能证明是自己开的**（见 `_is_ours`），
#:   并把商家中心的**商品工作面**加进保留清单做第二道保险。
KEEP_PATH_HINTS = (
    "/mmsos/orders.html",
    "/mmsos/export-order.html",
    "/mmsos/waybill.html",
    "/mmsos/",
    "/main/",
    # 商品工作面：用户日常就在这些页上编辑，绝不能碰。
    "/goods/",          # 商品列表 / 新建商品 / 编辑商品（goods/list、goods/edit）
    "/newon/",          # 上新流程（newon/product-select）
    "/product/",        # 商品详情/选择
)

#: **可以确定是脚本临时页**的路径片段（授权中间页）。
#:
#: 这些页是**站点自己**在我们的点击之后弹出来的（不是 `create_target` 建的），
#: 所以登记表和 openerId 都证明不了归属。但它们是纯过程页、用户不会在上面干活，
#: 且不关掉就会越堆越多（每次授权都留一张登录页）。
#: ⇒ 只有命中这里的"无人区"才允许在没有归属证据时关掉。
SCRATCH_PATH_HINTS = (
    "/auth/authentication",
    "/settle/seller-login",
    "/seller-login",
)


def _is_keep_path(url: str) -> bool:
    """这个 URL 是不是"任务目标页 / 用户工作面"，不许被清理掉。"""
    try:
        from urllib.parse import urlparse
        p = (urlparse(str(url or "")).path or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return any(h in p for h in KEEP_PATH_HINTS)


def _is_scratch_path(url: str) -> bool:
    """这个 URL 是不是"纯过程页"（授权中间页），用户不会在上面干活。"""
    try:
        from urllib.parse import urlparse
        p = (urlparse(str(url or "")).path or "").lower()
    except Exception:  # noqa: BLE001
        return False
    if str(url or "").strip().lower() in ("about:blank", ""):
        return True
    return any(h in p for h in SCRATCH_PATH_HINTS)


def snapshot_ids(port: int) -> set:
    """取当前所有 page target 的 id 集合（收尾时用来判断「哪些页是本次新开的」）。"""
    try:
        return {(t.get("targetId") or t.get("id")) for t in list_pages(port)} - {None}
    except Exception:  # noqa: BLE001 - 端口不可用 ⇒ 当作空，不影响调用方
        return set()


def _norm_url(u) -> str:
    """把 URL 规范化到「同一条地址」的口径（去 fragment、host/path 小写）。

    为什么要比 URL（2026-10-27 加）：Chrome 重载/崩溃恢复会让**同一页换一个
    targetId**。只比 id 的话，用户本来开着的页会被误判成「本次新开」而被关掉。
    多记一份 URL 就多一道保险：运行前已经存在的地址，一律不关。
    """
    try:
        from urllib.parse import urlparse
        p = urlparse(str(u or ""))
        return ("%s%s" % ((p.netloc or "").lower(), (p.path or "").lower())).rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


def snapshot_urls(port: int) -> set:
    """取当前所有 page target 的 URL 集合（规范化后）。"""
    try:
        return {_norm_url(t.get("url")) for t in list_pages(port)} - {""}
    except Exception:  # noqa: BLE001
        return set()


def snapshot_tabs(port: int) -> tuple[set, set]:
    """一次取回 (ids, urls) 作为收尾基线。**调用方应在运行开始前调用一次。**

    两者都要：
      · ids  —— 正常情况下的「本次新开」判据；
      · urls —— Chrome 重载/恢复会让同一页换 targetId，光比 id 会误关用户的页。
    """
    try:
        pages = list_pages(port)
    except Exception:  # noqa: BLE001
        return set(), set()
    ids = {(t.get("targetId") or t.get("id")) for t in pages} - {None}
    urls = {_norm_url(t.get("url")) for t in pages} - {""}
    return ids, urls


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return (urlparse(str(url or "")).netloc or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _openers_of(port: int) -> dict:
    """targetId → openerId（站点自己弹出来的页会带 opener，能证明"从我们的页开的"）。

    取不到就返回空表 —— 归属判断退化成"只认登记表"，宁可少关，不可错关。
    """
    try:
        ws = http_version(port)["webSocketDebuggerUrl"]
        r = Cdp(ws).send("Target.getTargets")
        out = {}
        for t in ((r.get("result") or {}).get("targetInfos") or []):
            tid = t.get("targetId")
            if tid:
                out[tid] = t.get("openerId") or ""
        return out
    except Exception:  # noqa: BLE001 - 拿不到归属线索 ⇒ 少关，不影响业务
        return {}


def close_new_seller_pages(port: int, before_ids: set, keep_min_tabs: int = 1,
                           log=None, before_urls: set | None = None,
                           owned_ids: set | None = None) -> list:
    """关掉**本次运行自己打开的、商家中心域**的标签页。用完即关，且绝不动用户的页。

    为什么需要它（2026-10-27 用户口径「每触发任务打开的新页面，用完即关，
    不能动我正在使用的页面」）：
      驱动里开页有两种写法 —— `ctx.new_page()`（新 tab）和 `page.goto()`
      （**改写现有页**）。前者不开完就没人关，后者会动用户的页。
      单靠"记得在 finally 里关"靠不住（每个入口都要记得）。

    ⚠ 2026-10-28 根本性修正：旧判据是**「启动时不存在 ⇒ 是我开的」**，这在
      用户**运行期间**打开新标签页时会把用户的页误判成自己的 —— 实测把用户正在
      编辑的「新建商品」页（`/goods/edit?productId=…`）当临时页关掉了。
      现在改成**只关能证明是自己开的**：

        ① `owned_ids` / 登记表：`create_target()` 建的页会把 targetId 落盘（带 pid），
           这是最硬的证据；
        ② `openerId` 归属：站点从**我们的页**弹出来的页（如确认发货后的 waybill），
           opener 指向我们的 targetId，也算我们开的；
        ③ 纯过程页（`SCRATCH_PATH_HINTS`：授权/登录中间页、about:blank）——
           用户不会在上面干活，且没有归属线索也允许清，否则每次授权都留一张。

      「新开 + 商家中心域 + 非保留路径」只是**必要条件**，不再是充分条件。

    四条硬约束：
      1. **只在 before_ids 之外的**（＝本次新出现的）里挑，运行前就存在的一律不碰；
      2. **URL 在 before_urls 里的也不关** —— Chrome 重载会让同一页换 targetId；
      3. **只关商家中心域**（`SELLER_HOSTS`）。用户的 `www.temu.com` 购物页、
         `us.shein.com`、扩展页一律不碰；
      4. **窗口里至少留 keep_min_tabs 个真实页** —— 2026-09-24 事故：清障把窗口
         唯一一个真实页关掉 ⇒ 整个店铺窗口消失 ⇒ 接着连死端口。

    返回被关掉的 [(targetId, url), …]，供日志留痕。
    """
    closed = []
    try:
        pages = list_pages(port)
    except Exception:  # noqa: BLE001
        return closed
    before_urls = before_urls or set()

    # ---- 归属证据 ----
    owned = set(owned_ids or ())
    try:
        me = os.getpid()
        for t in _reg_load():
            if t.get("pid") == me and t.get("tid"):
                owned.add(t["tid"])
    except Exception:  # noqa: BLE001
        pass
    openers = _openers_of(port)

    def _is_ours(t: dict) -> bool:
        """能证明这一页是本次运行自己开的吗？证不出来 ⇒ 当作用户的页，不关。"""
        tid = t.get("targetId") or t.get("id")
        if not tid:
            return False
        if tid in owned:
            return True
        # 站点从我们的页弹出来的（opener 指向自己的页）
        op = openers.get(tid) or ""
        return bool(op and op in owned)

    fresh = [t for t in pages
             if (t.get("targetId") or t.get("id")) not in before_ids]
    # 商家中心域 + 运行前没开过这个地址 + 不是用户工作面
    todo = [t for t in fresh
            if _host_of(t.get("url")) in SELLER_HOSTS
            and _norm_url(t.get("url")) not in before_urls
            and not _is_keep_path(t.get("url"))
            and (_is_ours(t) or _is_scratch_path(t.get("url")))]
    if not todo:
        return closed
    # 保底：窗口里真实页太少就不关
    remaining = len(pages) - len(todo)
    if remaining < keep_min_tabs:
        todo = todo[:max(0, len(pages) - keep_min_tabs)]
    for t in todo:
        tid = t.get("targetId") or t.get("id")
        u = str(t.get("url") or "")
        if not tid:
            continue
        if close_target(port, tid):
            closed.append((tid, u))
            if log:
                try:
                    log("[页] 已关本次新开的页：%s" % u[:76])
                except Exception:  # noqa: BLE001
                    pass
    return closed
