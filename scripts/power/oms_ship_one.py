# -*- coding: utf-8 -*-
"""oms_ship_one.py — 在领星 OMS 把**已买好面单**的单子做完「分配仓库&物流 + 审核」。

用途（2026-11-06 用户口径）：TEMU 页已经买完面单（有运单号），
再到恒信领星 OMS 把这一单推进到「处理中/已发货」。

流程（全部真机实测过的判据，别改回旧写法）：
    ① 重置筛选 → ② 平台单号单值搜索（**放大镜 lx_combo_search**，不是多行 filter 图标）
    → ③ 表头 `th .vxe-cell--checkbox` 全选（vxe 固定列在 DOM 最后，th[0] 取不到）
    → ④ 「分配仓库&物流」是 split button，还要点菜单项「分配勾选数据」
    → ⑤ 发货仓库（el-select）→ 物流渠道（el-cascader，两级，选「上传物流面单」）
    → ⑥ 「确定并审核」→ ⑦ **去「处理中」验证**（见下）

★ 成败判据（用户 2026-11-06 原话，**不许改**）：
    「点击完确认并审核之后，你要去『处理中』查看这一单有没有转成处理中；
      **否则你就要去『待获取平台面单』查看，如果在这个页面下，那就是下单失败了**」
    ⇒ 三个终态：
        `处理中` 找到   → 成功（发货已受理）
        `处理中` 没有 + `待获取平台面单` 找到 → **失败**（下单/面单没成，要回头看面单）
        两处都没有      → 未知，如实报，不许当成功
    接口返回值、页签计数变化**都不算判据**。

写操作：默认只跑到 ⑥ 之前，加 `--commit` 才点「确定并审核」。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

PEEK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "_peek")

# ---------------------------------------------------------------- JS（全部 rect 判可见）

_JS_RESET = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const b = [...document.querySelectorAll('button')].filter(vis).find(x => txt(x) === '重置');
  if (b) b.click();
  return 'ok';
}"""

_JS_PO_INPUT = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const all = [...document.querySelectorAll('input.el-input__inner')].filter(vis);
  const lab = all.find(i => (i.value||'').trim() === '平台单号');
  if (!lab) return 'no-label';
  const lb = lab.getBoundingClientRect();
  const val = all.find(i => { const r = i.getBoundingClientRect();
    return Math.abs(r.y - lb.y) < 8 && r.x > lb.x + 10 && r.width > 150; });
  if (!val) return 'no-input';
  val.scrollIntoView({block:'center'});
  const r = val.getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                         inView: r.top>=0 && r.bottom<=window.innerHeight});
}"""

_JS_MAG = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const all = [...document.querySelectorAll('input.el-input__inner')].filter(vis);
  const lab = all.find(i => (i.value||'').trim() === '平台单号');
  if (!lab) return 'no-label';
  const lb = lab.getBoundingClientRect();
  const ic = [...document.querySelectorAll('i.lx_combo_search')].filter(vis)
      .filter(i => { const r = i.getBoundingClientRect();
        return Math.abs(r.y + r.height/2 - (lb.y + lb.height/2)) < 10 && r.x > lb.x; })
      .sort((a,b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x)[0];
  if (!ic) return 'no-icon';
  const r = ic.getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)});
}"""

_JS_ROWS = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const rows = [...document.querySelectorAll('.vxe-body--row')].filter(vis).map(txt).filter(t=>/PO-\d+/.test(t));
  const body = document.body.innerText||'';
  return JSON.stringify({total:(body.match(/共\s*(\d+)\s*条/)||[])[1]||'', rows: rows.length,
    detail: rows.slice(0,1).map(t=>t.slice(0,200))});
}"""

_JS_HDR_CB = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const cb = [...document.querySelectorAll('th .vxe-cell--checkbox')].filter(vis)[0];
  if (!cb) return 'no-cb';
  cb.scrollIntoView({block:'center'});
  const r = cb.getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                         inView: r.top>=0 && r.bottom<=window.innerHeight});
}"""

