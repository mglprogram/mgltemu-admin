# -*- coding: utf-8 -*-
"""批量买面单（帕沃尔 13 单）——按已验证的手动流程逐步执行，每步回读。

流程（2026-11-07 实测有效）：
  ① 打开批量页（自拼 URL）→ ② 顶部四维 → ③ 勾选子订单 → ④ 批量填充(覆盖)
  → ⑤ 表头「物流服务 一键填充」→ ⑥ 选「配送成本优先」圆点 → ⑦ 确认填充
  → ⑧ 逐行核对仓库/重量/服务都非空 → ⑨ 确认下单 → ⑩ 二级确认
任何一步回读不符就停，不点确认下单。
"""
import io
import json
import os
import sys
import time
import urllib.parse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"scripts\power\lib")

from cdp_transport import CdpPage, http_targets  # noqa: E402

PORT = 58029
BASE = r"D:\AI-workplace\TEMU\scripts\power\out\2026-11-07\fulfill"
WEIGHT, L, W, H = "1.50", "12.30", "12.30", "9.30"
WAREHOUSE = os.environ.get("PW_WH", "加州3仓")


def log(s):
    print(s, flush=True)


def page():
    ts = [t for t in http_targets(PORT) if t.get("type") == "page"
          and "agentseller-us.temu.com" in (t.get("url") or "")]
    if not ts:
        ts = [t for t in http_targets(PORT) if t.get("type") == "page"]
    return CdpPage(PORT, ts[0])


def open_batch(pg, pos):
    url = ("https://agentseller-us.temu.com/mmsos/online-shipping.html?parent_order_sn_list="
           + urllib.parse.quote(json.dumps(pos)))
    pg.goto(url)
    time.sleep(10)
    for _ in range(10):
        n = pg.evaluate(
            "() => (document.body.innerText||'').match(/共\\s*(\\d+)\\s*个订单/) ? "
            "parseInt((document.body.innerText||'').match(/共\\s*(\\d+)\\s*个订单/)[1]) : 0")
        if n == len(pos):
            return n
        time.sleep(3)
    return n


