# -*- coding: utf-8 -*-
"""pw_stock_query.py — 库存页按 Seller sku 查询 + 按库龄选 Fnsku（无头，2026-11-07 起）。

修正：上一版查完一单后页面没重置，再查会拿旧条件 ⇒ 返回全量。
本版每次：清三个框 → 只填 code(Seller sku) → 点查询 → **校验「总计 N 条」** →
再读行；若仍有全量行，如实报「查询未生效」而不是当作结果。
"""
import json
import re
import sys
import time

LIB = r"D:\AI-workplace\TEMU\scripts\power\lib"
sys.path.insert(0, LIB)
from cdp_transport import CdpPage, http_targets  # noqa: E402

PORT = 9333
PEEK = r"D:\AI-workplace\TEMU\scripts\power\out\_peek"
STOCK = "https://www.powerwarehouse.net/stock.php?act=list"

_TAIL = re.compile(r"[\d.]+$")
_BRANDS = ("BEL-", "BHY-", "BH-", "BK-", "BS2-", "BS-", "BLA-")


def relax(sku: str) -> str:
    s = str(sku or "").strip()
    up = s.upper()
    for pre in _BRANDS:
        if up.startswith(pre):
            s = s[len(pre):]
            break
    parts = s.split("-")
    if len(parts) >= 3:
        head, var, size = "-".join(parts[:-2]), parts[-2], parts[-1]
        v2 = _TAIL.sub("", var)
        return "%s-%s-%s" % (head, v2 or var, size)
    return s


JS_RESET = r"""() => {
  ['keywords', 'ck_sku', 'code'].forEach(n => {
    const e = document.querySelector('input[name=' + n + ']');
    if (e) { e.value = ''; e.dispatchEvent(new Event('input', {bubbles: true})); }
  });
  return 'ok';
}"""

JS_ROWS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
  const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const body = txt(document.body);
  const totalM = body.match(/总计\s*(\d+)\s*个记录/);
  const out = [];
  [...document.querySelectorAll('tr')].filter(vis).forEach(tr => {
    const tds = [...tr.querySelectorAll('td')].filter(vis).map(txt);
    if (tds.length < 9) return;
    if (!/^[A-Z0-9]{8,}$/.test(tds[1] || '')) return;
    out.push({fnsku: tds[1], seller: tds[2] || '', name: tds[3] || '',
              wh: tds[5] || '', transit: tds[6] || '', avail: tds[7] || '',
              age: tds[12] || '', in_at: tds[15] || ''});
  });
  return JSON.stringify({total: totalM ? totalM[1] : '', rows: out});
}"""


def main():
    sku = sys.argv[1] if len(sys.argv) > 1 else ""
    if not sku:
        print("用法: pw_stock_query.py <平台SKU>")
        return
    q = relax(sku)
    print("平台 SKU %s → Seller sku = %s" % (sku, q))

    pg = CdpPage(PORT, [t for t in http_targets(PORT) if t.get("type") == "page"][0])
    cdp = pg.cdp_session()

    if "stock.php" not in (pg.evaluate("() => location.href") or ""):
        pg.evaluate("(u) => { location.href = u; return 'ok'; }", STOCK)
        time.sleep(7)

    print("清空:", pg.evaluate(JS_RESET))
    time.sleep(0.4)

    b = json.loads(pg.evaluate(r"""() => {
      const e = document.querySelector('input[name=code]');
      e.scrollIntoView({block: 'center'});
      const r = e.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }"""))
    pg.dispatch_mouse(b["x"], b["y"]); time.sleep(0.3)
    cdp.send("Input.insertText", {"text": q})
    time.sleep(0.5)
    print("三框:", pg.evaluate(r"""() => {
      const g = n => { const e = document.querySelector('input[name=' + n + ']'); return e ? e.value : null; };
      return JSON.stringify({keywords: g('keywords'), ck_sku: g('ck_sku'), code: g('code')});
    }"""))

    btn = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('button,input[type=button],a')]
        .find(e => /^\s*查询\s*$/.test((e.innerText || e.value || '').trim()));
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }"""))
    pg.dispatch_mouse(btn["x"], btn["y"])
    time.sleep(8)

    d = json.loads(pg.evaluate(JS_ROWS) or "{}")
    rows = d.get("rows") or []
    print("\n总计 %s 条，解析 %d 行" % (d.get("total"), len(rows)))
    if str(d.get("total")) not in ("0", "") and len(rows) > 40:
        print("⚠ 结果过多，查询可能没生效（返回全量）")
    # 按库龄从高到低
    def age_num(x):
        m = re.match(r"[\d.]+", x.get("age") or "")
        return float(m.group(0)) if m else -1
    rows.sort(key=age_num, reverse=True)
    for r in rows[:12]:
        print("   Fnsku=%-15s 仓=%-8s 代发=%-5s 库龄=%-8s 入库=%-12s Seller=%s"
              % (r["fnsku"], r["wh"], r["avail"], r["age"], r["in_at"], r["seller"]))
    raw = pg.screenshot()
    with open(PEEK + "\\pw_stock3.png", "wb") as f:
        f.write(raw)
    print("落图 pw_stock3.png")


if __name__ == "__main__":
    main()