_JS_SEL = r"""() => {
  const m = (document.body.innerText||'').match(/已选\s*(\d+)\s*条数据/);
  return m ? m[1] : '';
}"""

_JS_ALLOC_BTN = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const b = [...document.querySelectorAll('button')].filter(vis).find(x => txt(x).indexOf('分配仓库') >= 0);
  if (!b) return 'no-btn';
  const r = b.getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                         disabled: b.disabled===true});
}"""

_JS_MENU_ITEM = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const it = [...document.querySelectorAll('li,.el-dropdown-menu__item,[class*=dropdown-item]')]
      .filter(vis).find(e => txt(e) === '分配勾选数据');
  if (!it) return 'no-item';
  const r = it.getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)});
}"""

_JS_DLG = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const dlg = [...document.querySelectorAll('.el-dialog,[class*=dialog]')].filter(vis)
      .filter(d => (d.innerText||'').indexOf('分配仓库') >= 0)[0];
  if (!dlg) return JSON.stringify({open:false});
  const sel = [...dlg.querySelectorAll('.el-select')].filter(vis)[0];
  const cs = [...dlg.querySelectorAll('.el-cascader')].filter(vis)[0];
  const inp = cs ? cs.querySelector('input.el-input__inner') : null;
  const info = (e) => e ? (() => { const r = e.getBoundingClientRect();
      return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
              cur: txt(e).slice(0,60), inView: r.top>=0 && r.bottom<=window.innerHeight}; })() : null;
  return JSON.stringify({open:true, wh: info(sel), ch: info(inp),
    channelCur: inp ? inp.value : '',
    buttons: [...dlg.querySelectorAll('button')].filter(vis).map(txt).filter(Boolean)});
}"""

_JS_WH_OPT = r"""() => {
  const want = %s;
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const lists = [...document.querySelectorAll('.el-select-dropdown')].filter(vis);
  const items = lists.flatMap(l => [...l.querySelectorAll('.el-select-dropdown__item')]).filter(vis);
  const hit = items.find(e => txt(e).indexOf(want) >= 0);
  if (!hit) return JSON.stringify({found:false, all: items.map(txt).slice(0,10)});
  const r = hit.getBoundingClientRect();
  return JSON.stringify({found:true, t: txt(hit),
    disabled: /is-disabled/.test((hit.className||'').toString()),
    x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)});
}"""

_JS_CH_LEAF = r"""() => {
  const want = %s;
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const menus = [...document.querySelectorAll('.el-cascader-menu')].filter(vis);
  for (let i = 1; i < menus.length; i++) {
    const nodes = [...menus[i].querySelectorAll('.el-cascader-node')].filter(vis);
    const hit = nodes.find(n => txt(n).indexOf(want) >= 0);
    if (hit) {
      const r = hit.getBoundingClientRect();
      return JSON.stringify({found:true, t: txt(hit).slice(0,60),
        disabled: /is-disabled/.test((hit.className||'').toString()),
        x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
        inView: r.top>=0 && r.bottom<=window.innerHeight});
    }
  }
  return JSON.stringify({found:false, menus: menus.length});
}"""

_JS_COMMIT = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const dlg = [...document.querySelectorAll('.el-dialog,[class*=dialog]')].filter(vis)
      .filter(d => (d.innerText||'').indexOf('分配仓库') >= 0)[0];
  if (!dlg) return 'no-dialog';
  const b = [...dlg.querySelectorAll('button')].filter(vis).find(x => txt(x) === '确定并审核');
  if (!b) return 'no-btn';
  const r = b.getBoundingClientRect();
  return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                         disabled: b.disabled===true});
}"""