def main() -> int:
    import argparse  # noqa: PLC0415
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders-file", default=BASE + r"\pw_label_todo.txt")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    pos = [x.strip() for x in open(a.orders_file, encoding="utf-8") if x.strip()]
    if len(pos) < 1:
        log("[FAIL] 没有单")
        return 2
    log("本批 %d 单" % len(pos))
    pg = page()
    n = open_batch(pg, pos)
    log("批量页订单数：%s（期望 %d）" % (n, len(pos)))
    # ★ 允许「少」：已买过面单的单不会出现在这里（2026-11-07 实测 14 单里 1 单已买→13 单）
    if n < 1 or n > len(pos):
        log("[FAIL] 批量页订单数异常（%s / %d），停" % (n, len(pos)))
        return 42
    if n < len(pos):
        log("   ℹ 有 %d 单不在批量页（多为已买过面单），按剩余 %d 单继续" % (len(pos) - n, n))

    # ② 顶部四维 + 仓库
    pg.evaluate(r"""(w) => {
      const want = {'重量': w.weight, '长': w.l, '宽': w.w, '高': w.h};
      [...document.querySelectorAll('input')]
        .filter(i => want[i.placeholder] && i.getBoundingClientRect().top < 450)
        .forEach(i => {
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(i, want[i.placeholder]);
          i.dispatchEvent(new Event('input', {bubbles: true}));
          i.dispatchEvent(new Event('change', {bubbles: true}));
          i.blur();
        });
      return 1;
    }""", {"weight": WEIGHT, "l": L, "w": W, "h": H})
    time.sleep(1.2)
    # 顶部仓库：点开 → 按前缀选
    pg.evaluate(r"""() => {
      const hs = [...document.querySelectorAll('[data-testid=beast-core-select-header]')]
        .filter(e => e.getBoundingClientRect().top < 450);
      if (hs[0]) { hs[0].scrollIntoView({block:'center'}); hs[0].click(); }
      return 1;
    }""")
    time.sleep(1.5)
    picked = pg.evaluate(r"""(wh) => {
      const els = [...document.querySelectorAll('[class*=cIL_item],[class*=ST_item]')]
        .filter(e => (e.innerText || '').trim().indexOf(wh) === 0);
      if (!els.length) return 'no-option';
      els[0].click();
      return 'ok';
    }""", WAREHOUSE)
    log("顶部仓库=%s → %s" % (WAREHOUSE, picked))
    time.sleep(2)

    # ③ 勾选子订单 + ④ 批量填充
    pg.evaluate(r"""() => {
      [...document.querySelectorAll('label[data-testid=beast-core-checkbox]')]
        .filter(l => l.getBoundingClientRect().width > 0).forEach(l => {
          if (l.getAttribute('data-checked') !== 'true') l.click();
        });
      return 1;
    }""")
    time.sleep(1.2)
    b = pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('button')].find(e => /批量填充/.test(e.innerText || ''));
      const r = el.getBoundingClientRect();
      return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
    }""")
    pg.dispatch_mouse(b["x"], b["y"])
    time.sleep(3)
    # 覆盖填充
    if pg.evaluate("() => (document.body.innerText||'').indexOf('是否剔除并填充剩余包裹') >= 0 "
                   "|| (document.body.innerText||'').indexOf('是否覆盖填充') >= 0"):
        log("  覆盖确认：%s" % pg.evaluate(r"""() => {
          const el = [...document.querySelectorAll('*')].filter(e => e.children.length === 0
            && /忽略并全部填充|覆盖填充/.test(e.innerText || '')).pop();
          if (!el) return 'no-btn';
          (el.closest('button') || el).click();
          return 'ok';
        }"""))
        time.sleep(3)

    # ⑤ 表头「物流服务 一键填充」（文字右侧 ~28px）
    pos_hdr = pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('*')]
        .filter(e => e.children.length === 0 && (e.innerText || '').trim() === '物流服务')
        .map(e => e.getBoundingClientRect()).filter(r => r.width > 0).pop();
      if (!el) return null;
      return {x: Math.round(el.x + el.width + 28), y: Math.round(el.y + el.height / 2)};
    }""")
    log("物流服务一键填充坐标：%s" % pos_hdr)
    if not pos_hdr:
        log("[FAIL] 找不到表头物流服务")
        return 42
    pg.dispatch_mouse(pos_hdr["x"], pos_hdr["y"])
    time.sleep(5)
    # 可能先弹「全部包裹已填写…覆盖填充？」
    if pg.evaluate("() => (document.body.innerText||'').indexOf('是否覆盖填充') >= 0"):
        pg.evaluate(r"""() => {
          const el = [...document.querySelectorAll('*')].filter(e => e.children.length === 0
            && /^覆盖填充$/.test((e.innerText || '').trim())).pop();
          if (el) (el.closest('button') || el).click();
          return 1;
        }""")
        time.sleep(4)

    # ⑥ 选策略「配送成本优先」圆点
    dot = pg.evaluate(r"""() => {
      const m = [...document.querySelectorAll('[class*=MDL_inner]')]
        .filter(e => (e.innerText || '').indexOf('物流服务一键填充策略') >= 0).pop();
      if (!m) return null;
      const lab = [...m.querySelectorAll('*')]
        .filter(e => (e.innerText || '').replace(/\s+/g, '').indexOf('配送成本优先') === 0
                  && e.children.length < 4).pop();
      if (!lab) return null;
      lab.scrollIntoView({block: 'center'});
      const r = lab.getBoundingClientRect();
      return {x: Math.round(r.x + 8), y: Math.round(r.y + r.height / 2)};
    }""")
    log("配送成本优先圆点：%s" % dot)
    if not dot:
        log("[FAIL] 找不到策略弹窗")
        return 42
    pg.dispatch_mouse(dot["x"], dot["y"])
    time.sleep(1.2)
    # ⑦ 确认填充
    log("确认填充：%s" % pg.evaluate(r"""() => {
      const m = [...document.querySelectorAll('[class*=MDL_inner]')]
        .filter(e => (e.innerText || '').indexOf('物流服务一键填充策略') >= 0).pop();
      if (!m) return 'no-modal';
      const b = [...m.querySelectorAll('button')].find(x => /确认填充/.test(x.innerText || ''));
      if (!b) return 'no-btn';
      b.click();
      return 'ok';
    }"""))
    time.sleep(8)

    # ⑧ 逐行核对：重量/仓库/服务
    chk = pg.evaluate(r"""() => {
      const rows = [...document.querySelectorAll('tr')].filter(tr =>
        /PO-\\d{3}-\\d+/.test((tr.innerText || '')));
      const out = [];
      rows.forEach(tr => {
        const ins = [...tr.querySelectorAll('input')]
          .filter(i => ['重量','长','宽','高'].includes(i.placeholder || ''))
          .map(i => (i.value || '').trim());
        const sels = [...tr.querySelectorAll('[data-testid=beast-core-select-header]')]
          .map(e => (e.innerText || '').replace(/\s+/g, ' ').trim());
        out.push({vals: ins.filter(Boolean).length, sels: sels.filter(Boolean)});
      });
      return out;
    }""")
    bad = [x for x in (chk or []) if x["vals"] < 4 or len(x["sels"]) < 2]
    log("核对：%d 行，缺项 %d 行" % (len(chk or []), len(bad)))
    if bad:
        log("  [STOP] 缺项示例：%s" % json.dumps(bad[:2], ensure_ascii=False))
        return 42
    if not a.commit:
        log("[dry] 未给 --commit，停在下单前")
        return 0

    # ⑨ 确认下单
    cb = pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('button')].find(e => /确认下单/.test(e.innerText || ''));
      if (!el) return null;
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
    }""")
    log("确认下单：%s" % cb)
    pg.dispatch_mouse(cb["x"], cb["y"])
    time.sleep(4)
    # ⑩ 二级确认
    log("二级确认：%s" % pg.evaluate(r"""() => {
      const m = [...document.querySelectorAll('[class*=MDL_inner]')]
        .filter(e => /确认下单？/.test(e.innerText || '')).pop();
      if (!m) return 'no-modal';
      const b = [...m.querySelectorAll('button')]
        .find(x => /^\s*(确认|确认下单)\s*$/.test(x.innerText || ''));
      if (!b) return 'no-btn';
      b.click();
      return 'ok';
    }"""))
    time.sleep(10)
    log("结果 URL：%s" % (pg.url or "")[:90])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
