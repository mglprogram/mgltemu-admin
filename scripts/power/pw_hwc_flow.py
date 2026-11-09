# -*- coding: utf-8 -*-
"""pw_hwc_flow.py — 帕沃尔「店铺 → 全部订单」页(hwc_shop)主流程驱动。

用户口径（2026-11-06，重要纠正）：
    操作**只在这一个页面**：
    https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1&uselastfilter=1
    · 搜索框 textarea[name=deliver_no]，支持多行（一行一个参考单号）
    · 「下单代发」= input[value=下单代发] onclick=edit_order('xiadan_df')
    · 批量改仓渠道 = 底部 select[name=select_types]=pl_edit_ck_chqd
      + select_ck(22=加州3仓 / 10=kansas仓) + select_zdkd(803=temu) + select_chqd(现取)

真机坑（实测）：
  1. 「查询」不能连点 → 页面 is_dianji 锁 + 原生 alert 会阻塞 JS。先 window.alert/confirm 打桩。
  2. 输入框写值要 focus()+select() 再 insertText，写完回读（本脚本会硬校验行数）。
  3. 勾选要 checked 属性 + property 双写（xiadan_df 读 attr('checked')）。
  4. 页面保留大量注释掉的旧列，解析前必须剥注释，否则「状态列」错位。
  5. xiadan_df 的返回 msg 不可信 → 真实结果只认「状态」列 + 运单号。

子命令：
    query    按单号清单精确查询 → 落 hwc_<tag>.json
    setwh    勾选当前列表 → 设仓库渠道 → 提交 → 回读
    xiadan   勾选当前列表 → 点「下单代发」→ 等全部完成 → 回读状态
    check    只看当前列表状态（不点任何动作）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, HERE)
from cdp_transport import CdpPage, http_targets  # noqa: E402

import pw_hwc_parse as P  # noqa: E402

PORT = 9333
URL = "https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1&uselastfilter=1"
OUT = os.path.join(HERE, "out", "2026-11-06")
PEEK = os.path.join(HERE, "out", "_peek")
CK = {"加州3仓": "22", "kansas仓": "10"}
ZDKD_TEMU = "803"
#: 「请选择操作」下拉 → 批量改仓渠道。★ 2026-11-07 用户贴图：
#:   下拉里是「**修改仓库渠道**」（不是「批量修改渠道」）；提交前出现
#:   仓库 / 指定快递 / 出货渠道 三个下拉：仓库真值 `加州3仓` / `kansas仓`，
#:   指定快递 `temu`（ZDKD_TEMU），出货渠道 `temu官方面单`（现取 id）。
SET_WH_ACTION = "pl_edit_ck_chqd"
#: 店铺 ID（帕沃尔里的「店铺ID」）。★ 2026-11-07 真机：这里曾写死 BHY 的
#: `634418228526171`，于是 **BEL 的 P2 单在 BHY 店铺下查永远是 0 行**
#: （查询「成功」但列表 0 条，看着像页面坏了）。改为按店传：BEL=…77419 / BHY=…26171。
SHOP_IDS = {"BEL": "634418227477419", "BHY": "634418228526171"}
SHOP_ID = SHOP_IDS["BEL"]


# ---------------------------------------------------------------- 页面基础
def page() -> CdpPage:
    """取帕沃尔页面（**有头**，2026-11-09 用户令：帕沃尔仓的发货改有头）。

    由 `lib/pw_browser.ensure()` 起实例（独立 profile）并自动登录；
    模式默认有头，`PW_HEADED=0` 才无头。
    """
    import pw_browser  # noqa: PLC0415

    pg, _ = pw_browser.ensure(PORT)
    if not pg:
        raise SystemExit("帕沃尔浏览器起不来（端口 %d）" % PORT)
    if not pw_browser.ensure_logged_in(pg):
        raise SystemExit("帕沃尔实例登录失效，且自动登录失败")
    return pg


def arm(pg: CdpPage) -> None:
    """打桩弹窗 + 关掉 layer 遮罩。

    真机坑：`xiadan_df` 跑完会 `layer.alert(waybill_str)` 弹一个遮罩层，
    它会盖住整页（`document.elementFromPoint(按钮中心)` 返回 DIV 而不是按钮），
    之后点「查询」全部落空 —— 必须 `layer.closeAll()` 才能继续（实测踩过）。
    """
    pg.evaluate("() => {"
                " window.alert = () => {}; window.confirm = () => true;"
                " try { if (typeof is_dianji !== 'undefined') is_dianji = false; } catch(e) {}"
                " try { if (window.layer && layer.closeAll) layer.closeAll(); } catch(e) {}"
                " document.querySelectorAll('.layui-layer').forEach(e => e.remove());"
                " return 1; }")


def ensure_page(pg: CdpPage) -> None:
    if "hwc_shop.php" not in (pg.url or "") or "shop_order" not in (pg.url or ""):
        pg.goto(URL)
        time.sleep(6)
    arm(pg)


JS_STATE = r"""() => {
  const out = [];
  document.querySelectorAll('tr[class^=tr_html_]').forEach(t => {
    const id = (t.className.match(/tr_html_(\d+)/) || [])[1];
    if (!id) return;
    const tds = [...t.children].filter(e => e.tagName === 'TD');
    const wbCell = t.querySelector('[class^=waybill_]');
    const idx = wbCell ? tds.indexOf(wbCell) : -1;
    const info = (tds[4] ? tds[4].innerText : '').replace(/\s+/g, ' ');
    // 非贪婪 + 用下一个标签做边界：仓库/渠道为空时贪婪版会把「备注」吃进渠道（实测踩过）
    const m = info.match(/发货仓库\s*:\s*(.*?)\s*出货渠道\s*:\s*(.*?)\s*备注/) || [];
    const skus = [...t.querySelectorAll('p')].map(p => (p.innerText || '').trim())
      .filter(x => x.indexOf('平台SKU') === 0)
      .map(x => (x.split(':')[1] || '').trim());
    out.push({
      id,
      po: ((t.querySelector('[class^=deliver_no_]') || {}).innerText || '').trim(),
      status: idx > 0 ? (tds[idx - 1].innerText || '').trim() : '',
      err: wbCell ? (wbCell.innerText || '').trim() : '',
      wh: m[1] || '', qd: m[2] || '',
      sku: skus.join(' / '),
      cb: (t.querySelector('input[name="checkboxes[]"]') || {}).checked,
    });
  });
  return JSON.stringify(out);
}"""


def read_state(pg: CdpPage) -> list:
    return json.loads(pg.evaluate(JS_STATE) or "[]")


WB_RE = P.WB_RE


def wb_of(s: str) -> str:
    m = WB_RE.search(s or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------- 搜索
def do_query(pg: CdpPage, pos: list, shop_id: str = SHOP_ID, wait: float = 25.0) -> list:
    """真实页面操作：清筛选 → textarea 写入多行 → 点「查询」→ 等列表刷新回来。"""
    ensure_page(pg)
    pg.evaluate(r"""(sid) => {
      const ta = document.querySelector('textarea[name=deliver_no]');
      const f = ta.closest('form');
      f.elements['deliver_no'].value = '';
      f.elements['shop_id'].value = sid;
      f.elements['status'].value = '';
      f.elements['ck_id'].value = '';
      f.elements['country'].value = '';
      f.elements['shop_types'].value = '';
      f.elements['start_date'].value = '2026-09-01';
      f.elements['end_date'].value = '2026-12-31';
      ta.focus(); ta.select();
      return 1;
    }""", shop_id)
    cdp = pg.cdp_session()
    text = "\n".join(pos)
    cdp.send("Input.insertText", {"text": text})
    time.sleep(0.7)
    got = pg.evaluate("() => document.querySelector('textarea[name=deliver_no]').value") or ""
    n_got = len([x for x in got.split("\n") if x.strip()])
    if n_got != len(pos):
        raise SystemExit("[FATAL] textarea 回读不符：期望 %d 行，页面 %d 行" % (len(pos), n_got))

    # 清掉「查询」的连点锁（is_dianji 在上一批查询里可能还挂着）
    pg.evaluate("() => { try { if (typeof is_dianji !== 'undefined') is_dianji = false; }"
                " catch(e) {} return 1; }")
    # ★ 用 hook 判「这次查询真的回来了」，不靠 DOM 指纹：
    #   列表可能和上一次完全一样（同筛选再查一次），指纹不变会被误判成"没刷新"（实测踩过）。
    pg.evaluate(r"""() => {
      window.__qdone = 0; window.__qres = null;
      if (typeof listTable !== 'undefined' && !listTable.__hooked) {
        const orig = listTable.listCallback;
        listTable.listCallback = function(res, txt) {
          window.__qdone = 1;
          window.__qres = (res && res.filter) ? res.filter.record_count : null;
          return orig.call(listTable, res, txt);
        };
        listTable.__hooked = 1;
      }
      return 1;
    }""")
    b = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('button,input[type=button]')]
        .find(e => /^\s*查询\s*$/.test((e.innerText || e.value || '').trim()));
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                             inView: r.top >= 0 && r.bottom <= innerHeight});
    }""") or "{}")
    if not b.get("x"):
        raise SystemExit("[FATAL] 找不到「查询」按钮")
    # 弹层会吃掉点击（elementFromPoint 变成遮罩），先清
    arm(pg)
    pg.dispatch_mouse(b["x"], b["y"])

    # 物理点击偶尔会落空（焦点/滚动竞态）→ 4 秒内没回调就退化成调同一个函数。
    # 「查询」按钮的 form action 就是 `javascript:searchOrder(1)`，所以两者等价。
    time.sleep(4)
    if not pg.evaluate("() => window.__qdone"):
        pg.evaluate("() => { try { searchOrder(1); return 1; } catch(e) { return 0; } }")

    want = set(pos)
    got_rows = []
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.5)
        pg.evaluate("() => { try { if (typeof is_dianji !== 'undefined') is_dianji = false; }"
                    " catch(e) {} return 1; }")
        if not pg.evaluate("() => window.__qdone"):
            continue
        time.sleep(0.5)                     # 让 innerHTML 换完再读
        rows = read_state(pg)
        pos_now = {x["po"] for x in rows}
        if rows and pos_now <= want:
            got_rows = rows
            break
    arm(pg)
    if not got_rows:
        raise SystemExit("[FATAL] 查询没刷新出结果（listCallback 未回调或列表不是本批单号）")
    if len(got_rows) < len(want):
        print("[WARN] 查到 %d / 期望 %d 单" % (len(got_rows), len(want)))
    return got_rows