_JS_AFTER = r"""() => {
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const body = document.body.innerText||'';
  const dlg = [...document.querySelectorAll('.el-dialog,[class*=dialog]')].filter(vis)
      .filter(d => (d.innerText||'').indexOf('分配仓库') >= 0)[0];
  return JSON.stringify({dialogOpen: !!dlg,
    toasts: [...document.querySelectorAll('.el-message,.el-message__content')].filter(vis)
              .map(txt).filter(t => t && t.length < 120).slice(0,4),
    tabs: {待处理: (body.match(/待处理\s*\((\d+)\)/)||[])[1]||'',
           处理中: (body.match(/处理中\s*\((\d+)\)/)||[])[1]||'',
           待获取平台面单: (body.match(/待获取平台面单\s*\((\d+)\)/)||[])[1]||''},
    rows: [...document.querySelectorAll('.vxe-body--row')].filter(vis).map(txt)
            .filter(t=>/PO-\\d+/.test(t)).slice(0,2)});
}"""

#: 切到某个状态页签（★ 真机：页签文本是「处理中 (135)」，数字前**有空格**，
#:   旧正则 `处理中\(\d+\)` 永远匹配不上 ⇒ no-tab；这里先去掉所有空白再比）。
_JS_GOTO_TAB = r"""() => {
  const want = %s;
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const norm = s => s.replace(/\s+/g, '');
  const tab = [...document.querySelectorAll('.el-tabs__item,[role=tab]')].filter(vis)
      .find(e => norm(txt(e)).indexOf(norm(want)) === 0);
  if (!tab) return 'no-tab';
  const r = tab.getBoundingClientRect();
  return JSON.stringify({t: txt(tab), x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)});
}"""

#: 在**当前页签**里找这一单（用户口径：确认并审核之后必须去「处理中」看到它）。
_JS_FIND_ROW = r"""() => {
  const want = %s;
  const vis = e => { const r=e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const body = document.body.innerText||'';
  const rows = [...document.querySelectorAll('.vxe-body--row')].filter(vis).map(txt);
  const hit = rows.filter(t => t.indexOf(want) >= 0);
  return JSON.stringify({total: (body.match(/共\s*(\d+)\s*条/)||[])[1]||'',
    rows: rows.length, hit: hit.length, rowText: (hit[0]||'').slice(0, 300)});
}"""


