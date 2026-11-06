# -*- coding: utf-8 -*-
"""pw_deliver_ship.py — 帕沃尔「代发」全链：待确认 → 审核 → 未出跟踪号 → 批量获取单号。

用户口径（2026-11-06，本流程唯一权威）：
    · 「待确认订单（deliver.php?act=list&pb=1），直接通过 BEL 的店铺代码（634418227477419）、
       BHY（634418228526171）→ 提交」
    · 「先勾选那个序号的才会出现（批量按钮）」
    · 「确认之后 → deliver.php?act=list&pb=2 批量获取跟踪号」
    · 页头「全部订单」选 **未出跟踪号**（is_cd=2）再查询
    · 「每页拉到最大 300」

真机坑（每条都实测过，别改回去）：
    1. `#pageSize` 是 text 框，靠 `onkeypress` 回车触发 `listTable.changePageSize`；
       改 value 不生效；回车会**刷新页面** ⇒ CDP 会超时，必须重连后再继续。
    2. 「查询」**不能连点** —— 会弹原生 alert「您已提交查询操作,请勿重复点击…」，
       它**阻塞页面 JS**（所有 Runtime.evaluate 超时）。规避：先 `window.alert = ()=>{}`。
    3. 平台勾选用 **jQuery `attr('checked')`** 判断，鼠标点只改 property ⇒
       必须同时 `cb.checked = true` 和 `cb.setAttribute('checked','checked')`。
    4. 批量按钮（`提交` / `批量获取单号` / `下单代发`）都在**页面最底部**，
       且必须**先勾选行**才有效；坐标要在点之前现取（滚动会变）。
    5. `select_types`（pb=1）= `确认订单:order_queren`；pb=2 底部另有
       `批量获取单号:edit_order('huoqumd')` / `回传跟踪号:edit_order('tuihui_danhao')`。

用法：
    python scripts/power/pw_deliver_ship.py --shop BEL --stage confirm
    python scripts/power/pw_deliver_ship.py --shop BHY --stage track
    python scripts/power/pw_deliver_ship.py --shops BEL,BHY --stage all
    python scripts/power/pw_deliver_ship.py --shop BEL --stage track --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

PEEK = os.path.join(_HERE, "out", "_peek")
PORT = 9333                      # 有头 Chrome 调试端口（用户可见）
SHOPS = {"BEL": "634418227477419", "BHY": "634418228526171"}
BASE = "https://www.powerwarehouse.net/"
PB_CONFIRM = BASE + "deliver.php?act=list&pb=1"
PB_TRACK = BASE + "deliver.php?act=list&pb=2"

JS_KILL_DIALOG = ("() => { window.alert = function(){}; "
                  "window.confirm = function(){ return true; }; "
                  "window.onbeforeunload = null; return 'ok'; }")

JS_SUM = r"""() => {
  const b = (document.body.innerText || '').replace(/\s+/g, ' ');
  const m = b.match(/总计\s*(\d+)\s*个记录分为\s*(\d+)\s*页[^ ]*每页\s*(\d+)/);
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
  const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const rows = [...document.querySelectorAll('tr')].filter(vis).map(txt)
    .filter(t => /PO-\d+-\d+|GSU|SEMI/.test(t));
  const trackRe = /1Z[0-9A-Z]{16}|GFUS\d{8,}|SPX[A-Z0-9]{6,}|SWX\d{6,}|YWE\d{8,}/;
  return JSON.stringify({
    summary: m ? m[0] : '', rows: rows.length,
    withTrack: rows.filter(t => trackRe.test(t)).length,
    total: m ? m[1] : '', pages: m ? m[2] : '', size: m ? m[3] : '',
    shop: (document.querySelector('input[name=shop_id]') || {}).value || '',
    cd: (document.querySelector('select[name=is_cd]') || {}).value || ''});
}"""

JS_CHECK_ALL = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
  const cbs = [...document.querySelectorAll('input[type=checkbox]')].filter(vis)
    .filter(c => /PO-\d+-\d+|GSU|SEMI/.test(((c.closest('tr') || {}).innerText || '')));
  cbs.forEach(c => { c.checked = true; c.setAttribute('checked', 'checked'); });
  return JSON.stringify({rows: cbs.length,
    checked: cbs.filter(c => c.getAttribute('checked') === 'checked').length});
}"""


