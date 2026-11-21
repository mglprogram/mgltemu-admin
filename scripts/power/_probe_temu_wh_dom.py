"""探针：打印「库存扣减仓库」筛选控件的 DOM 结构（只读，跑完关页）。"""
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
    a = ap.parse_args()
    own = ""
    try:
        tid = C.create_target(a.port, EXPORT_URL)
        deadline = time.time() + 20
        pg = None
        while time.time() < deadline:
            for x in C.list_pages(a.port):
                if (x.get("targetId") or x.get("id")) == tid:
                    pg = C.CdpPage(a.port, x)
                    break
            if pg is not None:
                break
            time.sleep(0.4)
        own = tid
        time.sleep(4)
        pg.wait_for_timeout(1500)
        r = pg.evaluate(r"""() => {
          const norm = s => (s || '').replace(/\s+/g, '');
          const out = {};
          const label = [...document.querySelectorAll('div,span,label')]
            .filter(e => e.children.length === 0 && norm(e.innerText) === '库存扣减仓库')[0];
          if (!label) return JSON.stringify({err: 'no-label'});
          let node = label;
          const chain = [];
          for (let i = 0; i < 5 && node; i++) {
            chain.push({i, tag: node.tagName, cls: String(node.className).slice(0, 100),
                        html: (node.outerHTML || '').slice(0, 900)});
            node = node.parentElement;
          }
          // 页面上所有带 select 类名的可见节点
          const sels = [...document.querySelectorAll('div[class*=select],div[class*=Select],div[class*=dropdown]')]
            .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0; })
            .map(e => ({cls: String(e.className).slice(0, 90), txt: norm(e.innerText).slice(0, 40),
                        html: (e.outerHTML || '').slice(0, 300)}));
          return JSON.stringify({chain, sels: sels.slice(0, 20)});
        }""")
        print(r)
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
