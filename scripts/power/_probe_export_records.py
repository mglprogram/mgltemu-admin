"""探针：看导出记录表「请求时间」分布 + 排他性验证（分批设仓库）。只读、跑完关页。"""
from __future__ import annotations

import argparse
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
        dl = time.time() + 20
        pg = None
        while time.time() < dl:
            for x in C.list_pages(a.port):
                if (x.get("targetId") or x.get("id")) == tid:
                    pg = C.CdpPage(a.port, x)
                    break
            if pg is not None:
                break
            time.sleep(0.4)
        own = tid
        time.sleep(5)
        pg.wait_for_timeout(2000)
        r = pg.evaluate(r"""() => {
          const trs = [...document.querySelectorAll('tbody tr')].slice(0, 6);
          return JSON.stringify({
            n: document.querySelectorAll('tbody tr').length,
            rows: trs.map(tr => [...tr.children].map(td => (td.innerText || '').replace(/\s+/g, ' ').trim())),
            refreshBtns: [...document.querySelectorAll('button,div,span')]
              .filter(e => e.children.length === 0 && /刷新|查询/.test(e.innerText || ''))
              .map(e => (e.innerText || '').trim()).slice(0, 8),
          });
        }""")
        print("ROWS:", r)
        # 点一次「刷新/查询」（如有）再看
        pg.evaluate(r"""() => {
          const el = [...document.querySelectorAll('button,div,span')]
            .filter(e => e.children.length === 0 && /刷新/.test(e.innerText || ''))[0];
          if (el) { el.scrollIntoView({block:'center'}); el.click(); }
          return !!el;
        }""")
        pg.wait_for_timeout(2000)
        print("AFTER:", pg.evaluate(r"""() => {
          const tr = document.querySelector('tbody tr');
          return tr ? [...tr.children].map(td => (td.innerText || '').replace(/\s+/g, ' ').trim()).join(' | ') : '';
        }"""))
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