def _zs():
    """有头 Chrome 的裸 CDP（不走紫鸟）。"""
    import cdp_transport as C  # noqa: PLC0415
    return C


def page():
    C = _zs()
    tgs = [t for t in C.http_targets(PORT) if t.get("type") == "page"]
    if not tgs:
        raise RuntimeError("端口 %d 没有可用页面（先开有头 Chrome："
                           "--remote-debugging-port=%d）" % (PORT, PORT))
    return C.CdpPage(PORT, tgs[0])


def _kill_dialog(pg, log=print):
    try:
        pg.evaluate(JS_KILL_DIALOG)
    except Exception as ex:  # noqa: BLE001
        log("   ⚠ 废弹窗失败（页面可能已被 alert 阻塞）：%s" % str(ex)[:60])


def _goto(pg, url, log=print):
    _kill_dialog(pg, log)
    pg.evaluate("(u) => { location.href = u; return 'ok'; }", url)
    time.sleep(9)
    pg = page()
    _kill_dialog(pg, log)
    return pg


def ensure_page_size(pg, want="300", log=print):
    """把「每页」拉到 want。★ 回车会刷新页面 ⇒ 之后必须重新取 page。"""
    cur = pg.evaluate("() => { const e=document.querySelector('#pageSize'); return e?e.value:'none'; }")
    if str(cur) == str(want):
        log("   每页已是 %s" % want)
        return pg
    b = json.loads(pg.evaluate(r"""() => {
      const e = document.querySelector('#pageSize');
      if (!e) return '{}';
      e.scrollIntoView({block: 'center'}); e.focus(); e.select();
      const r = e.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }""") or "{}")
    if not b.get("x"):
        log("   ⚠ 没有 #pageSize 控件")
        return pg
    cdp = pg.cdp_session()
    pg.dispatch_mouse(b["x"], b["y"]); time.sleep(0.2)
    cdp.send("Input.insertText", {"text": str(want)})
    time.sleep(0.4)
    try:
        for typ in ("keyDown", "char", "keyUp"):
            cdp.send("Input.dispatchKeyEvent",
                     {"type": typ, "key": "Enter", "code": "Enter", "text": "\r",
                      "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
    except Exception:  # noqa: BLE001  页面刷新会掐断 ws，属预期
        pass
    time.sleep(10)
    pg = page()
    _kill_dialog(pg, log)
    now = pg.evaluate("() => { const e=document.querySelector('#pageSize'); return e?e.value:'none'; }")
    log("   每页设为 %s" % now)
    return pg


def apply_filter(pg, shop: str, cd: str = "", log=print):
    """填店铺代码 +（可选）订单类型 → 点一次「查询」（**只点一次**，连点会弹 alert 阻塞）。"""
    cdp = pg.cdp_session()
    bx = json.loads(pg.evaluate(r"""() => {
      const e = document.querySelector('input[name=shop_id]');
      if (!e) return '{}';
      e.scrollIntoView({block: 'center'});
      const r = e.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                             cur: e.value});
    }""") or "{}")
    if not bx.get("x"):
        log("   ⚠ 没有店铺筛选框")
        return pg
    code = SHOPS.get(shop, shop)
    # ★ 2026-11-06 修（两次真机踩坑合并）：
    #   ① 不再“已是目标值就跳过” —— 页面刷新后框被清空，脚本却以为还是目标值，
    #      结果只用「未出跟踪号」筛了**全部店铺**（实测 120 行、混进 TK-TAE 的单）。
    #   ② 必须先用 JS `focus() + select()` 落到这个框上，再用 `insertText`；
    #      单纯 `dispatch_mouse` 点击**不会**把焦点落进框里 ⇒ 值写不进去（实际为 ''）。
    #      （与改订单 sku 那次是同一个坑）
    pg.dispatch_mouse(bx["x"], bx["y"]); time.sleep(0.3)
    pg.evaluate(r"""() => {
      const e = document.querySelector('input[name=shop_id]');
      e.focus(); e.select();
      return 'ok';
    }""")
    time.sleep(0.3)
    cdp.send("Input.insertText", {"text": code})
    time.sleep(0.7)
    now = pg.evaluate("() => { const e=document.querySelector('input[name=shop_id]'); return e?e.value:'none'; }")
    if str(now).strip() != code:
        # 再试一次：明显写不进去时，用 JS 直接 set + 派发事件（普通表单，非受控）
        pg.evaluate(r"""(v) => {
          const e = document.querySelector('input[name=shop_id]');
          const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          s.call(e, v);
          e.dispatchEvent(new Event('input', {bubbles: true}));
          e.dispatchEvent(new Event('change', {bubbles: true}));
          return e.value;
        }""", code)
        time.sleep(0.4)
        now = pg.evaluate("() => { const e=document.querySelector('input[name=shop_id]'); return e?e.value:'none'; }")
    log("   店铺代码填为 %r（目标 %s）" % (now, code))
    if str(now).strip() != code:
        raise RuntimeError("店铺代码没填进去（实际 %r）—— 拒绝在「全部店铺」上批量操作" % now)

    if cd:
        got = pg.evaluate(r"""() => {
          const s = document.querySelector('select[name=is_cd]');
          if (!s) return 'no-select';
          s.value = %s;
          s.dispatchEvent(new Event('change', {bubbles: true}));
          return JSON.stringify({v: s.value, t: s.options[s.selectedIndex].text});
        }""" % json.dumps(str(cd)))
        log("   订单类型: %s" % got)
        time.sleep(0.6)

    q = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('button,input[type=button],a')]
        .find(e => /^\s*查询\s*$/.test((e.innerText || e.value || '').trim()));
      if (!el) return '{}';
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }""") or "{}")
    if not q.get("x"):
        log("   ⚠ 找不到「查询」")
        return pg
    pg.dispatch_mouse(q["x"], q["y"])
    time.sleep(12)
    _kill_dialog(pg, log)
    return pg


def click_by_text(pg, text: str, log=print):
    """滚到并点击指定文字的按钮（返回是否点到）。坐标现取，避免滚动后失效。"""
    b = json.loads(pg.evaluate(r"""(txt) => {
      const vis = e => { const r = e.getBoundingClientRect();
        return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
      const t = e => (e.innerText || e.value || '').replace(/\s+/g, ' ').trim();
      const el = [...document.querySelectorAll('input[type=button],button,a,input[type=submit]')]
        .filter(vis).find(e => t(e) === txt || t(e).indexOf(txt) === 0);
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({t: t(el), x: Math.round(r.x + r.width/2),
                             y: Math.round(r.y + r.height/2),
                             on: el.getAttribute('onclick') || ''});
    }""", text) or "{}")
    if not b.get("x"):
        log("   ⚠ 找不到按钮「%s」" % text)
        return False
    log("   点「%s」onclick=%s" % (b.get("t"), b.get("on")))
    pg.dispatch_mouse(b["x"], b["y"])
    return True


def stage_confirm(pg, shop, dry_run=False, log=print) -> dict:
    """pb=1：店铺筛选 → 勾选 → select_types=确认订单 → 提交。"""
    log("== [%s] 阶段 1/2 待确认 → 审核" % shop)
    pg = _goto(pg, PB_CONFIRM, log)
    pg = ensure_page_size(pg, "300", log)
    log("   前: %s" % pg.evaluate(JS_SUM))
    pg = apply_filter(pg, shop, cd="", log=log)
    log("   筛后: %s" % pg.evaluate(JS_SUM))
    log("   勾选: %s" % pg.evaluate(JS_CHECK_ALL))
    time.sleep(1.2)

    if not dry_run:
        # ★ 2026-11-06：`_goto` 刚导航完时 `select_types` 可能还没渲染（实测报 JS Uncaught），
        #   先等它出现再操作，别把"页面没就绪"当成"没有这个控件"。
        for _w in range(15):
            if pg.evaluate("() => !!document.querySelector('select[name=select_types]')"):
                break
            time.sleep(1.5)
        got = pg.evaluate(r"""() => {
          const s = document.querySelector('select[name=select_types]');
          if (!s) return 'no-select';
          s.value = 'order_queren';
          s.dispatchEvent(new Event('change', {bubbles: true}));
          return JSON.stringify({v: s.value, t: s.options[s.selectedIndex].text});
        }""")
        log("   操作: %s" % got)
        time.sleep(0.6)
        if not click_by_text(pg, "提交", log):
            return {"ok": False, "why": "找不到提交按钮"}
        time.sleep(14)
        body = pg.evaluate("() => (document.body.innerText || '').replace(/\\s+/g,' ').slice(0,600)")
        ok_n = body.count("审核通过")
        log("   结果页「审核通过」出现 %d 次" % ok_n)
        return {"ok": ok_n > 0, "approved": ok_n, "url": pg.evaluate("() => location.href")[:100]}
    return {"ok": True, "dry_run": True}


def stage_track(pg, shop, dry_run=False, log=print) -> dict:
    """pb=2 + 未出跟踪号：勾选 → 批量获取单号。"""
    log("== [%s] 阶段 2/2 未出跟踪号 → 批量获取单号" % shop)
    pg = _goto(pg, PB_TRACK, log)
    pg = ensure_page_size(pg, "300", log)
    log("   前: %s" % pg.evaluate(JS_SUM))
    pg = apply_filter(pg, shop, cd="2", log=log)
    before = json.loads(pg.evaluate(JS_SUM) or "{}")
    log("   筛后: %s" % json.dumps(before, ensure_ascii=False))
    if not before.get("rows"):
        log("   ✅ 没有「未出跟踪号」的单 → 无需操作")
        return {"ok": True, "rows": 0}
    log("   勾选: %s" % pg.evaluate(JS_CHECK_ALL))
    time.sleep(1.2)

    if dry_run:
        click_by_text(pg, "批量获取单号", log) if False else None
        return {"ok": True, "dry_run": True, "rows": before.get("rows")}

    if not click_by_text(pg, "批量获取单号", log):
        return {"ok": False, "why": "找不到「批量获取单号」"}
    time.sleep(18)
    _kill_dialog(pg, log)
    after = json.loads(pg.evaluate(JS_SUM) or "{}")
    log("   获取后: %s" % json.dumps(after, ensure_ascii=False))
    got = int(after.get("withTrack") or 0)
    need = int(before.get("rows") or 0)
    return {"ok": got >= need, "need": need, "withTrack": got}


def main() -> int:
    global PORT
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="帕沃尔代发：待确认→审核→获取跟踪号")
    ap.add_argument("--shop", default="", help="BEL / BHY")
    ap.add_argument("--shops", default="", help="多店，逗号分隔")
    ap.add_argument("--stage", default="all", choices=["confirm", "track", "all"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()

    PORT = a.port
    shops = [x.strip() for x in a.shops.split(",") if x.strip()] or \
            ([a.shop.strip()] if a.shop.strip() else [])
    if not shops:
        ap.error("要给 --shop 或 --shops")

    pg = page()
    res = {}
    for s in shops:
        r = {}
        if a.stage in ("confirm", "all"):
            r["confirm"] = stage_confirm(pg, s, a.dry_run)
            pg = page()
        if a.stage in ("track", "all"):
            r["track"] = stage_track(pg, s, a.dry_run)
            pg = page()
        res[s] = r
        os.makedirs(PEEK, exist_ok=True)
        try:
            raw = pg.screenshot()
            with open(os.path.join(PEEK, "pw_ship_%s.png" % s), "wb") as f:
                f.write(raw)
        except Exception:  # noqa: BLE001
            pass

    print("\n[RESULT] " + json.dumps(res, ensure_ascii=False))
    ok = all(v.get("ok") for r in res.values() for v in r.values())
    return 0 if ok else 42


if __name__ == "__main__":
    raise SystemExit(main())
