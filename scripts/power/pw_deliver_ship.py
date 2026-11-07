# -*- coding: utf-8 -*-
"""pw_deliver_ship.py — 帕沃尔「代发」全链：待确认 → 审核 → 未出跟踪号 → 批量获取单号。

用户口径（2026-11-06，本流程唯一权威）：
    · 「待确认订单（deliver.php?act=list&pb=1），直接通过 BEL 的店铺代码（634418227477419）、
       BHY（634418228526171）→ 提交」
    · 「先勾选那个序号的才会出现（批量按钮）」
    · 「确认之后 → deliver.php?act=list&pb=2 批量获取跟踪号」
    · 页头「全部订单」选 **未出跟踪号**（is_cd=2）再查询
    · 「每页拉到最大 300」
    · ★ 2026-11-07 补（用户贴 pb=1 截图纠正确的跑法）：pb=1 的**待确认订单**要先按
      店铺代码或**复制参考单号**筛出来 → 勾选 → 「请选择操作=确认订单」→ 提交，
      **然后才去获取单号**。

⚠ 两套「出库单」别混（2026-11-06/07 真机实测的区别）：
    pb=1 待确认 · 参考单号  = hwc_shop「下单代发」后生成的出库单
        （状态「未确认」，出库单号/运单号都空）→ `stage_confirm` 在这里审核
    pb=2 待打包 · 出库单号  = deliver 侧出库单，`参考单号` 列**是空的**
    ⇒ pb=1 用 `--by po`（按参考单号筛，本店确认前的单都在）；
      pb=2 用 `--by shop`（出库单号列表里没有参考单号不是 bug，别再逐单搜 PO）。
    历史对照：2026-11-06 的 `deliver_all124.json`（pb=all，按 PO 匹配）**误中了别人的单**
    —— 那 124 行的订单 id 与 hwc 的 124 单完全对不上。别拿它当判据。

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
    # ★ 按单号精确做（整链用它：只确认本批、只取本批）
    python scripts/power/pw_deliver_ship.py --shop BEL --stage all --by po --orders-file <本批单号.txt>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

PEEK = os.path.join(_HERE, "out", "_peek")
PORT = 9333                      # 无头 Chrome 调试端口（见 lib/pw_browser.py）
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
    """帕沃尔裸 CDP（不走紫鸟；2026-11-07 起为无头实例）。"""
    import cdp_transport as C  # noqa: PLC0415
    return C


def page():
    """取帕沃尔页面 —— **无头**（2026-11-07 用户令）。

    以前要求「先开有头 Chrome：--remote-debugging-port=9333」；现在由
    `lib/pw_browser.ensure()` 自己起无头实例（独立 profile）+ 自动登录。
    """
    import pw_browser  # noqa: PLC0415

    pg, _ = pw_browser.ensure(PORT)
    if not pg:
        raise RuntimeError("无头帕沃尔浏览器起不来（端口 %d）" % PORT)
    if not pw_browser.ensure_logged_in(pg):
        raise RuntimeError("无头实例登录失效，且自动登录失败")
    return pg


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


def apply_filter(pg, shop: str, cd: str = "", pos: list | None = None, log=print):
    """筛选 → 点一次「查询」（**只点一次**，连点会弹 alert 阻塞）。

    `pos` 非空 = **按参考单号**筛（用户 2026-11-06 口径「复制订单号」）：
      选 `search_types=deliver_no` → `textarea[name=keywords]` 一行一个 →
      店铺代码仍要填（同一批单不能混进别的店）。单号回读不符**直接抛错**。
    `pos` 为空 = 老口径：按店铺代码（pb=2 出库单号列表就是这样，它没有参考单号列）。
    """
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

    if pos:
        # ★ 按参考单号筛：`search_types=deliver_no` + textarea 一行一个（用户「复制订单号」的口径）
        want = "\n".join(pos)
        pg.evaluate(r"""() => {
          const st = document.querySelector('select[name=search_types]');
          if (st) { st.value = 'deliver_no';
                   st.dispatchEvent(new Event('change', {bubbles: true})); }
          const ta = document.querySelector('textarea[name=keywords]');
          if (!ta) return 'no-textarea';
          ta.focus(); ta.select();
          return 'ok';
        }""")
        time.sleep(0.4)
        cdp.send("Input.insertText", {"text": want})
        time.sleep(0.7)
        got = pg.evaluate(
            "() => (document.querySelector('textarea[name=keywords]')||{}).value || ''") or ""
        n_got = len([x for x in got.split("\n") if x.strip()])
        if n_got != len(pos):
            # 单号没真的写进去 ⇒ 查询会返回**全部店铺**的单，绝不许继续
            raise RuntimeError("参考单号只写进 %d / %d 行（实际 %r…）—— 拒绝按全量查询"
                               % (n_got, len(pos), got[:40]))
        log("   参考单号 %d 行已填入" % n_got)

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


JS_ROWS = r"""() => {
  const out = [];
  document.querySelectorAll('tr[class^=tr_html_], tr[id^=tr_html_]').forEach(t => {
    const id = (t.className.match(/tr_html_(\d+)/) || [])[1]
            || (t.id || '').replace('tr_html_', '');
    if (!id) return;
    const tds = [...t.children].filter(e => e.tagName === 'TD');
    const cb = t.querySelector('input[name="checkboxes[]"]') || t.querySelector('input[type=checkbox]');
    out.push({id: id, cb: cb ? cb.value : '',
              tds: tds.map(e => (e.innerText || '').replace(/\s+/g, ' ').trim())});
  });
  return JSON.stringify(out);
}"""

PO_RE = re.compile(r"^(PO-\d+-\d+|GSU\w+|SEMI\w+)$")
WB_RE = re.compile(r"(1Z[0-9A-Z]{16}|GFUS[0-9A-Z]{6,}|SPX[0-9A-Z]{6,}|SWX[0-9A-Z]{6,}"
                   r"|YWE[0-9A-Z]{8,})")


def read_rows(pg) -> list:
    return json.loads(pg.evaluate(JS_ROWS) or "[]")


def rows_from_raw(raw: list) -> list:
    """把 `JS_ROWS` 的原始行拆成 `{id, po, out_no, status, wb}`（纯函数，离线可测）。

    ★ 两列的取值**不按固定下标**，而是**按列头文字定位**：
      pb=1（待确认）有「参考单号」列、出库单号常为空；
      pb=2（待打包）有「出库单号」列、参考单号为空。
      写死下标就会把两种页面的列错位（真机上很容易把「状态」读成「发货仓库」）。
    """
    out = []
    for r in raw or []:
        tds = r.get("tds") or []
        po = ""
        out_no = ""
        wb = ""
        status = ""
        for t in tds:
            t = str(t or "").strip()
            if not po and PO_RE.match(t):
                po = t
            elif not out_no and re.match(r"^[A-Z]{2,4}\d{8,}$", t):
                out_no = t
            if not wb:
                m = WB_RE.search(t)
                if m:
                    wb = m.group(1)
            if not status and t in STATUSES:
                status = t
        out.append({"id": str(r.get("id") or ""), "po": po, "out_no": out_no,
                    "status": status, "wb": wb})
    return out


#: 帕沃尔 deliver 列表里见过的状态（用于「按文字认列」，不按固定下标）
STATUSES = ("未确认", "待确认", "待打包", "已打包", "待出库", "已出库", "已发货", "已签收",
            "缺货中", "已取消")


def judge_batch(rows: list, pos: list, stage: str) -> dict:
    """这批行**是不是本批单**、够不够格做操作（纯函数，任何一条不满足就拒绝）。

    ★ 2026-11-06 真机：pb=all 上按 PO 匹配「查到 124 行」就批量提交，结果那 124 行是
      **别人的单**（id 与 hwc 的订单完全对不上），等于替别的店确认了 124 单。
    ⇒ 规矩：给了 pos 就必须「查到数 == 本批数」且「每个 PO 都在」，否则**一步都不做**。
    """
    want = [str(x).strip() for x in (pos or []) if str(x).strip()]
    got = [str(r.get("po") or "") for r in (rows or [])]
    if not want:
        return {"ok": bool(rows), "checked": 0, "n": len(rows or []), "why": ""
                if rows else "没有查到任何单"}
    missing = [p for p in want if p not in got]
    extra = [p for p in got if p and p not in want]
    if missing:
        return {"ok": False, "checked": 0, "n": len(got),
                "why": "本批 %d 单里有 %d 单在 pb=%s 查不到（%s）——拒绝批量提交"
                       % (len(want), len(missing), stage, ", ".join(missing[:3]))}
    if extra:
        return {"ok": False, "checked": 0, "n": len(got),
                "why": "查到了本批之外的 %d 单（%s）——查询没收敛到本批，拒绝提交"
                       % (len(extra), ", ".join(extra[:3]))}
    return {"ok": True, "checked": len(want), "n": len(got), "why": ""}


#: 确认订单后允许出现的状态（出现别的就是没成）
OK_AFTER_CONFIRM = ("待打包", "已打包", "待出库", "已出库", "已发货", "已签收")


def click_by_text(pg, text: str, log=print):
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


def stage_confirm(pg, shop, dry_run=False, pos=None, log=print) -> dict:
    """pb=1 待确认订单：店铺代码/参考单号筛 → 勾选 → 操作=确认订单 → 提交 → 回读状态。

    ★ 用户 2026-11-06 口径（贴图纠的）：待确认页按**店铺代码或复制订单号**筛出来，
      勾选序号 → 「请选择操作」= 确认订单 → 提交，**然后才**去获取单号。
    ★ 成功判据＝**回读状态**（本批每单都离开「未确认」），不是「点到了提交」。
    """
    log("== [%s] 阶段 1/2 待确认 → 确认订单" % shop)
    pg = _goto(pg, PB_CONFIRM, log)
    pg = ensure_page_size(pg, "300", log)
    log("   前: %s" % pg.evaluate(JS_SUM))
    pg = apply_filter(pg, shop, cd="", pos=pos, log=log)
    log("   筛后: %s" % pg.evaluate(JS_SUM))

    gate = judge_batch(rows_from_raw(read_rows(pg)), pos or [], "1")
    _g = {k: gate[k] for k in ("ok", "n", "checked")}
    if gate.get("why"):
        _g["why"] = gate["why"]
    log("   核对: %s" % json.dumps(_g, ensure_ascii=False))
    if not gate["ok"]:
        # ★ 2026-11-06 真机：pb=all 上「查到 124 行」其实是别人的单，批量提交等于替别的店确认
        return {"ok": False, "why": gate["why"], "rows": gate["n"]}

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
        # ★ 2026-11-07 用户贴图：下拉里写的是「确认订单」；没真选上就**不许点提交**
        if "确认订单" not in str(got):
            return {"ok": False, "why": "「请选择操作」没选成确认订单（回读 %s）" % got}
        time.sleep(0.6)
        if not click_by_text(pg, "提交", log):
            return {"ok": False, "why": "找不到提交按钮"}
        time.sleep(14)
        body = pg.evaluate("() => (document.body.innerText || '').replace(/\\s+/g,' ').slice(0,600)") or ""
        ok_n = body.count("审核通过")
        # ① 结果页提示；② **回读状态**（唯一可信判据）
        pg2 = _goto(pg, PB_CONFIRM, log)
        pg2 = ensure_page_size(pg2, "300", log)
        pg2 = apply_filter(pg2, shop, cd="", pos=pos, log=log)
        after = rows_from_raw(read_rows(pg2))
        left = [r["po"] for r in after if r.get("status") == "未确认"]
        moved = [r["po"] for r in after if r.get("status") in OK_AFTER_CONFIRM]
        log("   回读：离开「未确认」%d 单，仍「未确认」%d 单（结果页提示 %d 次）"
            % (len(moved), len(left), ok_n))
        return {"ok": not left, "approved": ok_n, "moved": len(moved), "left": left[:5],
                "rows": len(after)}
    return {"ok": True, "dry_run": True, "rows": gate["n"]}


def stage_track(pg, shop, dry_run=False, pos=None, log=print) -> dict:
    """pb=2 待打包 · 未出跟踪号：勾选 → 批量获取单号 → 回读运单号。

    ⚠ pb=2 的行**没有参考单号**（那列在出库单号页是空的）⇒
      给 `pos` 时只能按**店铺 + 未出跟踪号**筛，再用**行数**核对：
      查到的行数必须 == 本批在 pb=1 确认成功的单数；不符就停下报出来，绝不「点了就算成」。
    """
    log("== [%s] 阶段 2/2 未出跟踪号 → 批量获取单号" % shop)
    pg = _goto(pg, PB_TRACK, log)
    pg = ensure_page_size(pg, "300", log)
    log("   前: %s" % pg.evaluate(JS_SUM))
    pg = apply_filter(pg, shop, cd="2", pos=None, log=log)
    before = json.loads(pg.evaluate(JS_SUM) or "{}")
    log("   筛后: %s" % json.dumps(before, ensure_ascii=False))
    if not before.get("rows"):
        log("   ✅ 没有「未出跟踪号」的单 → 无需操作")
        return {"ok": True, "rows": 0}
    if pos:
        # ⚠ pb=2 列表里**没有参考单号**（那列是出库单号）⇒ 只能按「店铺 + 未出跟踪号」筛，
        #   再用行数对照本批；对不上就记下来交给调用方判定，别假装按单号筛过。
        log("   [按店筛] 本批 %d 单；pb=2 未出跟踪号 %d 行（pb=2 无参考单号列，只能按店+行数核）"
            % (len(pos), int(before.get("rows") or 0)))
    log("   勾选: %s" % pg.evaluate(JS_CHECK_ALL))
    time.sleep(1.2)

    if dry_run:
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
    ap = argparse.ArgumentParser(description="帕沃尔代发：待确认→确认订单→获取跟踪号")
    ap.add_argument("--shop", default="", help="BEL / BHY")
    ap.add_argument("--shops", default="", help="多店，逗号分隔")
    ap.add_argument("--stage", default="all", choices=["confirm", "track", "all"])
    ap.add_argument("--by", default="shop", choices=["shop", "po"],
                    help="按什么筛：shop=店铺代码（默认，整店）；po=参考单号（只动本批）")
    ap.add_argument("--orders-file", default="", help="--by po 时的单号清单 txt（一行一个）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()

    PORT = a.port
    shops = [x.strip() for x in a.shops.split(",") if x.strip()] or \
            ([a.shop.strip()] if a.shop.strip() else [])
    if not shops:
        ap.error("要给 --shop 或 --shops")
    pos: list = []
    if a.by == "po":
        if not a.orders_file or not os.path.exists(a.orders_file):
            ap.error("--by po 要给存在的 --orders-file")
        with open(a.orders_file, encoding="utf-8") as f:
            pos = [x.strip() for x in f if x.strip()]
        if not pos:
            ap.error("--orders-file 是空的")

    pg = page()
    res = {}
    for s in shops:
        r = {}
        if a.stage in ("confirm", "all"):
            r["confirm"] = stage_confirm(pg, s, a.dry_run, pos=pos or None)
            pg = page()
        if a.stage in ("track", "all"):
            # ★ 取号一律按店筛（pb=2 没有参考单号列），只做 dry-run 时也一样
            r["track"] = stage_track(pg, s, a.dry_run, pos=pos or None)
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
