"""探针：点开「库存扣减仓库」下拉（#warehouseId 内的 beast-core-select），打印选项。"""
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
    ap.add_argument("--pick", default="", help="要点的仓库名（给了就点它并回读）")
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
          const box = document.querySelector('#warehouseId');
          if (!box) return JSON.stringify({err: 'no-warehouseId'});
          const sel = box.querySelector('[data-testid=beast-core-select]');
          if (!sel) return JSON.stringify({err: 'no-select', html: box.outerHTML.slice(0, 500)});
          const r = sel.getBoundingClientRect();
          // React 受控：先 scrollIntoView 再点坐标
          sel.scrollIntoView({block: 'center'});
          const rr = sel.getBoundingClientRect();
          return JSON.stringify({hit: true, x: Math.round(rr.x + rr.width / 2), y: Math.round(rr.y + rr.height / 2),
                                 wrapper: sel.outerHTML.slice(0, 700)});
        }""")
        print("BOX:", r)
        import json
        d = json.loads(r)
        if not d.get("hit"):
            return 0
        pg.dispatch_mouse(d["x"], d["y"])
        pg.wait_for_timeout(1200)
        opts = pg.evaluate(r"""() => {
          const norm = s => (s || '').replace(/\s+/g, '');
          const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
          const items = [...document.querySelectorAll('li,[class*=item],[class*=Item],[class*=option]')]
            .filter(e => vis(e) && e.children.length <= 2)
            .map(e => norm(e.innerText)).filter(t => t && t.length < 40);
          return JSON.stringify([...new Set(items)]);
        }""")
        print("OPTIONS:", opts)
        if a.pick:
            hit = pg.evaluate("""(name) => {
              const norm = s => (s || '').replace(/\\s+/g, '');
              const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
              const el = [...document.querySelectorAll('li,[class*=item],[class*=Item],[class*=option]')]
                .filter(e => vis(e) && e.children.length <= 2 && norm(e.innerText) === name)[0];
              if (!el) return 'no-hit';
              el.scrollIntoView({block: 'center'});
              el.click();
              return JSON.stringify({text: norm(el.innerText), cls: String(el.className).slice(0, 80)});
            }""", a.pick)
            print("PICK:", hit)
            pg.wait_for_timeout(800)
            print("AFTER:", pg.evaluate("() => { const b=document.querySelector('#warehouseId'); return b ? b.innerText.replace(/\\s+/g,' ').slice(0,120) : 'none'; }"))
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
