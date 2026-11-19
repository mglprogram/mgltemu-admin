# -*- coding: utf-8 -*-
"""守着「发货模式」弹框，自动完成切换：识别 SKU → 查原库存 → 切卖家自发货 → 填库存 → 三层确认 → 记台账。

SKU 识别（按优先级）：
  1. 「SKU(精准查询)」框里有完整 SKU → 直接用
  2. 列表行的 `货号：XXX` + 记录到的点击行里的 `尺码：Y` → 拼成 SKU
     （用户在弹框出现前的那次点击会被 document 捕获阶段监听器记到 window.__lastRow）
  3. 兜底：用弹框里显示的原库存值，在该货号的尺码里唯一匹配 → 采用；不唯一就跳过并记 ambiguous

填数口径：
  两行（加州3仓 + kansas仓）：加州 = max(1, 原库存−1)，kansas = 1
  一行：max(1, 原库存)

坑（第一版踩过）：
  - 库存格必须只从弹框内取（__dlg tbody input），全局 `[id*=stockNum]` 会命中列表页元素，回读出鬼数。
  - 填数用 Click → Ctrl+A → insertText 覆盖，再回读比对，不一致重填（最多 3 次）。

用法：
    python scripts/goods/finish_dialog_watch.py --port 59670 --timeout 5400
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
import goto_spu as gs  # noqa: E402

LOGS = Path(__file__).resolve().parents[2] / "_logs"
LEDGER = LOGS / "switch_watch.jsonl"

# BEL 待改 SPU 顺序（跑完一个自动切下一个）
BEL_ORDER = ["1778062806", "1967628638", "2321151464", "2330660256", "2946934746",
             "3715326778", "4515698446", "4832753656", "6249945071", "6484180977",
             "6542860127", "7249832474", "8093849028", "8974123783", "9458759928",
             "9982707958"]



def load_stock_map() -> dict:
    m = {}
    for name, store in (("bel_blocked_skus2.json", "BEL"), ("kls_blocked_skus.json", "KLS")):
        p = LOGS / name
        if not p.exists():
            continue
        for it in json.loads(p.read_text(encoding="utf-8")):
            m[it["sku"]] = {"store": store, "stock": int(it["stock"]), "spu": it["spu"]}
    return m


# ---------- JS ----------
JS_HOOK = r"""
if (!window.__clickHook) {
  window.__clickHook = true;
  window.__lastRow = window.__lastRow || null;
  document.addEventListener('click', function (e) {
    try {
      var el = e.target;
      var tr = el && el.closest ? el.closest('tr') : null;
      window.__lastRow = tr ? (tr.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim() : null;
      window.__lastRowTs = Date.now();
    } catch (err) {}
  }, true);
}
return 'ok';"""

JS_DLG = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 300 && /发货模式/.test(t(d)) && d.querySelectorAll('tbody tr').length > 0;
});
if (!boxes.length) { window.__dlg = null; return 'closed'; }
boxes.sort(function (a, b) {
  return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length; });
var d = boxes[0];
window.__dlg = d;
var rows = Array.prototype.slice.call(d.querySelectorAll('tbody tr')).map(function (tr) {
  return Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
});
var ins = Array.prototype.slice.call(d.querySelectorAll('tbody input'));
return JSON.stringify({rows: rows, ninputs: ins.length,
                       vals: ins.map(function (i) { return i.value; })});"""

JS_CTX = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = {skuBox: '', code: '', size: '', lastRow: window.__lastRow || '', dlgStock: []};
var inputs = Array.prototype.slice.call(document.querySelectorAll('input'));
inputs.forEach(function (i) {
  var ph = i.getAttribute('placeholder') || '';
  if (ph.indexOf('多个查询请空格或逗号依次输入') < 0) return;
  var v = (i.value || '').trim();
  if (!v) return;
  v.split(/[\s,，]+/).forEach(function (tok) {
    if (/^(BEL|BK|BH)-[A-Za-z0-9\-]+$/i.test(tok)) out.skuBox = tok;
  });
});
var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
for (var k = 0; k < trs.length; k++) {
  var m = t(trs[k]).match(/货号[:：]\s*([A-Za-z0-9\-]+)/);
  if (m) { out.code = m[1]; break; }
}
var lm = (out.lastRow || '').match(/尺码[:：]\s*([A-Za-z0-9]+)/);
if (lm) out.size = lm[1];
if (window.__dlg) {
  Array.prototype.slice.call(window.__dlg.querySelectorAll('tbody tr')).forEach(function (tr) {
    Array.prototype.slice.call(tr.querySelectorAll('td')).forEach(function (td) {
      var s = t(td);
      if (/^\d+$/.test(s)) out.dlgStock.push(parseInt(s, 10));
    });
  });
}
return JSON.stringify(out);"""

JS_RESET_ROW = "window.__lastRow = null; return 'ok';"

JS_CELLS = r"""
if (!window.__dlg) return '[]';
var ins = Array.prototype.slice.call(window.__dlg.querySelectorAll('tbody input'));
return JSON.stringify(ins.map(function (i) {
  var r = i.getBoundingClientRect();
  return [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)];
}));"""

JS_VALS = r"""
if (!window.__dlg) return '[]';
var ins = Array.prototype.slice.call(window.__dlg.querySelectorAll('tbody input'));
return JSON.stringify(ins.map(function (i) { return i.value; }));"""

JS_MODES = r"""
if (!window.__dlg) return '[]';
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
return JSON.stringify(Array.prototype.slice.call(window.__dlg.querySelectorAll('tbody tr')).map(function (tr) {
  return t(tr.querySelector('td')); }));"""

JS_OPEN_SEL = r"""
if (!window.__dlg) return 'no-dlg';
var s = window.__dlg.querySelector('[data-testid="beast-core-select"]');
if (!s) return 'no-sel';
var h = s.querySelector('[data-testid="beast-core-select-header"]') || s;
return window.__zr.call(h, 'onClick');"""

JS_SEL_XY = r"""
if (!window.__dlg) return 'no-dlg';
var s = window.__dlg.querySelector('[data-testid="beast-core-select"]');
if (!s) return 'no-sel';
var h = s.querySelector('[data-testid="beast-core-select-header"]') || s;
var r = h.getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);"""

JS_OPT = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var c = Array.prototype.slice.call(document.querySelectorAll('div,li,span')).filter(function (e) {
  return !e.children.length && t(e) === '卖家自发货' && e.getClientRects().length; });
if (!c.length) return 'none';
var r = c[c.length - 1].getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);"""