# ---------------------------------------------------------------- 勾选 / 下拉
def set_checks(pg: CdpPage, ids: list) -> int:
    """勾选指定 id。

    用户口径（2026-11-06）：「你要全选序号，再去提交」—— 走页面自己的全选框
    `listTable.selectAll(this,"checkboxes")`，它只写 `checked` property；
    而 `edit_order('xiadan_df')` 读的是 `attr('checked')` ⇒ 必须再补一层
    setAttribute('checked','checked')，否则下单代发会认为"没选中"。
    """
    # ① 全选：点表头那个 checkbox（等同人工点「序号」全选）
    pg.evaluate(r"""() => {
      const hdr = document.querySelector('th input[type=checkbox]');
      hdr.scrollIntoView({block: 'center'});
      if (!hdr.checked) { hdr.click(); }
      if (typeof listTable !== 'undefined') { listTable.selectAll(hdr, 'checkboxes'); }
      return 1;
    }""")
    time.sleep(0.3)
    # ② 收窄到目标 id + 双写 attr（xiadan_df 依赖 attribute）
    js = r"""(ids) => {
  const want = new Set(ids.map(String));
  let n = 0;
  document.querySelectorAll('input.checkbox').forEach(cb => {
    const hit = want.has(String(cb.value));
    cb.checked = hit;
    if (hit) { cb.setAttribute('checked', 'checked'); n++; }
    else { cb.removeAttribute('checked'); }
  });
  return n;
}"""
    return int(pg.evaluate(js, [str(i) for i in ids]) or 0)


