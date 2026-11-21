"""探针：看 TEMU 导出页（export-order.html）有没有「发货仓 / 仓库」筛选。

只读：自己开一个临时页、读 DOM 文本与筛选项、打印后关掉。
用法：python scripts/power/_probe_temu_export_filters.py --port 53773
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

import cdp_transport as C  # noqa: E402

EXPORT_URL = "https://agentseller-us.temu.com/mmsos/export-order.html"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--url", default=EXPORT_URL)
    a = ap.parse_args()
    b = None
    own = ""
    try:
        own = C.create_target(a.port, a.url)
        deadline = time.time() + 20
        pg = None
        while time.time() < deadline:
            for x in C.list_pages(a.port):
                if (x.get("targetId") or x.get("id")) == own:
                    pg = C.CdpPage(a.port, x)
                    break
            if pg is not None:
                break
            time.sleep(0.4)
        if pg is None:
            raise SystemExit("FAIL: 新页没出现")
        time.sleep(4)
        pg.wait_for_timeout(2000)
        info = pg.evaluate(r"""() => {
          const norm = s => (s || '').replace(/\s+/g, '');
          const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
          const leaves = [...document.querySelectorAll('div,span,label,th,td')]
            .filter(e => e.children.length === 0 && vis(e));
          const texts = [...new Set(leaves.map(e => norm(e.innerText)).filter(Boolean))];
          const chips = [...new Set([...document.querySelectorAll('input')]
            .map(e => (e.value || '').trim()).filter(Boolean))];
          return JSON.stringify({url: location.href, title: document.title, chips, texts});
        }""")
        d = json.loads(info)
        print("URL:", d.get("url"))
        print("TITLE:", d.get("title"))
        print("INPUT VALUES:", json.dumps(d.get("chips"), ensure_ascii=False)[:1200])
        hits = [t for t in d.get("texts", []) if any(k in t for k in ("仓", "发货", "仓库", "物流", "履约", "导出"))]
        print("HITS:", json.dumps(hits, ensure_ascii=False)[:2000])
        print("FIRST100:", json.dumps(d.get("texts", [])[:100], ensure_ascii=False)[:2500])
    finally:
        if own:
            try:
                C.close_target(a.port, own)
            except Exception:  # noqa: BLE001
                pass
        try:
            b.close()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
