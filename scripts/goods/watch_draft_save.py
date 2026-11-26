# -*- coding: utf-8 -*-
"""专抓「保存草稿」：监听 draft/save，把请求体与响应体完整取下来。

⚠ 本脚本**只监听、不发任何请求**。你自己在页面点保存，它旁听。

设计要点（继承 watch_image_upload2 的修正）：
    · URL 在 `requestWillBeSent` 时按 requestId 存进 meta，
      `loadingFinished` 时**必须查 meta**（该事件本身没有 request 对象）；
    · 取 body 走同一条 send()，内部把顺路事件暂存，不会互锁；
    · 白名单：draft/save + 保存前后会打的校验接口（校验失败会拦下保存）。

用法：
    python scripts/goods/watch_draft_save.py --port 61407 --out out/draftsave --minutes 15
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import cdp_net  # noqa: E402
import websockets  # noqa: E402

SKIP = re.compile(r"\.(js|css|png|jpe?g|svg|gif|woff2?|ico|webp|map|ttf)(\?|$)|"
                  r"^data:|^blob:|^chrome-extension:|^about:|favicon", re.I)
#: 只抓这些（保存动作 + 保存时的校验/提示）
WANT = re.compile(r"draft/save|draft/add|draft/query|product/check|extCode/batchCheck"
                  r"|product/query|guideFile|sensitiveLimit|risk/", re.I)


class Rec:
    def __init__(self, outdir: Path):
        outdir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.jsonl = outdir / ("save_%s.jsonl" % ts)
        self.log = outdir / ("save_%s.log" % ts)
        self.seen: set = set()
        self.n = 0
        self.fh = open(self.jsonl, "a", encoding="utf-8")
        self.lf = open(self.log, "a", encoding="utf-8")

    def say(self, s):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), s)
        print(line, flush=True)
        self.lf.write(line + "\n")
        self.lf.flush()

    def add(self, rec):
        k = (rec.get("method"), rec.get("url"))
        if k in self.seen:
            return
        self.seen.add(k)
        rec["ts"] = time.strftime("%H:%M:%S")
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()
        self.n += 1
        u = str(rec.get("url") or "")
        star = "★" if "draft/save" in u else " "
        self.say("%s [%03d] %-5s %-4s %s"
                 % (star, self.n, rec.get("method"), rec.get("status") or "",
                    u.replace("https://agentseller.temu.com", "")[:110]))
        if rec.get("postData"):
            body = str(rec["postData"])
            self.say("        req(%d字节): %s" % (len(body), body[:1200].replace("\n", " ")))
        if rec.get("resp"):
            self.say("        res: %s" % str(rec["resp"])[:800].replace("\n", " "))


async def watch(ws_url, rec, stop):
    try:
        async with websockets.connect(ws_url, max_size=256 * 1024 * 1024,
                                      ping_interval=None, ping_timeout=None) as w:
            nid = [0]
            evq: list = []
            meta: dict = {}

            async def recv_one(timeout):
                try:
                    raw = await asyncio.wait_for(w.recv(), timeout)
                except asyncio.TimeoutError:
                    return None
                return json.loads(raw)

            async def call(method, params=None, timeout=20):
                nid[0] += 1
                i = nid[0]
                await w.send(json.dumps({"id": i, "method": method,
                                         "params": params or {}}))
                end = asyncio.get_running_loop().time() + timeout
                while True:
                    left = end - asyncio.get_running_loop().time()
                    if left <= 0:
                        return {}
                    m = await recv_one(min(left, 5.0))
                    if m is None:
                        continue
                    if "id" in m:
                        if m["id"] == i:
                            return m.get("result") or {}
                        continue
                    evq.append(m)

            async def drain():
                while evq:
                    m = evq.pop(0)
                    me = m.get("method") or ""
                    p = m.get("params") or {}
                    rid = p.get("requestId")
                    if me == "Network.requestWillBeSent":
                        rq = p.get("request") or {}
                        u = rq.get("url") or ""
                        if not u or SKIP.search(u):
                            continue
                        meta[rid] = {"url": u, "method": rq.get("method"),
                                     "postData": rq.get("postData") or "",
                                     "status": None}
                    elif me == "Network.responseReceived":
                        if rid in meta:
                            meta[rid]["status"] = (p.get("response") or {}).get("status")
                    elif me == "Network.loadingFinished":
                        info = meta.pop(rid, None)
                        if not info:
                            continue
                        if not WANT.search(info["url"]):
                            continue
                        pos = info.get("postData") or ""
                        if not pos:
                            r = await call("Network.getRequestPostData",
                                           {"requestId": rid}, 10)
                            pos = r.get("postData") or ""
                        r2 = await call("Network.getResponseBody",
                                        {"requestId": rid}, 10)
                        resp = r2.get("body") or ""
                        if r2.get("base64Encoded") and resp:
                            try:
                                import base64
                                resp = base64.b64decode(resp).decode("utf-8", "replace")
                            except Exception:
                                pass
                        rec.add({"url": info["url"], "method": info["method"],
                                 "postData": pos[:500000], "resp": resp[:200000],
                                 "status": info.get("status")})

            await call("Network.enable", {"maxTotalBufferSize": 512 * 1024 * 1024,
                                          "maxResourceBufferSize": 256 * 1024 * 1024})
            rec.say("  [挂上] %s" % ws_url[-24:])
            while not stop.is_set():
                m = await recv_one(0.5)
                if m is not None and "id" not in m:
                    evq.append(m)
                if evq:
                    await drain()
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        rec.say("!! 连接异常: %s" % str(e)[:110])


async def main(a):
    outdir = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    rec = Rec(outdir)
    stop = asyncio.Event()
    rec.say("★ 保存草稿监听（只旁听，不发请求）")
    rec.say("数据: %s" % rec.jsonl)

    tasks, attached = [], set()
    start = time.time()
    try:
        while time.time() - start < a.minutes * 60:
            try:
                pages = [t for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
                         if t.get("type") == "page" and "agentseller" in (t.get("url") or "")]
            except Exception:
                pages = []
            for t in pages:
                tm = t.get("id")
                if tm in attached:
                    continue
                attached.add(tm)
                rec.say("  [attach] %s %s" % (tm[:8], (t.get("url") or "")[:62]))
                tasks.append(asyncio.create_task(
                    watch(t["webSocketDebuggerUrl"], rec, stop)))
            await asyncio.sleep(2)
    except (KeyboardInterrupt, asyncio.CancelledError):
        rec.say("停止")
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        rec.say("=== 共 %d 条 ===" % rec.n)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--out", default="out/draftsave")
    ap.add_argument("--minutes", type=float, default=15)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
