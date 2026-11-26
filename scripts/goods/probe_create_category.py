# -*- coding: utf-8 -*-
"""探测 TEMU「上架-选类目」页 https://agentseller.temu.com/goods/create/category 的接口。

做什么（只读，不改任何数据）：
    ① 在店铺窗口里新开一个前台页 → 打开类目页
    ② 全程抓 Network：列出所有 XHR/Fetch 请求（URL / method / 状态码 / 请求体 / 响应体）
    ③ 把命中关键词（category / cat / query / tree）的接口落盘成 JSON，供后续复用

用法：
    python scripts/goods/probe_create_category.py --port 63854 --out out/probe_create_category

⚠ 用户要求（2026-10-13）：脚本开的页面用完就关 —— 本脚本自带 finally 关页。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import cdp_net  # noqa: E402

URL = "https://agentseller.temu.com/goods/create/category"

#: 抓包关键词：只看跟类目相关的接口，避免把整个页面几十个请求都拉下来
MATCH = ("categor", "cat/", "/cat", "queryCat", "productCat", "prop", "attribute", "template")


async def main(a):
    outdir = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    outdir.mkdir(parents=True, exist_ok=True)

    print("[1] 新开前台页 → %s" % URL, flush=True)
    tid = await cdp_net.new_tab(a.port, URL, foreground=True)
    if not tid:
        print("!! 建页失败（port=%s 是否可连？）" % a.port)
        return 2
    print("    targetId=%s" % tid, flush=True)

    ws = None
    for _ in range(20):
        await asyncio.sleep(0.5)
        for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port):
            if t.get("id") == tid:
                ws = t.get("webSocketDebuggerUrl")
                break
        if ws:
            break
    if not ws:
        print("!! 拿不到该 target 的 ws 地址")
        return 3

    hits: list = []
    allreq: dict = {}
    try:
        async with cdp_net.NetCapture(ws, match=MATCH) as cap:
            # 等页面把首屏接口发完（qiankun 微前端 + 类目树，实测首屏 6~10s）
            settle = max(int(a.settle), 1)
            for i in range(settle):
                await asyncio.sleep(1)
                if i == 2:
                    # 记录此时页面 URL/标题，确认有没有被踢到登录/授权页
                    v, _ = await cap.eval(
                        "JSON.stringify({url:location.href,title:document.title,"
                        "hasTree:!!document.querySelector('[class*=tree],[class*=Tree]'),"
                        "text:(document.body&&document.body.innerText||'').slice(0,600)})")
                    print("[2] %ss 页面状态：%s" % (i + 1, v), flush=True)
            hits = list(cap.hits)
            allreq = dict(cap._urls)
    finally:
        await cdp_net.close_tab(a.port, tid)
        print("[4] 已关页 %s" % tid, flush=True)

    ts = time.strftime("%Y%m%d-%H%M%S")
    (outdir / ("hits_%s.json" % ts)).write_text(
        json.dumps(hits, ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / ("allurls_%s.json" % ts)).write_text(
        json.dumps(sorted(set(allreq.values())), ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n[3] 命中 %d 个类目相关接口：" % len(hits))
    for h in hits:
        print("   ", h["url"].replace("https://agentseller.temu.com", ""))
        if h.get("req"):
            print("        req:", str(h["req"])[:200])
        j = h.get("json")
        if isinstance(j, dict):
            print("        resp keys:", list(j.keys())[:6],
                  "| result keys:", list((j.get("result") or {}).keys())[:6]
                  if isinstance(j.get("result"), dict) else "-")
    print("\n落盘：%s" % outdir)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True, help="店铺窗口 CDP 端口")
    ap.add_argument("--out", default="out/probe_create_category")
    ap.add_argument("--settle", default=12, help="首屏等待秒数")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