def main() -> int:
    ap = argparse.ArgumentParser(description="领星 OMS：把已买面单的单推进到审核（分配仓库&物流）")
    ap.add_argument("--po", required=True, help="平台单号，如 PO-211-10577625477751753")
    ap.add_argument("--window", default="TK-Savonire-YJF", help="紫鸟店铺窗口名（恒信 OMS 所在）")
    ap.add_argument("--warehouse", default="USW01", help="发货仓库（默认 USW01 = ZOO ⑥美西）")
    ap.add_argument("--channel", default="上传物流面单", help="物流渠道叶子文本")
    ap.add_argument("--commit", action="store_true", help="真点「确定并审核」（写操作）")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    import ziniao_store as zs  # noqa: PLC0415
    from cdp_transport import CdpPage  # noqa: PLC0415

    port = zs.probe_port_by_window(a.window)
    if not port:
        print("[FAIL] 窗口 %s 未就绪" % a.window, file=sys.stderr)
        return 42
    tgs = [t for t in zs.list_page_targets(port) if "oms.xlwms.com" in (t.get("url") or "")]
    if not tgs:
        print("[FAIL] 窗口里没有 OMS 页面", file=sys.stderr)
        return 42
    pg = CdpPage(port, tgs[0])
    cdp = pg.cdp_session()

    def shot(name):
        import base64  # noqa: PLC0415
        raw = pg.screenshot()
        os.makedirs(PEEK, exist_ok=True)
        p = os.path.join(PEEK, "%s.png" % name)
        with open(p, "wb") as f:
            f.write(base64.b64decode(raw) if isinstance(raw, str) else raw)
        return p

    # ⓪-0 先关掉**残留的「分配仓库&物流」弹窗**。
    #   ★ 2026-11-06 真机：不加这一步，上一次 `--dry-run` 留下的弹窗会把
    #     「重置 / 平台单号输入框 / 放大镜」全遮住 ⇒ 鼠标点不动 ⇒ 搜出全量 38300 条
    #     ⇒ 被「没搜到唯一一单」拦下（或更糟：勾到一堆别的单）。
    _close = pg.evaluate(r"""() => {
      const vis = e => { const r=e.getBoundingClientRect();
        return e.getClientRects().length>0 && r.width>0 && r.height>0
            && getComputedStyle(e).visibility!=='hidden'; };
      const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
      const dlgs = [...document.querySelectorAll('.el-dialog,[class*=dialog]')].filter(vis)
          .filter(d => (d.innerText||'').indexOf('分配仓库') >= 0);
      let n = 0;
      dlgs.forEach(d => {
        const b = [...d.querySelectorAll('button')].filter(vis)
            .find(x => txt(x) === '关闭' || txt(x) === '取消');
        if (b) { b.click(); n++; }
      });
      return String(n);
    }""")
    if str(_close).strip() not in ("0", "", "None"):
        print("[⓪] 关掉残留弹窗 %s 个" % _close)
        time.sleep(2.0)

    # ⓪ 先切到「待处理」（★ 2026-11-06 真机：上一次跑完会停在「处理中」页签，
    #    接着再跑就会在**错误的页签**里搜单 —— 实测搜到的是上一单、勾到 20 行，
    #    差点提交错单。所以每次都必须显式回「待处理」。）
    t0 = pg.evaluate(_JS_GOTO_TAB % json.dumps("\u5f85\u5904\u7406"))
    if str(t0).startswith("{"):
        tp0 = json.loads(t0)
        print("[⓪] 切「待处理」：%s" % tp0.get("t"))
        pg.dispatch_mouse(tp0["x"], tp0["y"])
        time.sleep(4.0)
    else:
        print("[WARN] 找不到「待处理」页签（%s），按当前页签继续" % t0)

    # ① 重置 + ② 搜单
    pg.evaluate(_JS_RESET)
    time.sleep(4.5)
    d = pg.evaluate(_JS_PO_INPUT)
    if not str(d).startswith("{"):
        print("[FAIL] 找不到平台单号输入框：%s" % d, file=sys.stderr)
        return 42
    pos = json.loads(d)
    pg.dispatch_mouse(pos["x"], pos["y"])
    time.sleep(0.7)
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA",
                                        "windowsVirtualKeyCode": 65, "nativeVirtualKeyCode": 65,
                                        "modifiers": 2})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                        "windowsVirtualKeyCode": 65, "nativeVirtualKeyCode": 65,
                                        "modifiers": 2})
    time.sleep(0.3)
    cdp.send("Input.insertText", {"text": a.po})
    time.sleep(0.9)
    m = pg.evaluate(_JS_MAG)
    if not str(m).startswith("{"):
        print("[FAIL] 找不到放大镜：%s" % m, file=sys.stderr)
        return 42
    mp = json.loads(m)
    pg.dispatch_mouse(mp["x"], mp["y"])
    time.sleep(6.0)
    r = json.loads(pg.evaluate(_JS_ROWS) or "{}")
    print("[①] 搜索：total=%s rows=%s" % (r.get("total"), r.get("rows")))
    print("     %s" % (r.get("detail") or [""])[0][:160])
    if r.get("rows") != 1:
        print("[FAIL] 没搜到唯一一单，停", file=sys.stderr)
        return 42

    # ③ 勾选
    h = pg.evaluate(_JS_HDR_CB)
    if not str(h).startswith("{"):
        print("[FAIL] 找不到表头勾选框：%s" % h, file=sys.stderr)
        return 42
    hp = json.loads(h)
    pg.dispatch_mouse(hp["x"], hp["y"])
    time.sleep(1.8)
    sel = pg.evaluate(_JS_SEL)
    print("[②] 已选 = %s" % sel)
    if str(sel).strip() != "1":
        print("[FAIL] 勾选数不是 1，停（避免空提交）", file=sys.stderr)
        return 42

    # ④ 分配仓库&物流 → 分配勾选数据
    b = pg.evaluate(_JS_ALLOC_BTN)
    if not str(b).startswith("{"):
        print("[FAIL] 找不到分配按钮：%s" % b, file=sys.stderr)
        return 42
    bp = json.loads(b)
    if bp.get("disabled"):
        print("[FAIL] 分配按钮 disabled", file=sys.stderr)
        return 42
    pg.dispatch_mouse(bp["x"], bp["y"])
    time.sleep(1.5)
    it = pg.evaluate(_JS_MENU_ITEM)
    if not str(it).startswith("{"):
        print("[FAIL] 菜单项没出来：%s" % it, file=sys.stderr)
        return 42
    ip = json.loads(it)
    pg.dispatch_mouse(ip["x"], ip["y"])
    time.sleep(3.0)
    dlg = json.loads(pg.evaluate(_JS_DLG) or "{}")
    print("[③] 弹窗：%s" % json.dumps(dlg, ensure_ascii=False)[:200])
    if not dlg.get("open"):
        print("[FAIL] 分配弹窗没出现", file=sys.stderr)
        return 42

    # ⑤-a 发货仓库
    if not str((dlg.get("wh") or {}).get("cur") or "").startswith("ZOO"):
        o = pg.evaluate(_JS_WH_OPT % json.dumps(a.warehouse))
        if not str(o).startswith("{"):
            print("[FAIL] 仓库选项探测失败：%s" % o, file=sys.stderr)
            return 42
        op = json.loads(o)
        if not (op.get("all") or []):
            pg.dispatch_mouse(dlg["wh"]["x"], dlg["wh"]["y"])
            time.sleep(2.0)
            op = json.loads(pg.evaluate(_JS_WH_OPT % json.dumps(a.warehouse)) or "{}")
        print("[④] 仓库选项：%s" % json.dumps(op, ensure_ascii=False)[:160])
        if not op.get("found") or op.get("disabled"):
            print("[FAIL] 找不到可用仓库 %s" % a.warehouse, file=sys.stderr)
            return 42
        pg.dispatch_mouse(op["x"], op["y"])
        time.sleep(1.8)

    # ⑤-b 物流渠道（两级级联）
    dlg2 = json.loads(pg.evaluate(_JS_DLG) or "{}")
    if a.channel not in str(dlg2.get("channelCur") or ""):
        leaf = pg.evaluate(_JS_CH_LEAF % json.dumps(a.channel))
        if not str(leaf).startswith("{"):
            print("[FAIL] 渠道叶子探测失败：%s" % leaf, file=sys.stderr)
            return 42
        lp = json.loads(leaf)
        if not lp.get("found"):
            pg.dispatch_mouse(dlg2["ch"]["x"], dlg2["ch"]["y"])
            time.sleep(2.2)
            lp = json.loads(pg.evaluate(_JS_CH_LEAF % json.dumps(a.channel)) or "{}")
        print("[⑤] 渠道叶子：%s" % json.dumps(lp, ensure_ascii=False)[:160])
        if not lp.get("found") or lp.get("disabled"):
            print("[FAIL] 找不到可用渠道 %s" % a.channel, file=sys.stderr)
            return 42
        pg.dispatch_mouse(lp["x"], lp["y"])
        time.sleep(2.0)

    dlg3 = json.loads(pg.evaluate(_JS_DLG) or "{}")
    print("[⑥] 提交前：仓库=%s 渠道=%s"
          % ((dlg3.get("wh") or {}).get("cur"), dlg3.get("channelCur")))
    print("     图：%s" % shot("oms_ready_commit"))
    if not a.commit:
        print("[停] 未给 --commit，停在提交前（未点确定并审核）")
        return 0

    # ⑦ 确定并审核
    cb = pg.evaluate(_JS_COMMIT)
    if not str(cb).startswith("{"):
        print("[FAIL] 找不到确定并审核：%s" % cb, file=sys.stderr)
        return 42
    cp = json.loads(cb)
    if cp.get("disabled"):
        print("[FAIL] 确定并审核 disabled", file=sys.stderr)
        return 42
    pg.dispatch_mouse(cp["x"], cp["y"])
    time.sleep(6.0)
    after = pg.evaluate(_JS_AFTER)
    print("[⑦] 提交后：%s" % after)
    print("     图：%s" % shot("oms_after_commit"))

    # ⑧ ★ 用户口径（2026-11-06，判据不许改）：
    #    「点击完确认并审核之后，你要去『处理中』查看这一单有没有转成处理中；
    #      否则你就要去『待获取平台面单』查看，如果在这个页面下，那就是下单失败了」
    #    提交接口返回、页签计数都**不算**判据。
    def _goto_and_find(tab_name: str):
        """切到 tab_name 页签 → 搜这一单 → 返回 (命中数, 行文本, 是否找到页签)。"""
        t = pg.evaluate(_JS_GOTO_TAB % json.dumps(tab_name))
        if not str(t).startswith("{"):
            print("      ⚠ 找不到页签「%s」（%s）" % (tab_name, t))
            return 0, "", False
        tp = json.loads(t)
        pg.dispatch_mouse(tp["x"], tp["y"])
        time.sleep(5.0)
        ip2 = pg.evaluate(_JS_PO_INPUT)
        if str(ip2).startswith("{"):
            i2 = json.loads(ip2)
            pg.dispatch_mouse(i2["x"], i2["y"])
            time.sleep(0.7)
            cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA",
                                                "windowsVirtualKeyCode": 65,
                                                "nativeVirtualKeyCode": 65, "modifiers": 2})
            cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                                "windowsVirtualKeyCode": 65,
                                                "nativeVirtualKeyCode": 65, "modifiers": 2})
            time.sleep(0.3)
            cdp.send("Input.insertText", {"text": a.po})
            time.sleep(0.9)
            m2 = pg.evaluate(_JS_MAG)
            if str(m2).startswith("{"):
                mp2 = json.loads(m2)
                pg.dispatch_mouse(mp2["x"], mp2["y"])
                time.sleep(6.0)
        f = json.loads(pg.evaluate(_JS_FIND_ROW % json.dumps(a.po)) or "{}")
        return int(f.get("hit") or 0), str(f.get("rowText") or ""), True

    hit_doing, row_doing, ok_tab = _goto_and_find("\u5904\u7406\u4e2d")
    print("[⑧] 「处理中」查单：hit=%s" % hit_doing)
    if row_doing:
        print("     %s" % row_doing[:220])
    print("     图：%s" % shot("oms_in_progress"))

    if hit_doing:
        print("[OK] 已在「处理中」：%s" % a.po)
        print("[OK] %s" % json.dumps({"po": a.po, "committed": True, "verdict": "in_progress"},
                                     ensure_ascii=False))
        return 0

    # 「处理中」没有 → 去「待获取平台面单」看，在那儿 = **下单失败**
    hit_pending, row_pending, ok_tab2 = _goto_and_find("\u5f85\u83b7\u53d6\u5e73\u53f0\u9762\u5355")
    print("[⑧] 「待获取平台面单」查单：hit=%s" % hit_pending)
    if row_pending:
        print("     %s" % row_pending[:220])
    print("     图：%s" % shot("oms_pending_label"))

    if hit_pending:
        print("[FAIL] 这一单落在「待获取平台面单」⇒ **下单失败**（面单没成）: %s" % a.po,
              file=sys.stderr)
        print("[FAIL] %s" % json.dumps({"po": a.po, "committed": True, "verdict": "place_failed"},
                                       ensure_ascii=False))
        return 42

    print("[FAIL] 「处理中」与「待获取平台面单」都搜不到这一单 ⇒ 状态未知，不许当成功: %s"
          % a.po, file=sys.stderr)
    print("[FAIL] %s" % json.dumps({"po": a.po, "committed": True, "verdict": "unknown",
                                    "tabs_found": {"处理中": ok_tab, "待获取平台面单": ok_tab2}},
                                   ensure_ascii=False))
    return 42


if __name__ == "__main__":
    raise SystemExit(main())