def goto_batch_zone(pg: CdpPage) -> None:
    pg.evaluate("""() => {
      const s = document.querySelector('[name=select_types]');
      s.scrollIntoView({block: 'center'});
      return 1;
    }""")


def set_selects(pg: CdpPage, wh: str) -> dict:
    ck = CK[wh]
    r = json.loads(pg.evaluate(r"""(a) => {
      const s = document.querySelector('[name=select_types]');
      const c = document.querySelector('[name=select_ck]');
      const z = document.querySelector('[name=select_zdkd]');
      const q = document.querySelector('[name=select_chqd]');
      if (!s || !c || !z || !q) return JSON.stringify({err: 'missing select'});
      // ★ 用户贴图：下拉文字是「修改仓库渠道」；按文字兜底找 value，防止站点改枚举
      const hit = [...s.options].find(o => (o.text || '').replace(/\s+/g, '') === '修改仓库渠道');
      s.value = hit ? hit.value : a.act;
      c.value = a.ck; z.value = a.zdkd;
      c.style.display = ''; z.style.display = ''; q.style.display = '';
      return JSON.stringify({st: s.value, st_text: (s.options[s.selectedIndex] || {}).text,
                             ck: c.value, zdkd: z.value});
    }""", {"act": SET_WH_ACTION, "ck": ck, "zdkd": ZDKD_TEMU}) or "{}")
    r["chqd"] = ""
    for _ in range(24):
        pg.evaluate("() => { try { get_paisongx(); } catch(e) { return 0; } return 1; }")
        time.sleep(0.7)
        opts = json.loads(pg.evaluate(
            "() => JSON.stringify([...document.querySelector('[name=select_chqd]').options]"
            ".map(o => ({v: o.value, t: o.text.trim()})))") or "[]")
        # ★ 用户贴图：出货渠道的选项文字是「temu官方面单」（无空格）
        hit = [o for o in opts if o["v"] and "官方面单" in o["t"]]
        if hit:
            r["chqd"] = hit[0]["v"]
            r["chqd_name"] = hit[0]["t"]
            break
    # ★ 必须把值写回下拉框本身：get_paisongx() 只负责填充 options，
    #   不写 value 时表单里 select_chqd=""，服务端会回「请选择出货渠道」（实测踩过）。
    if r.get("chqd"):
        r["set"] = pg.evaluate(
            "(v) => { const q = document.querySelector('[name=select_chqd]');"
            " q.value = v; q.style.display = ''; return q.value; }", r["chqd"])
    return r


