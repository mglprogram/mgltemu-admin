# -*- coding: utf-8 -*-
"""后台监听（v2）：带文件日志，且支持「中途挂包也尽量补齐已发出的请求」。

相对 v1 的改动：
    ① **所有输出写日志文件**（out/publish_rec/watch.log），不依赖后台任务回显 ——
       v1 出现过「后台任务 10 分钟没回显，误以为没录到」的情况。
    ② attach 时自动 `Page.reload()` 一次：`Network.enable` 只能听**开启之后**的请求，
       用户先加载完页面再挂包会漏掉首屏那一批（属性模板/SKU/价格全在里面）。
       重载一次能让整条链路从头录到，代价是该页面的未保存草稿内容会丢 ——
       **只在 --reload 时做，且默认跳过含 productDraftId 的页**（用户可能正在填）。
    ③ 状态心跳：每 15 秒往日志写一行「已录 N 条 / 挂在 K 个页面」，
       任何时候打开日志都知道它活着。

用法：
    python scripts/goods/watch_publish_flow.py --port 63854 --out out/publish_rec --log
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import cdp_net  # noqa: E402
import websockets  # noqa: E402

BASE = "https://agentseller.temu.com"
HOSTS = re.compile(r"agentseller\.temu\.com|agentseller-us\.temu\.com")
SKIP = re.compile(r"\.(js|css|png|jpe?g|svg|gif|woff2?|ico|webp|map)(\?|$)|"
                  r"^data:|^blob:|^chrome-extension:|^about:", re.I)
NOISE = re.compile(r"/api/phantom/|/api/sec-csp/|/api/server/_stm|web-performace|"
                   r"queryFrontendReportUinResult|pftk\.temu|/b/th|redDot|"
                   r"totalUnreadMsgNum|unreadMsgDetail|/pop/query|/pop/read")
KEY = re.compile(r"categor|template|prop|sku|sizespec|price|image|draft|product/|"
                 r"freight|warehouse|save|submit|publish|check|barcode|title|desc", re.I)


class Recorder:
    def __init__(self, outdir: Path, log):
        self.outdir = outdir
        outdir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.jsonl = outdir / ("flow_%s.jsonl" % ts)
        self.log = log
        self.seen: set = set()
        self.count = Counter()
        self.n = 0
        self.fh = open(self.jsonl, "a", encoding="utf-8")

    def say(self, msg: str):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        self.log.write(line + "\n")
        self.log.flush()

    def add(self, rec: dict):
        k = (rec.get("method"), rec.get("url"), rec.get("postData"))
        if k in self.seen:
            return
        self.seen.add(k)
        rec["ts"] = time.strftime("%H:%M:%S")
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()
        self.n += 1
        self.count[rec.get("url")] += 1
        path = str(rec.get("url") or "").replace(BASE, "")
        mark = "★" if KEY.search(path) else " "
        self.say("%s [%03d] %-5s %s" % (mark, self.n, rec.get("method"), path[:110]))
        req = (rec.get("postData") or "")[:200].replace("\n", " ")
        if req:
            self.say("        req: %s" % req)

    def summary(self):
        p = self.outdir / (self.jsonl.stem + ".summary.json")
        p.write_text(json.dumps({"recorded": self.n, "by_url": self.count.most_common()},
                                ensure_ascii=False, indent=1), encoding="utf-8")
        self.say("=== 共录 %d 条 ===" % self.n)
        for u, c in self.count.most_common():
            self.say("  %3d  %s" % (c, str(u).replace(BASE, "")))
        self.say("落盘：%s" % self.jsonl)
        return p


async def watch_target(ws_url: str, rec: Recorder, stop: asyncio.Event,
                       reload: bool, url: str):
    try:
        # ⚠ 关键坑（2026-11-26 实测）：默认 `ping_interval=20 / ping_timeout=20`。
        # 我们对 6 个页面各开一条 ws，每条只有一个 reader 协程 —— 页面重载时
        # DevTools 会暂停响应，20 秒内答不上 ping 就被判 keepalive ping timeout，
        # 连接带着 1011 关闭，**整段录制丢失**（草稿页就是这样漏掉的）。
        # ⇒ 关掉自动 ping（None），并加大缓冲区；断线由外层循环重新 attach。
        async with websockets.connect(ws_url, max_size=128 * 1024 * 1024,
                                      ping_interval=None,
                                      ping_timeout=None,
                                      close_timeout=5) as w:
            nid = [0]
            pend: dict = {}
            meta: dict = {}

            async def send(m, p=None, timeout=20):
                nid[0] += 1
                i = nid[0]
                f = asyncio.get_running_loop().create_future()
                pend[i] = f
                try:
                    await w.send(json.dumps({"id": i, "method": m, "params": p or {}}))
                    return await asyncio.wait_for(f, timeout)
                except Exception:
                    return {}

            async def reader():
                try:
                    async for raw in w:
                        m = json.loads(raw)
                        if "id" in m:
                            f = pend.pop(m["id"], None)
                            if f and not f.done():
                                f.set_result(m.get("result") or {})
                            continue
                        meth = m.get("method") or ""
                        p = m.get("params") or {}
                        if meth == "Network.requestWillBeSent":
                            rid = p.get("requestId")
                            rq = p.get("request") or {}
                            u = rq.get("url") or ""
                            if not u or SKIP.search(u) or NOISE.search(u):
                                continue
                            meta[rid] = {"url": u, "method": rq.get("method"),
                                         "postData": (rq.get("postData") or "")[:200000],
                                         "page": (url or "")[:120]}
                        elif meth == "Network.loadingFinished":
                            rid = p.get("requestId")
                            mm = meta.pop(rid, None)
                            if not mm:
                                continue
                            r = await send("Network.getResponseBody",
                                           {"requestId": rid}, timeout=15)
                            body = r.get("body") or ""
                            if r.get("base64Encoded"):
                                try:
                                    import base64
                                    body = base64.b64decode(body).decode("utf-8", "replace")
                                except Exception:
                                    body = ""
                            mm["resp"] = body[:200000]
                            rec.add(mm)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    rec.say("!! reader 异常: %s" % str(e)[:120])

            task = asyncio.create_task(reader())
            try:
                await send("Network.enable", {"maxTotalBufferSize": 512 * 1024 * 1024,
                                              "maxResourceBufferSize": 256 * 1024 * 1024})
                await send("Page.enable", {})
                if reload:
                    rec.say("  [reload] 重载一次以从头录：%s" % (url or "")[:80])
                    await send("Page.reload", {"ignoreCache": False})
                await stop.wait()
            finally:
                task.cancel()
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        rec.say("!! attach 失败 %s: %s" % ((url or "")[:60], str(e)[:100]))


async def main(a):
    outdir = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    outdir.mkdir(parents=True, exist_ok=True)
    log = open(outdir / "watch.log", "a", encoding="utf-8")
    rec = Recorder(outdir, log)
    rec.say("监听启动 port=%s reload=%s" % (a.port, a.reload))
    rec.say("JSONL: %s" % rec.jsonl)

    stop = asyncio.Event()
    attached: dict = {}
    tick = 0
    try:
        while True:
            tick += 1
            try:
                all_log = cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
            except Exception:
                all_log = []
            targets = [t for t in all_log if t.get("type") == "page"
                       and HOSTS.search(t.get("url") or "")]
            for t in targets:
                tm = t.get("id")
                if tm in attached:
                    continue
                url = t.get("url") or ""
                # 不重载用户可能正在填的草稿页
                do_reload = bool(a.reload) and "productDraftId" not in url
                attached[tm] = asyncio.create_task(
                    watch_target(t["webSocketDebuggerUrl"], rec, stop, do_reload, url))
                rec.say("  [attach] %s %s%s" % (tm[:8], url[:70],
                                                "  (reload)" if do_reload else ""))
            live = {t.get("id") for t in all_log if t.get("type") == "page"}
            for tm in list(attached):
                if tm not in live:
                    attached.pop(tm, None)
            if tick % 5 == 0:
                rec.say("  [heartbeat] 已录 %d 条 / 挂在 %d 个页面" % (len(rec.seen), len(attached)))
            await asyncio.sleep(3)
            if a.max_seconds and tick * 3 > a.max_seconds:
                rec.say("到达 --max-seconds，结束")
                break
    except (KeyboardInterrupt, asyncio.CancelledError):
        rec.say("收到停止信号")
    finally:
        for t in attached.values():
            t.cancel()
        rec.summary()
        rec.fh.close()
        log.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--out", default="out/publish_rec")
    ap.add_argument("--log", action="store_true", help="（兼容参数）日志总是写文件")
    ap.add_argument("--reload", action="store_true",
                    help="attach 时重载页面以从头录（跳过含 productDraftId 的页）")
    ap.add_argument("--max-seconds", type=int, default=0)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
