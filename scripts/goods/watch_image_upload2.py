# -*- coding: utf-8 -*-
"""专抓图片上传（v3，修正版）。

修正 v2 的致命 bug：
    v2 在 `Network.loadingFinished` 事件里读 `params.request.url` ——
    **该事件根本没有 request 对象**，URL 永远是空串，
    于是白名单不匹配、事件被静默丢弃，结果 0 条。
    必须用 `requestWillBeSent` 时记下的 requestId → url 映射。

设计（保持简单，避免互锁）：
    · requestWillBeSent 时把 {url, method, postData, headers} 存进 meta[rid]
    · loadingFinished 时查 meta[rid] → 命中白名单才取 body
    · getRequestPostData / getResponseBody 都走同一条 send()（内部暂存事件，不丢）

用法：
    python scripts/goods/watch_image_upload2.py --port 61407 --out out/imgup2 --minutes 12
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
WANT = re.compile(r"/material/|/galerie/|get_signature|oss|upload|image/upload"
                  r"|amazonaws|aliyuncs|myqcloud|cos\.", re.I)


class Rec:
    def __init__(self, outdir: Path):
        outdir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.jsonl = outdir / ("imgup_%s.jsonl" % ts)
        self.log = outdir / ("imgup_%s.log" % ts)
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
        k = (rec.get("method"), rec.get("url"), (rec.get("postData") or "")[:150])
        if k in self.seen:
            return
        self.seen.add(k)
        rec["ts"] = time.strftime("%H:%M:%S")
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()
        self.n += 1
        u = str(rec.get("url") or "")
        self.say("★ [%03d] %-5s %-4s %s"
                 % (self.n, rec.get("method"), rec.get("status") or "",
                    u.replace("https://agentseller.temu.com", "")[:130]))
        if rec.get("contentType"):
            self.say("        ct: %s" % rec["contentType"][:70])
        if rec.get("postData"):
            self.say("        req(%d): %s" % (len(rec["postData"]),
                                              str(rec["postData"])[:600].replace("\n", " ")))
        if rec.get("resp"):
            self.say("        res(%d): %s" % (len(rec["resp"]),
                                              str(rec["resp"])[:500].replace("\n", " ")))


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
                """处理队列事件；用 meta[rid] 找 URL（关键修正）。"""
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
                        # 所有请求都记进 meta（不筛），筛选放到 loadingFinished
                        meta[rid] = {
                            "url": u, "method": rq.get("method"),
                            "postData": rq.get("postData") or "",
                            "headers": rq.get("headers") or {},
                            "status": None,
                        }
                    elif me == "Network.responseReceived":
                        if rid in meta:
                            meta[rid]["status"] = (p.get("response") or {}).get("status")
                    elif me == "Network.loadingFinished":
                        info = meta.pop(rid, None)
                        if not info:
                            continue
                        u = info["url"]
                        if not WANT.search(u):
                            continue
                        pos = info.get("postData") or ""
                        if not pos:
                            r = await call("Network.getRequestPostData",
                                           {"requestId": rid}, 8)
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
                        hdr = info.get("headers") or {}
                        rec.add({
                            "url": u, "method": info["method"],
                            "postData": pos[:300000], "resp": resp[:300000],
                            "status": info.get("status"),
                            "contentType": hdr.get("content-type")
                            or hdr.get("Content-Type") or "",
                            "headers": {k: v for k, v in hdr.items()
                                        if k.lower() in ("content-type", "origin",
                                                         "referer", "x-amz-acl",
                                                         "authorization", "x-cos-acl")},
                        })

            await call("Network.enable", {"maxTotalBufferSize": 512 * 1024 * 1024,
                                          "maxResourceBufferSize": 256 * 1024 * 1024})
            rec.say("  [挂上] %s" % ws_url[-24:])
            while not stop.is_set():
                m = await recv_one(0.5)
                if m is not None:
                    if "id" in m:
                        continue
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
    rec.say("★ 图片上传监听 v3（修正 loadingFinished 无 request 的 bug）")
    rec.say("数据: %s" % rec.jsonl)

    tasks, attached = [], set()
    start = time.time()
    try:
        while time.time() - start < a.minutes * 60:
            try:
                pages = [t for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
                         if t.get("type") == "page"
                         and "agentseller" in (t.get("url") or "")]
            except Exception:
                pages = []
            for t in pages:
                tm = t.get("id")
                if tm in attached:
                    continue
                attached.add(tm)
                rec.say("  [attach] %s %s" % (tm[:8], (t.get("url") or "")[:60]))
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
    ap.add_argument("--out", default="out/imgup2")
    ap.add_argument("--minutes", type=float, default=12)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