def click_submit(pg: CdpPage) -> None:
    b = json.loads(pg.evaluate(r"""() => {
      const f = document.forms['listForm'];
      const el = [...f.querySelectorAll('input[type=submit],button')]
        .find(e => /^\s*提交\s*$/.test((e.value || e.innerText || '').trim()));
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }""") or "{}")
    if not b.get("x"):
        raise SystemExit("[FATAL] 找不到「提交」按钮")
    pg.dispatch_mouse(b["x"], b["y"])


# ---------------------------------------------------------------- 下单代发
def click_xiadan(pg: CdpPage) -> dict:
    """点「下单代发」按钮（edit_order('xiadan_df')）。confirm 已打桩为 true。"""
    b = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('input,a,button')]
        .find(e => /xiadan_df/.test(e.getAttribute('onclick') || ''));
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                             tag: el.tagName, val: (el.value || el.innerText || '').trim()});
    }""") or "{}")
    if not b.get("x"):
        raise SystemExit("[FATAL] 找不到「下单代发」按钮")
    before = pg.evaluate("() => document.querySelectorAll('tr[class^=tr_html_]').length")
    pg.dispatch_mouse(b["x"], b["y"])
    return {"btn": b, "rows": before}


def wait_xiadan(pg: CdpPage, n_expect: int, timeout: float = 1800.0,
                log=print) -> dict:
    """等下完。

    判据（实测）：xiadan_df 只写「下单错误提醒」列（class=waybill_<id>），
    「状态」列**不会**自动变 —— 必须重新查询才刷新（这和坑 6 一致：
    接口 msg 不可信，但错误提醒列是逐单真实结果）。
    完成信号：waybill_ing 归 0 且已回条数 = 选中条数；兜底看 waybill_sm==waybill_count。
    """
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        time.sleep(3)
        st = json.loads(pg.evaluate(r"""() => {
          const g = n => { try { return eval(n); } catch(e) { return null; } };
          const rows = [...document.querySelectorAll('tr[class^=tr_html_]')];
          const filled = rows.filter(t => {
            const c = t.querySelector('[class^=waybill_]');
            return c && (c.innerText || '').trim();
          }).length;
          const ing = rows.filter(t => {
            const c = t.querySelector('[class^=waybill_]');
            return c && /获取中/.test(c.innerText || '');
          }).length;
          const load = document.querySelector('.load_start');
          return JSON.stringify({ing: g('waybill_ing'), sm: g('waybill_sm'),
                                 cnt: g('waybill_count'), rows: rows.length,
                                 filled, busy: ing,
                                 bar: load ? getComputedStyle(load).display : 'none'});
        }""") or "{}")
        if st != last:
            log("   [等] t=%.0fs ing=%s 已回=%s/%s 行=%s 有结果=%s 获取中=%s"
                % (time.time() - t0, st.get("ing"), st.get("sm"), st.get("cnt"),
                   st.get("rows"), st.get("filled"), st.get("busy")))
            last = st
        done = (st.get("ing") in (0, None)) and st.get("busy") == 0 and st.get("filled")
        if done:
            return st
    return {"timeout": True}


def fix_one_sku(pg: CdpPage, oid: str, new_sku: str) -> dict:
    """在订单编辑页把 ck_sku[] 覆盖成新 Fnsku，提交并回读。

    真机坑（用户反复强调）：sku 框必须 **JS focus() + select() 再 insertText**，
    单纯点击焦点不会落进去，页面还会假报"成功"但值没变 ⇒ 提交后必须回读。
    """
    pg.goto("https://www.powerwarehouse.net/hwc_shop.php?act=edit_order&id=%s" % oid)
    time.sleep(5)
    arm(pg)
    prep = json.loads(pg.evaluate(r"""() => {
      const e = document.querySelector('input[name="ck_sku[]"]');
      if (!e) return JSON.stringify({err: 'no sku input'});
      e.scrollIntoView({block: 'center'});
      e.focus(); e.select();
      return JSON.stringify({old: e.value, focused: document.activeElement === e,
                             rows: document.querySelectorAll('input[name="ck_sku[]"]').length});
    }""") or "{}")
    if prep.get("err"):
        return {"ok": False, "why": prep["err"]}
    cdp = pg.cdp_session()
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA",
                                        "windowsVirtualKeyCode": 65,
                                        "nativeVirtualKeyCode": 65, "modifiers": 2})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                        "windowsVirtualKeyCode": 65,
                                        "nativeVirtualKeyCode": 65, "modifiers": 2})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace",
                                        "code": "Backspace", "windowsVirtualKeyCode": 8,
                                        "nativeVirtualKeyCode": 8})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace",
                                        "code": "Backspace", "windowsVirtualKeyCode": 8,
                                        "nativeVirtualKeyCode": 8})
    time.sleep(0.2)
    pg.evaluate("() => { const e = document.querySelector('input[name=\"ck_sku[]\"]');"
                " e.focus(); e.select(); return 1; }")
    cdp.send("Input.insertText", {"text": new_sku})
    time.sleep(0.6)
    filled = pg.evaluate("() => (document.querySelector('input[name=\"ck_sku[]\"]')||{}).value")
    if (filled or "").strip() != new_sku:
        return {"ok": False, "why": "写入失败：回读=%r 期望=%r" % (filled, new_sku),
                "old": prep.get("old")}
    open(os.path.join(PEEK, "fix_%s_filled.png" % oid), "wb").write(pg.screenshot())
    if not COMMIT:
        return {"ok": True, "dry": True, "old": prep.get("old"), "new": filled}

    # 提交（tijiao()）
    b = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('input[type=button],button,input[type=submit]')]
        .find(e => /^\s*提交\s*$/.test((e.value || e.innerText || '').trim()));
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                             onclick: el.getAttribute('onclick') || ''});
    }""") or "{}")
    if not b.get("x"):
        return {"ok": False, "why": "找不到提交按钮"}
    pg.dispatch_mouse(b["x"], b["y"])
    time.sleep(7)
    arm(pg)
    txt = pg.evaluate("() => (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,300)")
    open(os.path.join(PEEK, "fix_%s_after.png" % oid), "wb").write(pg.screenshot())
    # 回读：编辑页保留在原 id 时直接读 sku 框，否则回列表页查
    now = pg.evaluate("() => location.href")
    back = ""
    if "edit_order" in (now or ""):
        back = pg.evaluate("() => (document.querySelector('input[name=\"ck_sku[]\"]')||{}).value")
    return {"ok": (back or "").strip() == new_sku, "old": prep.get("old"),
            "new": back, "msg": txt[:120], "url": (now or "")[:80]}


