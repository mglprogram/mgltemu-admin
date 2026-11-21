"""手工探针：在 BHY 导出页按「恒信加州3 + 11-21 单日」走一遍，抓网络请求与页面报错。

判定点：
  · createOrdersExportTask 有没有发出、响应 code 是什么
  · 页面有没有 toast/错误文案
只读（会留一条导出记录，但那是用户要的手工验证）。
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
WH = os.environ.get("HX_WH", "恒信加州3")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--day", default="2026-11-21")
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
        pg.wait_for_timeout(1500)
        # 装网络钩子：记录所有含 export/Task 的请求
        pg.evaluate(r"""() => {
          window.__net = [];
          if (!window.__hooked) {
            const of = window.fetch;
            window.fetch = async function(...args) {
              const url = String(args[0] && (args[0].url || args[0]) || '');
              const opt = args[1] || {};
              let body = '';
              try { body = typeof opt.body === 'string' ? opt.body : ''; } catch (e) {}
              let res, txt = '';
              try {
                res = await of.apply(this, args);
                try { txt = await res.clone().text(); } catch (e) {}
              } catch (e) {
                window.__net.push({url, req: body.slice(0, 500), err: String(e).slice(0, 200)});
                throw e;
              }
              if (/export|Export|Task|task/.test(url)) {
                window.__net.push({url, req: body.slice(0, 800), status: res.status, resp: String(txt).slice(0, 600)});
              }
              return res;
            };
            window.__hooked = true;
          }
          return 'ok';
        }""")

        # 选仓库
        pg.evaluate(r"""() => {
          const box = document.querySelector('#warehouseId');
          const sel = box && box.querySelector('[data-testid=beast-core-select]');
          if (!sel) return 'no-sel';
          sel.scrollIntoView({block: 'center'});
          const r = sel.getBoundingClientRect();
          sel.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
          sel.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
          sel.dispatchEvent(new MouseEvent('click', {bubbles: true}));
          return JSON.stringify({x: Math.round(r.x), y: Math.round(r.y)});
        }""")
        pg.wait_for_timeout(1000)
        pick = pg.evaluate(r"""(name) => {
          const norm = s => (s || '').replace(/\s+/g, '');
          const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
          const el = [...document.querySelectorAll('li,[class*=item],[class*=Item],[class*=option]')]
            .filter(e => vis(e) && e.children.length <= 2 && norm(e.innerText) === name)[0];
          if (!el) return 'no-hit';
          el.scrollIntoView({block: 'center'});
          ['mousedown', 'mouseup', 'click'].forEach(t => el.dispatchEvent(new MouseEvent(t, {bubbles: true})));
          return norm(el.innerText);
        }""", WH)
        pg.wait_for_timeout(800)
        cur = pg.evaluate(r"""() => {
          const box = document.querySelector('#warehouseId');
          const inp = box && box.querySelector('input');
          return inp ? (inp.value || '').trim() : '';
        }""")
        print("PICK:", pick, "| NOW:", cur)

        # 日期：单日 11-21（用元素级点击，和 driver 一致）
        okd = pg.evaluate(r"""() => {
          const els = [...document.querySelectorAll('div,span')].filter(e => {
            const r = e.getBoundingClientRect(); return r.width > 0 && /2026-11|请选择/.test(e.innerText || '');
          });
          return JSON.stringify(els.slice(0, 3).map(e => ({t: (e.innerText || '').slice(0, 30),
            x: Math.round(e.getBoundingClientRect().x), y: Math.round(e.getBoundingClientRect().y)})));
        }""")
        print("DATE-INPUT-CAND:", okd)
        pg.wait_for_timeout(200)

        # 直接按 driver 的流程设日期
        import temu_sales_driver as D  # noqa: PLW0406
        from datetime import date
        day = date.fromisoformat(a.day)
        set_ok = D._set_export_date(pg, day, exact_day=True)
        print("SET-DATE:", set_ok, "| read:", D._read_date_input(pg))
        # 重选仓库（日期之后）
        cur2 = pg.evaluate(r"""() => { const b = document.querySelector('#warehouseId'); const i = b && b.querySelector('input'); return i ? i.value : ''; }""")
        if cur2 != WH:
            pg.evaluate(r"""() => {
              const sel = document.querySelector('#warehouseId [data-testid=beast-core-select]');
              if (sel) { sel.scrollIntoView({block:'center'});
                ['mousedown','mouseup','click'].forEach(t => sel.dispatchEvent(new MouseEvent(t, {bubbles:true}))); }
              return 1;
            }""")
            pg.wait_for_timeout(900)
            pg.evaluate(r"""(name) => {
              const norm = s => (s || '').replace(/\s+/g, '');
              const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
              const el = [...document.querySelectorAll('li,[class*=item],[class*=Item],[class*=option]')]
                .filter(e => vis(e) && e.children.length <= 2 && norm(e.innerText) === name)[0];
              if (el) ['mousedown','mouseup','click'].forEach(t => el.dispatchEvent(new MouseEvent(t, {bubbles:true})));
              return !!el;
            }""", WH)
            pg.wait_for_timeout(700)
            print("RE-PICK, date now:", D._read_date_input(pg))
        D._confirm_calendar(pg)
        pg.wait_for_timeout(500)
        print("BEFORE-SUBMIT date:", D._read_date_input(pg),
              "| wh:", pg.evaluate("() => { const b=document.querySelector('#warehouseId [data-testid=beast-core-select-htmlInput]'); return b ? b.value : ''; }"))

        # 点确认导出（元素级）
        clicked = pg.evaluate(r"""() => {
          const vis = e => { const r = e.getBoundingClientRect(); return e.getClientRects().length > 0 && r.width > 0; };
          const el = [...document.querySelectorAll('button,div,span')].filter(vis)
            .find(e => e.children.length === 0 && (e.innerText || '').replace(/\s+/g, '') === '确认导出');
          if (!el) return 'no-btn';
          const btn = el.closest('button') || el;
          btn.scrollIntoView({block: 'center'});
          ['mousedown', 'mouseup', 'click'].forEach(t => btn.dispatchEvent(new MouseEvent(t, {bubbles: true})));
          return JSON.stringify({disabled: !!btn.disabled, txt: (btn.innerText || '').trim()});
        }""")
        print("SUBMIT:", clicked)
        pg.wait_for_timeout(4000)
        net = pg.evaluate("() => JSON.stringify(window.__net || [])")
        print("NET:", str(net)[:3000])
        toast = pg.evaluate(r"""() => {
          const t = [...document.querySelectorAll('div,span')].filter(e => e.children.length === 0)
            .map(e => (e.innerText || '').trim())
            .filter(s => /失败|错误|请选择|不能|不支持|超出|无权限|库存扣减仓库/.test(s) && s.length < 80);
          return JSON.stringify([...new Set(t)].slice(0, 10));
        }""")
        print("TOAST-ish:", toast)
        pg.wait_for_timeout(3000)
        print("ROWS:", pg.evaluate(r"""() => {
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
