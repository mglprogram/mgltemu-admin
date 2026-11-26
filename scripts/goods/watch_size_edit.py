# -*- coding: utf-8 -*-
"""监听「编辑草稿 → 改尺码 → 保存」全过程的接口（请求体 + 响应体）。

用法：先启动本脚本，再去页面点「编辑」、改尺码、点保存。
    python scripts/goods/watch_size_edit.py --port 61407 --minutes 15
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
#: 只抓可能跟尺码/保存相关的
WANT = re.compile(r"sizespec|sizeSpec|size_template|sizeTemplate|draft/save|"
                  r"draft/query|draft/add|check/cat|check/common|"
                  r"product/check|template/query", re.I)


class Rec:
    def __init__(self, outdir: Path):
        outdir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.jsonl = outdir / ("size_edit_%s.jsonl" % ts)
        self.log = outdir / ("size_edit_%s.log" % ts)
        self.seen = set()
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
        u = str(rec.get("url") or "").replace("https://agentseller.temu.com", "")
        self.say("★ [%03d] %-5s %-4s %s" % (
            self.n, rec.get("method"), rec.get("status") or "", u[:110]))
        if rec.get("postData"):
            self.say("        req(%d): %s" % (
                len(rec["postData"]), str(rec["postData"])[:700].replace("\n", " ")))
        if rec.get("resp"):
            self.say("        res(%d): %s" % (
                len(rec["resp"]), str(rec["resp"])[:400].replace("\n", " ")))

    def close(self):
        self.fh.close()
        self.lf.close()


async def watch(ws, rec, stop):
    try:
        async with websockets.connect(ws, max_size=256 * 1024 * 1024,
                                      ping_interval=None, ping_timeout=None) as w:
            nid = [0]
            pend = {}
            meta = {}
            evq = []

            async def recv_one(t):
                try:
                    return json.loads(await asyncio.wait_for(w.recv(), t))
                except asyncio.TimeoutError:
                    return None

            async def call(m, p=None, to=20):
                nid[0] += 1
                i = nid[0]
                fut = asyncio.get_running_loop().create_future()
                pend[i] = fut
                await w.send(json.dumps({"id": i, "method": m, "params": p or {}}))
                end = asyncio.get_running_loop().time() + to
                while True:
                    left = end - asyncio.get_running_loop().time()
                    if left <= 0:
                        return {}
                    mm = await recv_one(min(left, 5.0))
                    if mm is None:
                        continue
                    if "id" in mm:
                        if mm["id"] == i:
                            return mm.get("result") or {}
                        continue
                    evq.append(mm)

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
                                           {"requestId": rid}, 8)
                            pos = r.get("postData") or ""
                        r2 = await call("Network.getResponseBody",
                                        {"requestId": rid}, 10)
                        resp = r2.get("body") or ""
                        rec.add({"url": info["url"], "method": info["method"],
                                 "postData": pos[:400000], "resp": resp[:200000],
                                 "status": info.get("status")})

            await call("Network.enable", {"maxTotalBufferSize": 512 * 1024 * 1024,
                                          "maxResourceBufferSize": 256 * 1024 * 1024})
            rec.say("已挂上监听：%s" % ws[-24:])
            while not stop.is_set():
                m = await recv_one(0.5)
                if m is not None and "id" not in m:
                    evq.append(m)
                if evq:
                    await drain()
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        rec.say("连接异常: %s" % str(e)[:100])


async def main(a):
    outdir = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    rec = Rec(outdir)
    stop = asyncio.Event()
    rec.say("★ 监听尺码编辑（只旁听，不发请求）")
    rec.say("数据: %s" % rec.jsonl)
    rec.say("请现在点「编辑」→ 改尺码 → 保存")

    tasks, attached = [], set()
    start = time.time()
    try:
        while time.time() - start < a.minutes * 60:
            try:
                pages = [t for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
                         if t.get("type") == "page"
                         and "agentseller.temu.com" in (t.get("url") or "")]
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
            live = {t.get("id") for t in cdp_net.http_json(
                "http://127.0.0.1:%d/json/list" % a.port) if t.get("type") == "page"}
            for tm in list(attached):
                if tm not in live:
                    attached.discard(tm)
            await asyncio.sleep(2)
    except (KeyboardInterrupt, asyncio.CancelledError):
        rec.say("停止")
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        rec.say("=== 共 %d 条 ===" % rec.n)
        rec.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--out", default="out/size_edit")
    ap.add_argument("--minutes", type=float, default=15)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