COMMIT = False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["query", "setwh", "xiadan", "check", "fixsku"])
    ap.add_argument("--list", nargs="*", default=[])
    ap.add_argument("--wh", default="")
    ap.add_argument("--tag", default="q")
    ap.add_argument("--orders", nargs="*", default=[],
                    help="fixsku 用：id:Fnsku 或 po:Fnsku")
    ap.add_argument("--commit", action="store_true", help="fixsku 才会真提交")
    a = ap.parse_args()
    os.makedirs(PEEK, exist_ok=True)

    pos = []
    for f in a.list:
        with open(f, encoding="utf-8") as fh:
            pos += [x.strip() for x in fh if x.strip()]
    print("清单 %d 单" % len(pos))

    pg = page()
    ensure_page(pg)

    if a.cmd == "fixsku":
        global COMMIT
        COMMIT = bool(a.commit)
        # 支持两种写法：<订单id>:<Fnsku> / <参考单号>:<Fnsku>
        plan = []
        for spec in a.orders:
            left, _, fn = spec.rpartition(":")
            if not left or not fn:
                raise SystemExit("格式应为 id:Fnsku 或 参考单号:Fnsku，收到 %r" % spec)
            plan.append((left, fn))
        # 参考单号 → id（用订单列表页反查）
        need_po = [p for p, _ in plan if not p.isdigit()]
        po2id = {}
        if need_po:
            for x in do_query(pg, need_po):
                po2id[x["po"]] = x["id"]
        print("待改 SKU %d 单（commit=%s）" % (len(plan), COMMIT))
        from collections import defaultdict
        res = defaultdict(list)
        for left, fn in plan:
            oid = left if left.isdigit() else po2id.get(left, "")
            if not oid:
                res["找不到订单"].append(left)
                continue
            r = fix_one_sku(pg, oid, fn)
            print("  %-8s %-30s → %-16s %s" % (oid, r.get("old", ""), fn,
                                               "OK" if r.get("ok") else ("DRY" if r.get("dry")
                                                                         else r.get("why"))))
            res["OK" if r.get("ok") else (r.get("why") or "失败")].append("%s(%s)" % (left, oid))
        json.dump({k: v for k, v in res.items()},
                  open(os.path.join(OUT, "hwc_fixsku.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("汇总:", {k: len(v) for k, v in res.items()})
        return 0

    if a.cmd in ("query", "check"):
        rows = do_query(pg, pos) if a.cmd == "query" else read_state(pg)
        from collections import Counter
        print("列表 %d 行；状态 %s；仓库 %s" % (
            len(rows), dict(Counter(x["status"] for x in rows)),
            dict(Counter(x["wh"] or "(空)" for x in rows))))
        p = os.path.join(OUT, "hwc_%s.json" % a.tag)
        json.dump(rows, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        open(os.path.join(PEEK, "hwc_%s.png" % a.tag), "wb").write(pg.screenshot())
        print("落盘 %s" % p)
        return 0

    if a.cmd == "setwh":
        rows = read_state(pg)
        if not rows:
            raise SystemExit("[FATAL] 页面没有订单行，先跑 query")
        n = set_checks(pg, [x["id"] for x in rows])
        print("勾选 %d / %d 单" % (n, len(rows)))
        goto_batch_zone(pg)
        info = set_selects(pg, a.wh)
        print("下拉: %s" % info)
        if not info.get("chqd"):
            raise SystemExit("[FATAL] 没现取到「temu官方面单」渠道 id")
        print("渠道 id=%s (%s)" % (info["chqd"], info["chqd_name"]))
        open(os.path.join(PEEK, "hwc_before_submit.png"), "wb").write(pg.screenshot())
        click_submit(pg)
        time.sleep(8)
        arm(pg)
        print("提交后 url: %s" % pg.url[:110])
        rows2 = do_query(pg, pos)
        bad = [x["po"] for x in rows2 if a.wh not in (x["wh"] or "")]
        qbad = [x["po"] for x in rows2 if "官方面单" not in (x["qd"] or "")]
        print("回读：仓库正确 %d/%d，渠道正确 %d/%d"
              % (len(rows2) - len(bad), len(rows2), len(rows2) - len(qbad), len(rows2)))
        json.dump(rows2, open(os.path.join(OUT, "hwc_after_setwh_%s.json" % a.wh), "w",
                              encoding="utf-8"), ensure_ascii=False, indent=1)
        open(os.path.join(PEEK, "hwc_after_setwh.png"), "wb").write(pg.screenshot())
        if bad:
            print("[WARN] 仓库没改上: %s" % ", ".join(bad[:10]))
            return 42
        return 0

    if a.cmd == "xiadan":
        rows = read_state(pg)
        if not rows:
            raise SystemExit("[FATAL] 页面没有订单行，先跑 query")
        empty = [x["po"] for x in rows if not (x["wh"] or "")]
        if empty:
            print("[WARN] 有 %d 单发货仓库为空（%s…），先确认是否要设仓"
                  % (len(empty), ", ".join(empty[:3])))
        n = set_checks(pg, [x["id"] for x in rows])
        print("勾选 %d / %d 单" % (n, len(rows)))
        info = click_xiadan(pg)
        print("按钮: %s" % info)
        st = wait_xiadan(pg, n)
        print("等待结果: %s" % st)
        open(os.path.join(PEEK, "hwc_xiadan_done.png"), "wb").write(pg.screenshot())
        rows2 = do_query(pg, pos, wait=40)
        from collections import Counter
        print("回读状态 %s" % dict(Counter(x["status"] for x in rows2)))
        json.dump(rows2, open(os.path.join(OUT, "hwc_after_xiadan.json"), "w",
                              encoding="utf-8"), ensure_ascii=False, indent=1)
        ok = [x for x in rows2 if x["status"] == "已下单" and wb_of(x["err"])]
        print("已下单且有运单号: %d / %d" % (len(ok), len(rows2)))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