JS_BTN = r"""
var t = function (e) { return (e.innerText || '').trim(); };
var pat = %s;
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  return new RegExp(pat).test(d.innerText || '') && d.querySelectorAll('button').length > 0; });
if (!boxes.length) return 'none';
boxes.sort(function (a, b) { return a.getBoundingClientRect().width - b.getBoundingClientRect().width; });
var b = Array.prototype.slice.call(boxes[0].querySelectorAll('button')).filter(function (x) {
  return t(x) === '确认'; })[0];
if (!b) return 'no-btn';
var r = b.getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);"""

JS_ERR = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
if (!window.__dlg) return '';
var s = t(window.__dlg);
var m = s.match(/(库存数不能为空|修改库存数必须大于\s*0|请填写当前库存|请输入库存)/g);
return m ? m.join('|') : '';"""

# 切到「卖家自发货」之后，从弹框每行的 React props 里挖 warehouseId（拿 kansas 的 id 用）
JS_WH = r"""
if (!window.__dlg) return '[]';
var out = [];
var trs = Array.prototype.slice.call(window.__dlg.querySelectorAll('tbody tr'));
function walk(o, d, path) {
  if (d > 6 || o === null || typeof o !== 'object' || out.length > 60) return;
  try {
    for (var k in o) {
      if (out.length > 60) return;
      var v; try { v = o[k]; } catch (e) { continue; }
      if (typeof v === 'string') {
        if (/^WH-\d{6,}$/.test(v)) out.push([path + '.' + k, v]);
        else if (/warehouseName/i.test(k)) out.push([path + '.' + k, v]);
      } else if (typeof v === 'object' && d < 6) {
        if (Array.isArray(v)) { for (var i = 0; i < Math.min(v.length, 5); i++) walk(v[i], d + 1, path + '.' + k + '[' + i + ']'); }
        else walk(v, d + 1, path + '.' + k);
      }
    }
  } catch (e) {}
}
trs.forEach(function (tr, idx) {
  var ck = Object.keys(tr).filter(function (k) { return k.indexOf('__reactFiber') === 0; })[0];
  if (!ck) return;
  var node = tr[ck], hops = 0;
  while (node && hops < 9) {
    if (node.memoizedProps) walk(node.memoizedProps, 0, 'r' + idx);
    node = node.return; hops += 1;
  }
});
var s = {}, u = [];
out.forEach(function (x) { var k = JSON.stringify(x); if (!s[k]) { s[k] = 1; u.push(x); } });
return JSON.stringify(u.slice(0, 40));"""


async def ev(cap, js, timeout=40):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True},
                       timeout=timeout)
    if r.get("exceptionDetails"):
        return "EXC:" + str(r["exceptionDetails"])[:160]
    return (r.get("result") or {}).get("value")


async def click(cap, xy, settle=0.25):
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1}, timeout=20)
        await asyncio.sleep(settle)


async def set_cell(cap, xy, value):
    await click(cap, xy)
    await asyncio.sleep(0.35)
    for tp in ("keyDown", "keyUp"):
        await cap.send("Input.dispatchKeyEvent", {"type": tp, "key": "a", "code": "KeyA",
                                                  "windowsVirtualKeyCode": 65, "modifiers": 2},
                       timeout=10)
    await asyncio.sleep(0.15)
    await cap.send("Input.insertText", {"text": str(value)}, timeout=20)
    await asyncio.sleep(0.3)
    for tp in ("keyDown", "keyUp"):
        await cap.send("Input.dispatchKeyEvent", {"type": tp, "key": "Tab", "code": "Tab",
                                                  "windowsVirtualKeyCode": 9}, timeout=10)
    await asyncio.sleep(0.3)


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def resolve_sku(ctx, dlg, stock_map):
    """返回 (sku, how) 或 (None, 原因)。"""
    code = (ctx.get("code") or "").strip()
    cands = {k: v for k, v in stock_map.items() if code and k.rsplit("-", 1)[0] == code}

    def by_suffix(size):
        if not code or not size:
            return None
        want = "%s-%s" % (code, size)
        return want if want in stock_map else None

    # 1) SKU 框直给
    box = (ctx.get("skuBox") or "").strip()
    if box and box in stock_map:
        return box, "sku-box"
    # 2) 货号 + 点击行的尺码
    s = by_suffix((ctx.get("size") or "").strip())
    if s:
        return s, "code+size"
    # 3) 兜底：弹框里显示的原库存在该货号下唯一
    dlg_vals = [int(x) for x in (dlg.get("vals") or []) if str(x).isdigit()]
    pool = [int(x) for x in (ctx.get("dlgStock") or []) if isinstance(x, int)]
    probe = dlg_vals + pool
    if code and probe:
        hits = [k for k, v in cands.items() if v["stock"] in probe]
        if len(hits) == 1:
            return hits[0], "stock-match"
        if len(hits) > 1:
            return None, "ambiguous(%s)" % ",".join(sorted(hits))
    if not code:
        return None, "no-code"
    return None, "unresolved"


async def handle_one(cap, sku, base, dlg):
    n = int(dlg.get("ninputs") or 0)
    want = [max(1, base - 1), 1] if n >= 2 else [max(1, base)]
    modes = json.loads(await ev(cap, JS_MODES) or "[]")
    if not n or any("合作对接仓发货" in str(x) for x in modes):
        ok = False
        for attempt in range(5):
            if attempt % 2 == 0:
                await ev(cap, JS_OPEN_SEL)
            else:
                xy = await ev(cap, JS_SEL_XY)
                if str(xy).startswith("["):
                    await click(cap, json.loads(xy))
            await asyncio.sleep(1.3)
            opt = await ev(cap, JS_OPT)
            if str(opt).startswith("["):
                await click(cap, json.loads(opt))
                await asyncio.sleep(2.2)
            cells = json.loads(await ev(cap, JS_CELLS) or "[]")
            if cells:
                ok = True
                n = len(cells)
                want = [max(1, base - 1), 1] if n >= 2 else [max(1, base)]
                break
        if not ok:
            log({"event": "fail", "sku": sku, "why": "切换下拉后没出现库存格"})
            return False
    # 填 + 校验
    try:
        wh = json.loads(await ev(cap, JS_WH) or "[]")
        if wh:
            p = LOGS / "warehouse_ids.json"
            old = {}
            if p.exists():
                try:
                    old = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    old = {}
            for path, val in wh:
                old.setdefault(val if str(val).startswith("WH-") else "name:" + str(val), path)
            p.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
            log({"event": "warehouse-ids", "sku": sku,
                 "pairs": [x for x in wh if str(x[1]).startswith("WH-") or "warehouseName" in x[0]][:12]})
    except Exception:
        pass
    for attempt in range(3):
        cells = json.loads(await ev(cap, JS_CELLS) or "[]")
        if len(cells) != n or not n:
            await asyncio.sleep(1.0)
            n = len(cells)
            if n:
                want = [max(1, base - 1), 1] if n >= 2 else [max(1, base)]
            continue
        for k, c in enumerate(cells):
            await set_cell(cap, c, want[k] if k < len(want) else want[-1])
        got = json.loads(await ev(cap, JS_VALS) or "[]")
        got_n = [int(x) if str(x).isdigit() else x for x in got]
        if got_n == want:
            break
        log({"event": "refill", "sku": sku, "want": want, "got": got})
        await asyncio.sleep(0.8)
    else:
        got = await ev(cap, JS_VALS)
        log({"event": "fail", "sku": sku, "why": "库存填不进去", "want": want, "got": got})
        return False
    # 三层确认
    layers = ("当前库存", "确认修改发货模式", "建议SKU下每个发货仓")
    clicked = []
    for pat in layers:
        for _ in range(4):
            c = await ev(cap, JS_BTN % json.dumps(pat))
            if str(c).startswith("["):
                await click(cap, json.loads(c))
                clicked.append(pat)
                break
            await asyncio.sleep(1.2)
        await asyncio.sleep(2.0)
    closed = False
    for _ in range(45):
        err = await ev(cap, JS_ERR)
        if err:
            log({"event": "fail", "sku": sku, "why": "页面报错: %s" % err, "want": want})
            return False
        if await ev(cap, JS_DLG) == "closed":
            closed = True
            break
        await asyncio.sleep(1.0)
    log({"event": "done" if closed else "unclear", "sku": sku, "base": base,
         "filled": want, "layers": clicked, "closed": closed})
    return closed


async def maybe_advance(cap, stock_map, order):
    """当前货号的所有尺码都已是「卖家自发货」→ 自动查询下一个 SPU。返回下一个 SPU（未推进为 None）。"""
    try:
        st = await gs.get_status(cap)
    except Exception:
        return None
    code = st.get("code") or ""
    rows = st.get("rows") or []
    if not code or not rows:
        return None
    expect = {k for k in stock_map if k.rsplit("-", 1)[0] == code}
    if not expect:
        return None
    if any(r.get("mode") != "卖家自发货" for r in rows):
        return None
    if len(rows) < len(expect):
        return None
    spu = stock_map[next(iter(expect))]["spu"]
    if spu not in order:
        return None
    i = order.index(spu)
    if i + 1 >= len(order):
        log({"event": "spu-done-all", "code": code, "spu": spu})
        return None
    nxt = order[i + 1]
    nxt_code = ""
    for k, v in stock_map.items():
        if v["spu"] == nxt:
            nxt_code = k.rsplit("-", 1)[0]
            break
    ok, detail = await gs.goto_spu(cap, nxt, expect_code=nxt_code)
    log({"event": "advance", "from": code, "to_spu": nxt, "to_code": nxt_code,
         "ok": ok, "detail": detail if not ok else ""})
    return nxt if ok else None


async def main_async(a) -> int:
    stock_map = load_stock_map()
    print("库存表 %d 个 SKU" % len(stock_map), flush=True)
    end = time.time() + a.timeout
    last_sig = None
    while time.time() < end:
        pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
                 if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
        if not pages:
            print("没有商品列表页，10s 后重试", flush=True)
            await asyncio.sleep(10)
            continue
        cap = None
        try:
            cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
            await zreact.inject(cap)
            await ev(cap, JS_HOOK)
            print("已挂上 port=%d，等弹框…" % a.port, flush=True)
            while time.time() < end:
                st = await ev(cap, JS_DLG)
                if not str(st).startswith("{"):
                    last_sig = None
                    await asyncio.sleep(1.0)
                    continue
                dlg = json.loads(st)
                sig = (dlg.get("ninputs"), tuple(dlg.get("vals") or ()),
                       tuple(str(r) for r in dlg.get("rows", [])))
                if sig == last_sig:
                    await asyncio.sleep(1.0)
                    continue
                ctx = json.loads(await ev(cap, JS_CTX) or "{}")
                sku, how = resolve_sku(ctx, dlg, stock_map)
                print("弹框 code=%r size=%r box=%r → %s (%s)"
                      % (ctx.get("code"), ctx.get("size"), ctx.get("skuBox"), sku, how), flush=True)
                last_sig = sig
                if not sku:
                    log({"event": "skip", "why": how, "code": ctx.get("code"),
                         "size": ctx.get("size"), "dlg": dlg.get("vals")})
                    await asyncio.sleep(2.0)
                    continue
                ok = await handle_one(cap, sku, stock_map[sku]["stock"], dlg)
                await ev(cap, JS_RESET_ROW)
                if ok:
                    await asyncio.sleep(4.0)
                    if not a.no_advance:
                        await maybe_advance(cap, stock_map, BEL_ORDER)
        except Exception as e:  # noqa: BLE001
            print("连接异常，重连: %s" % str(e)[:160], flush=True)
            await asyncio.sleep(5)
        finally:
            if cap is not None:
                try:
                    await cap.__aexit__(None, None, None)
                except Exception:
                    pass
        await asyncio.sleep(2)
    print("监听结束", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=5400)
    ap.add_argument("--no-advance", action="store_true")
    ap.add_argument("--store", default="BEL", choices=("BEL", "KLS"))
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
