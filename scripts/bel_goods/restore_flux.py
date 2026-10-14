# -*- coding: utf-8 -*-
"""从 Cloudflare Pages 的历史部署里恢复 data/flux/*.json（抓取格式）。

背景：2026-10-10 有一次抓取跑到了错误页面，写出 0 条产物，
合并后把线上的好数据顶掉了。Cloudflare Pages 保留历史部署，
每个部署有独立 URL，可以从那里把合并后的数据取回来并转回抓取格式。

用法：
  python restore_flux.py <部署URL> BHY            # 恢复某店的已开启+待开启
  python restore_flux.py <部署URL> BHY accelerating
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\AI-workplace\TEMU")
FLUX_DIR = ROOT / "data" / "flux"


def fetch(url: str) -> dict:
    # 带浏览器 UA：Cloudflare 会 403 掉默认的 Python-urllib UA
    req = urllib.request.Request(
        url + "/data/flux-boost.json",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/142.0 Safari/537.36"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def to_raw_accelerating(it: dict) -> dict:
    """合并格式 → 抓取格式（accelerating）。"""
    return {
        "spuId": it.get("spuId"),
        "title": it.get("title"),
        "category": it.get("category"),
        "state": it.get("state"),
        "periodFrom": it.get("periodFrom"),
        "periodTo": it.get("periodTo"),
        "readOnlyLinks": ["查看效果", "查看详情"],
        "skus": [{
            "skc": s.get("skc"),
            "color": s.get("color"),
            "size": s.get("size"),
            "orig": (None if s.get("origPrice") is None else f"${s['origPrice']:.2f}"),
            "after": (None if s.get("afterPrice") is None else f"${s['afterPrice']:.2f}"),
        } for s in (it.get("skus") or [])],
    }


def to_raw_pending(it: dict) -> dict:
    """合并格式 → 抓取格式（pending）。"""
    links = []
    if it.get("hasViewDetail"):
        links.append("查看详情")
    return {
        "spuId": it.get("spuId"),
        "title": it.get("title"),
        "category": it.get("category"),
        "site": it.get("site"),
        "firstListed": it.get("firstListed"),
        "tags": it.get("tags"),
        "stats": it.get("stats") or {},
        "optimize": it.get("optimize"),
        "readOnlyLinks": links,
        "writeLinksPresent": it.get("writeActions") or [],
    }


def main() -> int:
    url = sys.argv[1].rstrip("/")
    store = sys.argv[2].upper()
    sections = sys.argv[3:] or ["accelerating", "pending"]

    data = fetch(url)
    st = (data.get("stores") or {}).get(store)
    if not st:
        print("部署里没有该店：", store)
        return 2
    print("来源 generatedAt =", data.get("generatedAt"))

    for sec in sections:
        s = (st.get("sections") or {}).get(sec)
        if not s:
            print(f"  [跳过] {sec}：部署里没有该板块")
            continue
        conv = to_raw_accelerating if sec == "accelerating" else to_raw_pending
        items = [conv(x) for x in s.get("list", [])]
        if not items:
            print(f"  [跳过] {sec}：0 条，不写（避免又写成空）")
            continue
        payload = {
            "store": store,
            "section": sec,
            "filter": "流量加速中" if sec == "accelerating" else "流量加速器待开启",
            "generatedAt": s.get("scrapedAt") or data.get("generatedAt"),
            "total": len(items),
            "priced": sum(1 for x in items if x.get("skus")),
            "restoredFrom": url,
            "list": items,
        }
        out = FLUX_DIR / f"{store.lower()}_{sec}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  [恢复] {out.name}  {len(items)} 条（有价 {payload['priced']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())