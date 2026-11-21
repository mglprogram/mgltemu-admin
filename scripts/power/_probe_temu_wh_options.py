"""探针：点开「库存扣减仓库」下拉，打印所有可选仓库名。只读，跑完关页。"""
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


def _page(port: int, url: str):
    tid = C.create_target(port, url)
    deadline = time.time() + 20
    pg = None
    while time.time() < deadline:
        for x in C.list_pages(port):
            if (x.get("targetId") or x.get("id")) == tid:
                pg = C.CdpPage(port, x)
                break
        if pg is not None:
            break
        time.sleep(0.4)
    if pg is None:
        raise SystemExit("FAIL: 新页没出现")
    time.sleep(4)
    pg.wait_for_timeout(2000)
    return pg, tid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    own = ""
    try:
        pg, own = _page(a.port, EXPORT_URL)
        # 找到「库存扣减仓库」标签，取其旁边可点的筛选框并点开
        r = pg.evaluate(r"""() => {
          const norm = s => (s || '').replace(/\s+/g, '');
          const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
          const label = [...document.querySelectorAll('div,span,label')]
            .filter(e => e.children.length === 0 && vis(e) && norm(e.innerText) === '库存扣减仓库')[0];
          if (!label) return JSON.stringify({err: 'no-label'});
          // 标签同一行内、位于其右侧的可点控件（select / input / 带箭头的 div）
          const row = label.parentElement;
          const cands = [...(row ? row.querySelectorAll('input,div[class*=select],div[class*=Select],span[class*=select]') : [])]
            .filter(vis)
            .map(e => ({tag: e.tagName, cls: String(e.className).slice(0, 80),
                        val: (e.value || '').trim(), txt: norm(e.innerText).slice(0, 40)}));
          const btn = [...(row ? row.querySelectorAll('input,div,span') : [])].filter(vis)
            .find(e => (e.value || '').trim() === '全部' || norm(e.innerText) === '全部');
          if (btn) { btn.scrollIntoView({block: 'center'}); btn.click(); }
          return JSON.stringify({cands, clicked: !!btn, rowTxt: norm(row ? row.innerText : '').slice(0, 120)});
        }""")
        print("CAND:", r)
        pg.wait_for_timeout(1200)
        opts = pg.evaluate(r"""() => {
          const norm = s => (s || '').replace(/\s+/g, '');
          const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
          const items = [...document.querySelectorAll('li,[class*=item],[class*=option]')]
            .filter(e => vis(e) && e.children.length <= 3)
            .map(e => norm(e.innerText)).filter(t => t && t.length < 40);
          return JSON.stringify([...new Set(items)].slice(0, 80));
        }""")
        print("OPTIONS:", opts)
    finally:
        if own:
            try:
                C.close_target(a.port, own)
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
