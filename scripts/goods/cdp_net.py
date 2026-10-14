# -*- coding: utf-8 -*-
"""cdp_net.py — 原生 CDP 网络抓包（target 级，不依赖页面内 JS 钩子）。

为什么要有它（2026-10-12 现场实测）：
    agentseller.temu.com 是 qiankun 微前端（`window.__POWERED_BY_QIANKUN__`），
    子应用跑在沙箱里，用的是**沙箱代理过的 fetch/xhr**；从页面顶层 `page exec` 里
    改写 `window.fetch` / `window.XMLHttpRequest` **抓不到子应用的请求**
    （实测点击「查询」后 `window.__cap` 恒为空，而 PerformanceObserver 能看到
    `/visage-agent-seller/product/skc/pageQuery` 请求确实发出了）。
    页面内的同步 XHR 又缺少风控头（`anti-content`），直调一律 403 No Permission。

    ⇒ 改用**协议层抓包**：CDP `Network.*` 事件与页面脚本无关，
    既能拿到页面自己发出的请求（含风控头），也能拿到完整响应体。

实现：一条常驻 websocket，边收事件边按 id 发命令（一次 `Runtime.evaluate` 触发动作）。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional
from urllib.request import urlopen

import websockets


def http_json(url: str, timeout: float = 8.0):
    with urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


async def new_tab(port: int, url: str = "about:blank", foreground: bool = True) -> str:
    """浏览器级建一个标签页，返回 targetId（异步：调用方已在事件循环里）。

    ⚠ `background=True` 建的页会被 Chrome 节流：`Runtime.evaluate` / `callFunctionOn`
    会**挂住不返回**（2026-10-12 实测：新页上连 `1+1` 都取不回来）。
    需要脚本驱动页面时必须用前台页（foreground=True，默认）。

    ⚠ 用户要求（2026-10-13）：「**你打开的页面用完就关了**」。
      这里注册到进程退出钩子：凡是本模块建的标签页，进程结束（正常/异常）时都会关掉，
      不需要每个脚本自己记得 `close_tab`。显式 `close_tab` 仍然有效且更及时。
    """
    ws = http_json("http://127.0.0.1:%d/json/version" % port)["webSocketDebuggerUrl"]
    r = await _one_shot(ws, "Target.createTarget",
                        {"url": url, "background": not foreground})
    tid = r.get("targetId", "")
    if tid:
        _AUTO_CLOSE.append((port, tid))
        _arm_atexit()
    return tid


#: 本进程建过的标签页：退出时兜底关闭（用户 2026-10-13 明确要求）
_AUTO_CLOSE: list = []
_ATEXIT_ARMED = False


def _close_all_ours() -> None:
    """进程退出时把本进程开的页面全关掉（同步版，别用 asyncio）。"""
    for port, tid in list(_AUTO_CLOSE):
        try:
            ws = http_json("http://127.0.0.1:%d/json/version" % port, timeout=3)[
                "webSocketDebuggerUrl"]
            _sync_send(ws, "Target.closeTarget", {"targetId": tid})
        except Exception:  # noqa: BLE001
            pass
    _AUTO_CLOSE.clear()


def _sync_send(ws_url: str, method: str, params: dict) -> None:
    """开一条 websocket 发一条命令就关（同步，供退出钩子用）。"""
    import asyncio as _a

    async def go():
        async with websockets.connect(ws_url, max_size=4 * 1024 * 1024,
                                      open_timeout=3, close_timeout=3) as ws:
            await ws.send(json.dumps({"id": 1, "method": method, "params": params}))
            try:
                await asyncio.wait_for(ws.recv(), timeout=3)
            except Exception:  # noqa: BLE001
                pass

    try:
        _a.new_event_loop().run_until_complete(go())
    except Exception:  # noqa: BLE001
        pass


def _arm_atexit() -> None:
    global _ATEXIT_ARMED
    if _ATEXIT_ARMED:
        return
    import atexit
    atexit.register(_close_all_ours)
    _ATEXIT_ARMED = True


async def close_tab(port: int, target_id: str) -> None:
    try:
        ws = http_json("http://127.0.0.1:%d/json/version" % port)["webSocketDebuggerUrl"]
        await _one_shot(ws, "Target.closeTarget", {"targetId": target_id})
    except Exception:  # noqa: BLE001
        pass


async def _one_shot(ws_url: str, method: str, params: dict) -> dict:
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": method, "params": params}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if m.get("id") == 1:
                return m.get("result") or {}


class NetCapture:
    """一条常驻 target 会话：开 Network 抓包 + 可插入动作 + 收集匹配的响应体。

    用法（事件循环里）：
        async with NetCapture(target_ws_url) as cap:
            await cap.wait_ready()
            resp = await cap.eval("(function(){...})()", timeout=20)
            await asyncio.sleep(6)
        hits = cap.hits           # [{'url':..,'status':..,'body':<解析后的 JSON 或原文>}]
    """

    def __init__(self, ws_url: str, match=(), max_body: int = 8 * 1024 * 1024):
        self.ws_url = ws_url
        self.match = tuple(match)
        self.max_body = max_body
        self.hits: list[dict] = []
        self._ws = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader = None
        self._urls: dict[str, str] = {}
        self._types: dict[str, str] = {}
        self._bodies: dict[str, str] = {}
        #: 页面里出现过的执行上下文（Runtime.executionContextCreated）——见 contexts 属性
        self.contexts: dict[int, dict] = {}

    # ---- 生命周期
    async def __aenter__(self):
        self._ws = await websockets.connect(self.ws_url, max_size=64 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read_loop())
        await self.send("Network.enable", {"maxTotalBufferSize": 512 * 1024 * 1024,
                                           "maxResourceBufferSize": 256 * 1024 * 1024})
        await self.send("Page.enable", {})
        return self

    async def __aexit__(self, *exc):
        try:
            if self._reader:
                self._reader.cancel()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._ws:
                await self._ws.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- 收发
    async def send(self, method: str, params: Optional[dict] = None,
                   timeout: float = 30.0):
        i = self._next_id
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[i] = fut
        await self._ws.send(json.dumps({"id": i, "method": method,
                                        "params": params or {}}))
        return await asyncio.wait_for(fut, timeout=timeout)

    async def eval(self, js: str, arg=None, timeout: float = 30.0, context_id=None):
        """页面内求值，返回 `(value, exceptionDetails)`。

        `js` 可以是表达式，也可以是**函数声明 + 调用**（script 是 program，比 IIFE 更抗页面包装）。
        `context_id` 指定执行上下文；本页面上**默认上下文求值会命中一个被 instrument 过的
        全局对象**（`window.__gs` 读不到、连 IIFE 都被包坏），故支持显式选上下文重试。
        """
        expr = page_eval_js(js, arg)
        params = {"expression": expr, "returnByValue": True,
                  "awaitPromise": False, "userGesture": True}
        if context_id is not None:
            params["contextId"] = context_id
        r = await self.send("Runtime.evaluate", params, timeout=timeout)
        res = r.get("result") or {}
        exc = r.get("exceptionDetails")
        if exc is not None:
            # ⚠ 现场实测（2026-10-12）：这张页面上 evaluate 会返回
            #   `TypeError: (intermediate value)(...) is not a function`，
            #   而同一表达式在**没开 Runtime.enable** 的会话里完全正常。
            #   `replMode: true` 走 console 求值路径，可以绕开该包装，故失败即重试一次。
            try:
                r2 = await self.send("Runtime.evaluate",
                                     {**params, "replMode": True}, timeout=timeout)
                if "exceptionDetails" not in r2:
                    return ((r2.get("result") or {}).get("value"),
                            None)
                res = r2.get("result") or {}
                exc = r2.get("exceptionDetails")
            except Exception:  # noqa: BLE001
                pass
        return res.get("value"), exc

    def main_context_id(self):
        """挑一个「主世界」上下文 id（origin 为页面域名、且不是扩展/隔离世界）。"""
        best = None
        for cid, c in sorted(self.contexts.items()):
            aux = c.get("auxData") or {}
            if not aux.get("isDefault", False):
                continue
            if not str(c.get("origin") or "").startswith("http"):
                continue
            if aux.get("frameId") and not aux.get("isDefault"):
                continue
            best = cid
        return best

    async def _read_loop(self):
        try:
            async for raw in self._ws:
                m = json.loads(raw)
                if "id" in m:
                    fut = self._pending.pop(m["id"], None)
                    if fut and not fut.done():
                        if "error" in m:
                            fut.set_exception(RuntimeError(str(m["error"])[:200]))
                        else:
                            fut.set_result(m.get("result") or {})
                    continue
                self._on_event(m)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    def _on_event(self, m: dict):
        method = m.get("method") or ""
        p = m.get("params") or {}
        if method == "Network.requestWillBeSent":
            rid = p.get("requestId")
            req = p.get("request") or {}
            self._urls[rid] = req.get("url") or ""
            self._types[rid] = (p.get("type") or "")
            self._bodies[rid] = req.get("postData") or ""
        elif method == "Runtime.executionContextCreated":
            ctx = p.get("context") or {}
            try:
                self.contexts[int(ctx.get("id"))] = {
                    "id": ctx.get("id"), "origin": ctx.get("origin"),
                    "name": ctx.get("name"), "auxData": ctx.get("auxData"),
                }
            except Exception:  # noqa: BLE001
                pass
        elif method == "Runtime.executionContextsCleared":
            self.contexts.clear()
        elif method == "Network.responseReceived":
            rid = p.get("requestId")
            self._urls.setdefault(rid, (p.get("response") or {}).get("url") or "")
            self._types.setdefault(rid, p.get("type") or "")
        elif method == "Network.loadingFinished":
            rid = p.get("requestId")
            url = self._urls.get(rid, "")
            if self.match and not any(k in url for k in self.match):
                return
            if self._types.get(rid) == "Preflight":
                return
            asyncio.ensure_future(self._grab(rid, url))

    async def _grab(self, rid: str, url: str):
        try:
            r = await self.send("Network.getResponseBody", {"requestId": rid}, timeout=30)
        except Exception:  # noqa: BLE001
            return
        body = r.get("body") or ""
        if r.get("base64Encoded"):
            import base64
            try:
                body = base64.b64decode(body).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return
        if len(body) > self.max_body:
            body = body[: self.max_body]
        parsed = None
        try:
            parsed = json.loads(body)
        except Exception:  # noqa: BLE001
            pass
        self.hits.append({"url": url, "body": body, "json": parsed,
                          "req": self._bodies.get(rid, ""),
                          "at": time.strftime("%H:%M:%S")})


async def capture_on_target(ws_url: str, match=(), action=None,
                            settle: float = 8.0, timeout: float = 240.0) -> list:
    """连一个 target：开抓包 → 跑 action(eval) → 等 settle → 返回命中的响应。

    `action` 是可选协程函数 `async def action(cap)`，里面可以 `await cap.eval(js)`。
    """
    async with NetCapture(ws_url, match=match) as cap:
        if action is not None:
            try:
                await action(cap)
            except Exception as e:  # noqa: BLE001
                cap.action_error = "%s: %s" % (type(e).__name__, str(e)[:300])
                print("[cdp_net] action 异常（已忽略，继续收集）:", cap.action_error)
        await asyncio.sleep(settle)
    return cap.hits


def page_eval_js(js: str, arg=None) -> str:
    """把一段 JS **表达式**包成 CDP `Runtime.evaluate` 的 expression（自带防 ASI 保护）。

    ⚠ 2026-10-12 三次踩坑，都记在这里：
    ① 历史实现给 `js` 做了 `json.dumps` —— 源码变成字符串字面量再调用，
       报 `TypeError: (intermediate value)(...) is not a function`；
    ② 修掉①后又「无条件补一对 `()`」，而 `js` 传进来时**自带 IIFE 调用括号**，
       于是变成 `((function(){...})())()` → 同样报错；
    ③ 改成「有参数时拼 `(js)(arg)`」后**仍然**报同一个错，真因是
       **`js` 结尾带换行**：拼出来是
           `...})()\n)("在售中")`
       JS 的自动分号插入（ASI）把第一行当成完整语句结束 ⇒ 变成
       `(IIFE结果)\n("在售中")` ⇒ 又把字符串当函数调用 ⇒ 同一个 TypeError。
       （实测：`(function(want){...})()('在售中')` 报错，而单行紧凑写法正常。）

    ⇒ 现在的写法：把 `js` 压成**单行**、再按「是否已经自带调用」决定参数怎么接
       （`(f)()` + arg → `(f)(arg)`；`(f)` + arg → `(f)(arg)`），只有一层括号，免疫 ASI。
    """
    body = " ".join(ln.strip() for ln in js.strip().splitlines() if ln.strip())
    if arg is None:
        return body
    if body.endswith("()"):                       # 自带调用：把参数塞进那对括号里
        body = body[:-2]
    return "(%s)(%s)" % (body, json.dumps(arg, ensure_ascii=False))
