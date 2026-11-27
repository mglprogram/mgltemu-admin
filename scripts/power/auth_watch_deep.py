# -*- coding: utf-8 -*-
"""auth_watch_deep.py — 深度监听「人工登录/授权」全过程，把每一步抓成可复现的证据。

用途（2026-11-27 用户令：「你开监听，看我操作」）：
    用户手动把 KLS 从 `seller.kuajingmaihuo.com/login`（默认扫码态）登进来。
    我要知道**到底是什么让它进去的**：账密被自动填了？点了哪个 tab/按钮？
    发了哪些登录请求？有没有验证码/扫码环节？

抓四类东西（都是「能直接下结论」的证据，不是截个图猜）：
  ① **输入框值**：每 2 秒记录每个 input 的 type/值长度/placeholder，
     只在**变化时**落一行 ⇒ 直接回答「紫鸟有没有自动填账密」；
  ② **XHR/fetch**：URL + 方法 + 是否带 username/password 字段 ⇒ 回答「提交走了哪个接口」；
  ③ **URL 跳转 / 新页**：回答「从哪一步跳到哪一步」；
  ④ **关键 DOM 状态**：扫码 tab、按钮文本、captcha/二维码元素是否存在。

产物 `_logs/auth_watch_deep/<时间戳>/`：
  events.jsonl   逐条事件
  inputs.jsonl   输入框值变化
  shot_XXX.png   每 4 秒截图

用法：
    python scripts/power/auth_watch_deep.py 300                 # 监听 KLS 5 分钟
    python scripts/power/auth_watch_deep.py 300 TM-BHY-YJF
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import websockets  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT = os.path.join(r"D:\AI-workplace\TEMU\_logs", "auth_watch_deep", STAMP)
_NOISE = ("temu.com", "kuajingmaihuo.com")

JS_SNAPSHOT = r"""() => {
  const vis = (e) => e && e.offsetParent !== null;
  const txt = (e) => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const ins = Array.from(document.querySelectorAll('input')).map(e => ({
    type: e.type || '', name: e.name || '', len: (e.value || '').length,
    ph: e.placeholder || '', vis: vis(e),
  }));
  const tabTexts = Array.from(document.querySelectorAll(
      'div,span,a,li,[role=tab],[class*=tab]')).filter(vis).map(txt)
      .filter(t => /登录$/.test(t) && t.length <= 8);
  return JSON.stringify({
    url: location.href, title: document.title, inputs: ins,
    tabs: Array.from(new Set(tabTexts)).slice(0, 8),
    hasCaptcha: !!document.querySelector(
        '[class*=captcha],[id*=captcha],[class*=verify-code],[class*=slider]'),
    hasQr: !!document.querySelector('[class*=qr],[class*=QR],canvas'),
    bodyLen: (document.body ? (document.body.innerText || '').length : 0),
  });
}"""


def find_port(window: str) -> int:
    import ziniao_store as zs  # noqa: PLC0415
    try:
        return int(zs.probe_port_by_window(window) or 0)
    except Exception:  # noqa: BLE001
        return 0


class Rec:
    def __init__(self):
        os.makedirs(OUT, exist_ok=True)
        self.f_ev = open(os.path.join(OUT, "events.jsonl"), "w",
                         encoding="utf-8", newline="\n")
        self.f_in = open(os.path.join(OUT, "inputs.jsonl"), "w",
                         encoding="utf-8", newline="\n")
        self.n_shot = 0
        self._last_inputs = ""

    def ev(self, kind: str, **kw):
        rec = dict(t=time.strftime("%H:%M:%S"), kind=kind, **kw)
        self.f_ev.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.f_ev.flush()

    def inputs(self, snap: dict):
        sig = json.dumps(snap.get("inputs") or [], ensure_ascii=False)
        if sig == self._last_inputs:
            return False
        self._last_inputs = sig
        self.f_in.write(json.dumps(
            {"t": time.strftime("%H:%M:%S"), "url": snap.get("url"),
             "inputs": snap.get("inputs")}, ensure_ascii=False) + "\n")
        self.f_in.flush()
        return True

    def close(self):
        for f in (self.f_ev, self.f_in):
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass


class Session:
    """单 reader + Future 响应表（**唯一** recv 者，避免 ConcurrencyError）。"""

    def __init__(self, ws, rec: Rec):
        self.ws = ws
        self.rec = rec
        self._mid = 0
        self._pend: dict = {}         # id -> Future
        self._readers: list = []
        self._stop = False

    async def send(self, method, params=None, sid=None) -> dict:
        self._mid += 1
        mid = self._mid
        msg = {"id": mid, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        fut = asyncio.get_event_loop().create_future()
        self._pend[mid] = fut
        await self.ws.send(json.dumps(msg))
        try:
            return await asyncio.wait_for(fut, timeout=25)
        except asyncio.TimeoutError:
            return {}
        finally:
            self._pend.pop(mid, None)

    def _on_msg(self, m: dict):
        if "id" in m:
            fut = self._pend.get(m["id"])
            if fut and not fut.done():
                fut.set_result(m)
            return
        me, p = m.get("method") or "", m.get("params") or {}
        if me == "Network.requestWillBeSent":
            r = p.get("request") or {}
            u = str(r.get("url") or "")
            if not any(k in u for k in _NOISE):
                return
            post = str(r.get("postData") or "")
            self.rec.ev("req", method=r.get("method"), url=u[:200], postLen=len(post),
                        hasUser=any(k in post for k in ("username", "account", "phone")),
                        hasPwd=any(k in post for k in ("password", "pwd")))
        elif me == "Network.responseReceived":
            rsp = p.get("response") or {}
            u = str(rsp.get("url") or "")
            if not any(k in u for k in _NOISE):
                return
            st = rsp.get("status")
            if st and (st >= 400 or "login" in u or "auth" in u or "settle" in u):
                self.rec.ev("resp", status=st, url=u[:200])
        elif me == "Network.frameNavigated":
            u = str((p.get("frame") or {}).get("url") or "")
            if u and any(k in u for k in _NOISE):
                self.rec.ev("nav", url=u[:240])
                print("  [NAV] %s" % u[:130], flush=True)
        elif me == "Target.targetCreated":
            ti = p.get("targetInfo") or {}
            if ti.get("type") == "page":
                u = str(ti.get("url") or "")
                self.rec.ev("newpage", url=u[:200])
                if any(k in u for k in _NOISE):
                    print("  [NEW] %s" % u[:120], flush=True)

    async def pump(self):
        while not self._stop:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                return
            try:
                self._on_msg(json.loads(raw))
            except Exception:  # noqa: BLE001
                pass

    def stop(self):
        self._stop = True


async def main() -> int:
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    window = sys.argv[2] if len(sys.argv) > 2 else "TM-KinglamanSport"
    port = find_port(window)
    if not port:
        print("窗口 %s 没开（探不到 CDP 端口）" % window)
        return 2
    print("监听窗口 %s ｜ 端口 %s ｜ 时长 %ds" % (window, port, secs))
    print("产物目录：%s" % OUT)

    rec = Rec()
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port, timeout=10).read())

    async with websockets.connect(ver["webSocketDebuggerUrl"],
                                 max_size=256 * 1024 * 1024) as ws:
        s = Session(ws, rec)
        pump = asyncio.create_task(s.pump())
        await s.send("Target.setDiscoverTargets", {"discover": True})
        await asyncio.sleep(0.6)

        tabs = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/json/list" % port, timeout=10).read())
        pages = [t for t in tabs if t.get("type") == "page"]
        print("窗口页数 %d：" % len(pages))
        for t in pages:
            print("   %s" % str(t.get("url"))[:110])

        sess = {}
        for t in pages:
            r = await s.send("Target.attachToTarget",
                             {"targetId": t["id"], "flatten": True})
            sid = (r.get("result") or {}).get("sessionId")
            if not sid:
                continue
            sess[t["id"]] = sid
            await s.send("Network.enable", {}, sid)
            await s.send("Page.enable", {}, sid)
            rec.ev("attach", url=str(t.get("url"))[:200], sid=sid[:8])

        print("\n👉 请现在手动操作（登录 / 授权）。\n"
              "   我会每 2 秒记录输入框状态、每 4 秒截图，任何填值/跳转/请求都会实时打印。\n",
              flush=True)

        end = time.time() + secs
        last_shot = last_snap = 0.0
        while time.time() < end:
            await asyncio.sleep(0.4)
            if time.time() - last_snap >= 2:
                last_snap = time.time()
                for tid, sid in list(sess.items()):
                    r = await s.send("Runtime.evaluate", {
                        "expression": "(%s)()" % JS_SNAPSHOT,
                        "returnByValue": True}, sid)
                    v = (((r.get("result") or {}).get("result")) or {}).get("value")
                    if not v:
                        continue
                    try:
                        snap = json.loads(v)
                    except Exception:  # noqa: BLE001
                        continue
                    if rec.inputs(snap):
                        filled = [x for x in (snap.get("inputs") or [])
                                  if x.get("len", 0) > 0]
                        print("  [输入] %s | %s"
                              % (snap.get("url", "")[:55],
                                 ", ".join("%s=%d字"
                                           % (x.get("ph") or x.get("type"), x.get("len", 0))
                                           for x in filled) or "全部为空"),
                              flush=True)
            if time.time() - last_shot >= 4:
                last_shot = time.time()
                for tid, sid in list(sess.items()):
                    r = await s.send("Page.captureScreenshot", {"format": "png"}, sid)
                    data = ((r.get("result") or {}).get("data") or "")
                    if data:
                        rec.n_shot += 1
                        p = os.path.join(OUT, "shot_%03d_%s.png"
                                         % (rec.n_shot, time.strftime("%H%M%S")))
                        with open(p, "wb") as f:
                            f.write(base64.b64decode(data))

        s.stop()
        try:
            await asyncio.wait_for(pump, timeout=3)
        except Exception:  # noqa: BLE001
            pass

    rec.close()
    print("\n完成：截图 %d 张 → %s" % (rec.n_shot, OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
