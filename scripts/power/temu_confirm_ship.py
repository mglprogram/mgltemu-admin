# -*- coding: utf-8 -*-
"""temu_confirm_ship.py — TEMU「待发货 → 批量确认发货」**单一入口**（四层一气呵成）。

为什么要合成一个（2026-11-06 用户令「封装成 skill 并 pipeline 化」）：
    确认发货实际是**四层**交互，任何一层漏点都表现为"点了没反应、待发货数不变"：
        ① 扫出**真正可确认**的单（有运单号 + 行内有「去手动确认」）
        ② 按单号**精确勾选**（不能全选：勾到没面单的单会让「批量确认发货 N」变 0 并置灰）
        ③ 顶部「批量确认发货 N」→ 右侧滑出抽屉 → 抽屉底部「确认发货 N」
        ④ 居中二级弹窗「是否确认发货？」→「确认」
    原来拆成 4 个脚本，跨进程各扫一遍、状态对不齐（实测出现"勾选数 8→24→16→8→0 抖动"
    "抽屉关了但没提交"）。合并到同一进程后可以直接复用同一张页面与同一份勾选状态。

真机铁律（每条都在真机上吃过）：
    · 「批量确认发货 N」的 N 是**全库可确认数**，勾了无运单号的单会变 0 并置灰
    · 勾选是**切换式**：先读 `data-checked`，已勾的**不要**再点
    · 「批量确认发货」按钮在页面**顶部工具条**，列表长时会滚到视口外（实测 y=-19787），
      CDP 点视口外坐标**静默无效** ⇒ 点前必须 `scrollIntoView` 并检查 `inView`
    · 成功判据**只认「待发货」计数下降**；页面渲染有延迟，要点完再重新读一次

用法：
    python scripts/power/temu_confirm_ship.py --store BEL            # 只读扫描
    python scripts/power/temu_confirm_ship.py --store BEL --commit   # 真确认
    python scripts/power/temu_confirm_ship.py --store BEL --only PO-xxx,PO-yyy --commit
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
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF", "KinglamanSport": "TM-KinglamanSport"}


def canon_store(name: str) -> str:
    """店名大小写不敏感地还原成 `WINDOWS` 里的键（`kinglamansport` → `KinglamanSport`）。

    ★ 为什么必须有：KLS 是驼峰店名，注册表/命令行里大小写不一，`.upper()` 会让它
      到处 miss（窗口名、映射表、锁文件名都会跟着错）。
    """
    low = str(name or "").strip().lower()
    for k in WINDOWS:
        if k.lower() == low:
            return k
    return str(name or "").strip()

#: 运单号形态（UPS / GOFO / SpeedX / SwiftX / SpeedX 自提 / 燕文…），只用于「有没有号」的判定
TRACK_PAT = (r"1Z[0-9A-Z]{16}|GFUS\d{8,}|SPX[A-Z0-9]{6,}|SWX\d{6,}"
             r"|YWE\d{8,}|[A-Z]{2,6}\d{8,}")

JS_SCAN = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
        && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const trackRe = new RegExp("__TRACK_PAT__");
  const rows = [...document.querySelectorAll('tr')].filter(vis).map(txt)
    .filter(t => /PO-\d+-\d+/.test(t));
  const out = [];
  rows.forEach(t => {
    const po = (t.match(/PO-\d+-\d+/)||[''])[0];
    if (!po || out.some(o => o.po === po)) return;
    out.push({po, manual: /去手动确认/.test(t),
              track: (t.match(trackRe)||[''])[0]});
  });
  const body = document.body.innerText||'';
  const tabs = [...document.querySelectorAll('[role=tab],[class*=tab]')].filter(vis).map(txt)
    .filter(t => /^待发货\(\d+\)$/.test(t));
  const m = body.match(/已选订单[：:]\s*(\d+)/);
  const btns = {};
  [...document.querySelectorAll('button')].filter(vis).forEach(b => {
    const t = txt(b);
    if (/批量确认发货|合并下单/.test(t)) {
      btns[t] = {disabled: b.disabled===true
                 || (b.className||'').toString().indexOf('disabled')>=0};
    }
  });
  const pageSize = (body.match(/每页\s*(\d+)\s*条/)||[])[1]||'';
  return JSON.stringify({rows: out, pending: tabs[0]||'', selected: m?m[1]:'',
                         btns, pageSize});
}""".replace("__TRACK_PAT__", TRACK_PAT)

JS_FIND_BATCH_BTN = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
        && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const el = [...document.querySelectorAll('button')].filter(vis)
    .find(x => /批量确认发货/.test(txt(x)));
  if (!el) return '{}';
  // ★ 顶部工具条，列表长时会在视口外：先滚到中间再取坐标
  el.scrollIntoView({block: 'center'});
  const r = el.getBoundingClientRect();
  return JSON.stringify({t: txt(el), disabled: el.disabled===true,
                         x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                         inView: r.top>=0 && r.bottom<=window.innerHeight});
}"""

JS_FIND_DRAWER_BTN = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
        && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const el = [...document.querySelectorAll('button')].filter(vis)
    .find(x => /^确认发货\s*\d*$/.test(txt(x)));
  if (!el) return JSON.stringify({found:false});
  const r = el.getBoundingClientRect();
  return JSON.stringify({found:true, t: txt(el), disabled: el.disabled===true,
                         x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                         inView: r.top>=0 && r.bottom<=window.innerHeight});
}"""

JS_FIND_FINAL = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
        && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const body = document.body.innerText||'';
  if (!/是否确认发货/.test(body)) return JSON.stringify({need:false});
  const all = [...document.querySelectorAll('button')].filter(vis)
      .filter(x => txt(x) === '确认');
  if (!all.length) return JSON.stringify({need:true, found:false});
  const el = all[all.length - 1];          // 二级弹窗在抽屉之上，DOM 更靠后
  const r = el.getBoundingClientRect();
  return JSON.stringify({need:true, found:true, x: Math.round(r.x+r.width/2),
                         y: Math.round(r.y+r.height/2),
                         inView: r.top>=0 && r.bottom<=window.innerHeight});
}"""


# ---------------------------------------------------------------- 纯函数（可单测）

def pick_confirmable(rows: list) -> dict:
    """从扫描结果里挑出**可确认**的单（纯函数）。

    ★ 2026-11-20 口径修正（用户真机截图：`批量确认发货 11` 而 `已选订单: 0`）：
      旧判据要求「有运单号 **且** 行内有『去手动确认』」——实测 KLS 待发货 24 单里
      这样只挑出 **2** 单，而平台按钮明明写着 11。原因是「去手动确认」只在运单还没
      回填的行里出现，有运单号的行直接被平台认成可确认，那一格就没了。
      ⇒ 现在：**有运单号就算可确认**；「有去手动确认」只作为更强的加分项另列。
      ⚠ N 仍以平台按钮为准：脚本勾完会回读「批量确认发货 N 」，勾多勾少都如实报。
    """
    rows = rows or []
    manual = [r for r in rows if r.get("track") and r.get("manual")]
    track_only = [r for r in rows if r.get("track") and not r.get("manual")]
    no_track = [r for r in rows if not r.get("track")]
    with_track = manual + track_only
    return {"confirmable": with_track, "manual": manual,
            "track_only": track_only, "no_track": no_track,
            "n_confirmable": len(with_track)}


def parse_tab_count(tab: str) -> int:
    """`待发货(121)` → 121（纯函数）；读不到返回 -1。"""
    import re  # noqa: PLC0415
    m = re.search(r"\((\d+)\)", str(tab or ""))
    return int(m.group(1)) if m else -1


def judge_confirm(before: int, after: int, target: int) -> dict:
    """确认发货成没成的判据（纯函数）：**只认「待发货」计数按目标数下降**。

    · 前 < 0 或 后 < 0（读不到）→ unknown，不许当成功
    · 后 == 前 - target（或更少）→ ok
    · 其它 → partial（部分成功），如实报差多少
    """
    if before < 0 or after < 0:
        return {"verdict": "unknown", "ok": False,
                "why": "读不到「待发货」计数（before=%s after=%s）" % (before, after)}
    drop = before - after
    if drop >= target and target > 0:
        return {"verdict": "ok", "ok": True, "dropped": drop, "target": target}
    if drop <= 0:
        return {"verdict": "no_effect", "ok": False, "dropped": drop, "target": target,
                "why": "待发货数没变（%d → %d）⇒ 四层里有一层没点" % (before, after)}
    return {"verdict": "partial", "ok": False, "dropped": drop, "target": target,
            "why": "只减少 %d / 目标 %d" % (drop, target)}


# ---------------------------------------------------------------- 页面操作

def _zs():
    import ziniao_store  # noqa: PLC0415
    return ziniao_store


def _page(store: str, log=print):
    """拿一张订单**列表**页。

    ★ 2026-11-06 修（真机：明明开着订单页却报「没有订单列表页」）：
      旧过滤是 `"parentOrderSnList=P" not in url`，本意是排除**批量下单页**
      （一张页塞多个单号），但 `parentOrderSnList=PO-211-...`（**单**订单详情页）
      也含这串前缀 `parentOrderSnList=P` ⇒ 一起被排掉。BHY 窗口里当时只开了
      这种单订单页，于是脚本报「没有订单列表页」。

      正解：批量页的判据是「单号列表有多个」或参数名是下划线版
      （`parent_order_sn_list`），不是前缀。选中后**导航回干净列表页**再用。
    """
    import cdp_transport as C  # noqa: PLC0415
    zs = _zs()
    win = WINDOWS.get(store, store)
    port = zs.probe_port_by_window(win)
    if not port:
        raise RuntimeError("窗口 %s 未就绪（先开紫鸟店铺窗口）" % win)
    cat = [t for t in zs.list_page_targets(int(port))
           if "/mmsos/orders" in (t.get("url") or "")]

    def _is_batch(url: str) -> bool:
        """批量页（一张页装多单）—— 这种页不能当列表页用。"""
        u = url or ""
        if "parent_order_sn_list" in u or "parentOrderSnList" not in u:
            return False
        raw = u.split("parentOrderSnList=", 1)[1].split("&", 1)[0]
        return "," in raw or "%2C" in raw.lower()   # 多个单号 = 批量页

    clean = [t for t in cat if _is_batch(t.get("url") or "") is False
             and "parentOrderSnList=" not in (t.get("url") or "")]
    pick = (clean or [t for t in cat if not _is_batch(t.get("url") or "")])
    if not pick:
        raise RuntimeError("窗口 %s 里没有订单列表页" % win)
    pg = C.CdpPage(int(port), pick[0])
    # 干净列表页才直接用；否则导航回去（详情页/带筛选的页不能直接扫待发货）
    if "parentOrderSnList=" in (pick[0].get("url") or ""):
        log("   复用单订单页 → 导航回订单列表")
        pg.goto("https://agentseller-us.temu.com/mmsos/orders.html")
        time.sleep(6)
    return pg


def _goto_pending(pg, log=print):
    """切到「待发货」页签。"""
    d = json.loads(pg.evaluate(r"""() => {
      const vis = e => { const r = e.getBoundingClientRect();
        return e.getClientRects().length>0 && r.width>0 && r.height>0; };
      const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
      const tab = [...document.querySelectorAll('[role=tab],[class*=tab]')].filter(vis)
        .find(e => /^待发货\(\d+\)$/.test(txt(e)));
      if (!tab) return '{}';
      const r = tab.getBoundingClientRect();
      return JSON.stringify({t: txt(tab), x: Math.round(r.x+r.width/2),
                             y: Math.round(r.y+r.height/2)});
    }""") or "{}")
    if d.get("x"):
        pg.dispatch_mouse(d["x"], d["y"])
        time.sleep(6)
        log("   切到 %s" % d.get("t"))
    return pg


def _select_rows(pg, pos: list, log=print) -> dict:
    """按单号**精确勾选**（已勾的跳过，避免切换成未勾）。"""
    miss, already = [], 0
    for i, po in enumerate(pos, 1):
        d = json.loads(pg.evaluate(r"""(ord) => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
          const tr = [...document.querySelectorAll('tr')].filter(vis)
            .find(t => txt(t).indexOf(ord) >= 0);
          if (!tr) return '{}';
          tr.scrollIntoView({block: 'center'});
          const lab = tr.querySelector('label[data-testid=beast-core-checkbox]')
                    || tr.querySelector('label');
          if (!lab) return 'no-label';
          const box = lab.querySelector('[data-testid=beast-core-checkbox-checkIcon]') || lab;
          const r = box.getBoundingClientRect();
          return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                                 inView: r.top>=0 && r.bottom<=window.innerHeight,
                                 checked: lab.getAttribute('data-checked')});
        }""", po) or "{}")
        if not d.get("x") or not d.get("inView"):
            miss.append(po)
            continue
        if d.get("checked") == "true":
            already += 1
            continue
        pg.dispatch_mouse(d["x"], d["y"])
        time.sleep(0.35)
        if i % 10 == 0:
            log("      已勾 %d/%d …" % (i, len(pos)))
    return {"miss": miss, "already": already}


def _read_state(pg) -> dict:
    return json.loads(pg.evaluate(JS_SCAN) or "{}")


def _set_page_size_500(pg, log=print) -> int:
    """把待发货列表「每页」拉到 500（用户 2026-11-06：「改成每页 500 条记录即可」）。

    ★ 2026-11-06 真机：BHY 待发货有 **460** 条，默认每页 20 条 ⇒ 只扫第一屏就报
      「可确认 0」，而实际可确认的单散在后面几十页里。**不翻页 = 结论无效**。
      控件同导出页：`li.PGT_sizeChanger_123` → 选项 `li.cIL_item_123`（5/10/20/50/100/500）。
    """
    try:
        d = json.loads(pg.evaluate(r"""() => {
          const vis = e => { const r=e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const sc = document.querySelector('li.PGT_sizeChanger_123');
          if (!sc || !vis(sc)) return '{}';
          sc.scrollIntoView({block: 'center'});
          const r = sc.getBoundingClientRect();
          return JSON.stringify({x: Math.round(r.x+r.width/2),
                                 y: Math.round(r.y+r.height/2), t:(sc.innerText||'').trim()});
        }""") or "{}")
        if not d.get("x"):
            log("   ⚠ 没找到每页控件（li.PGT_sizeChanger_123）")
            return 0
        # ★ 2026-11-20 真机（用户截图里 5/10/20/50/100/500 明明都在）：这个控件
        #   **第一次点只是聚焦，第二次点才展开**。旧实现只点一次就去 찾 500 选项，
        #   所以永远报「下拉里没有 500 选项」⇒ 停在每页 20 条 ⇒ 第 2 页那几单勾不到。
        #   ⇒ 点完若没看到选项，再点一次（最多 3 次）。
        _OPT_JS = r"""() => {
          const vis = e => { const r=e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const it = [...document.querySelectorAll('li.cIL_item_123,li')].filter(vis)
            .find(e => (e.innerText||'').replace(/\s+/g,'').trim() === '500');
          if (!it) return '{}';
          const r = it.getBoundingClientRect();
          return JSON.stringify({x: Math.round(r.x+r.width/2),
                                 y: Math.round(r.y+r.height/2)});
        }"""
        o = {}
        for attempt in (1, 2, 3):
            o = json.loads(pg.evaluate(_OPT_JS) or "{}")
            if o.get("x"):
                break
            pg.dispatch_mouse(int(d["x"]), int(d["y"]))
            time.sleep(1.6)
            if attempt >= 2:
                log("   ⚠ 下拉里没有 500 选项（已点开 %d 次）" % attempt)
        if not o.get("x"):
            log("   ⚠ 下拉里没有 500 选项")
            return 0
        pg.dispatch_mouse(int(o["x"]), int(o["y"]))
        time.sleep(4)
        log("   每页拉到 500")
        return 500
    except Exception as ex:  # noqa: BLE001
        log("   ⚠ 设每页失败：%s" % str(ex)[:60])
        return 0


JS_SELECT_ALL = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
        && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  // 表头那个「全选」勾选框：在 thead 里，或带 data-testid=beast-core-checkbox 的第一颗
  const head = document.querySelector('thead');
  let lab = head ? (head.querySelector('label[data-testid=beast-core-checkbox]')
                   || head.querySelector('label')) : null;
  if (!lab) {
    const all = [...document.querySelectorAll('label[data-testid=beast-core-checkbox]')].filter(vis);
    lab = all[0] || null;
  }
  if (!lab) return JSON.stringify({found:false});
  lab.scrollIntoView({block: 'center'});
  const box = lab.querySelector('[data-testid=beast-core-checkbox-checkIcon]') || lab;
  const r = box.getBoundingClientRect();
  return JSON.stringify({found:true, checked: lab.getAttribute('data-checked'),
                         x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                         inView: r.top>=0 && r.bottom<=window.innerHeight});
}"""


def _select_all_on_page(pg, log=print) -> int:
    """点表头「全选」，返回「已选订单」数（0 = 没选上）。

    ★ 2026-11-20 用户真机口径（截图：`批量确认发货 11`、`已选订单: 0`）：
      「不是一个个点，我发现一个直接批量点击」。
      页面上那颗「批量确认发货 N」的 N 就是**当前可确认数**，先全选再点它即可，
      比逐行按单号勾选快得多，也不用先扫「有没有运单号」。

    ⚠ 全选的代价：勾到平台认为不可确认的单（没面单号）会让按钮**变 0 并置灰**。
      所以这里不做"勾完了就点"，而是**读回按钮状态**再决定：按钮 disabled 或 N=0
      ⇒ 立刻取消全选并回退到「按单号精确勾选」那条路，绝不硬点。
    """
    try:
        d = json.loads(pg.evaluate(JS_SELECT_ALL) or "{}")
    except Exception as ex:  # noqa: BLE001
        log("   ⚠ 全选探针失败：%s" % str(ex)[:60])
        return 0
    if not d.get("found"):
        log("   ⚠ 没找到表头全选框 → 退回逐行勾选")
        return 0
    if d.get("checked") != "true":
        if not d.get("x") or not d.get("inView"):
            log("   ⚠ 全选框不在视口 → 退回逐行勾选")
            return 0
        pg.dispatch_mouse(int(d["x"]), int(d["y"]))
        time.sleep(2.0)
    st = _read_state(pg)
    picked = int(st.get("selected") or 0)
    btn = ""
    bdisabled = False
    for t, info in (st.get("btns") or {}).items():
        if "批量确认发货" in t:
            btn, bdisabled = t, bool(info.get("disabled"))
    n_btn = 0
    m = re.search(r"(\d+)", btn or "")
    if m:
        n_btn = int(m.group(1))
    log("   全选后：已选 %d ｜ 按钮「%s」disabled=%s"
        % (picked, btn or "没找到", bdisabled))
    if not picked or bdisabled or n_btn == 0:
        log("   ⚠ 全选把按钮点灰了（勾到了没面单号的单）→ 取消全选，退回逐行勾选")
        try:
            _ = json.loads(pg.evaluate(JS_SELECT_ALL) or "{}")      # 再点一次 = 取消
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5)
        return 0
    return picked


MAX_PAGES = 20


def _goto_page(pg, n: int, log=print) -> bool:
    """翻到第 n 页（点分页器里的页码按钮）。返回是否点中。"""
    try:
        d = json.loads(pg.evaluate(r"""(want) => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
          const cand = [...document.querySelectorAll('li,button,a')].filter(vis)
            .filter(e => txt(e) === String(want))
            .filter(e => {
              const c = String(e.className || '') + String((e.parentElement||{}).className || '');
              return /PGT|pagination|page/i.test(c);
            });
          if (!cand.length) return '{}';
          const el = cand[cand.length - 1];
          el.scrollIntoView({block: 'center'});
          const r = el.getBoundingClientRect();
          return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                                 inView: r.top>=0 && r.bottom<=window.innerHeight});
        }""", n) or "{}")
    except Exception:  # noqa: BLE001
        return False
    if not d.get("x") or not d.get("inView"):
        return False
    pg.dispatch_mouse(int(d["x"]), int(d["y"]))
    time.sleep(4)
    log("   翻到第 %d 页" % n)
    return True


def click_batch_confirm(pg, log=print) -> dict:
    """**什么都不勾**，直接点「批量确认发货 N」→ 抽屉「确认发货 N」→ 二级「确认」。

    ★ 2026-11-20 用户口径（第三次纠正，最终版）：「你不需要一个个勾选」。
      N 是平台自己算的**可确认单数**，不勾任何行点它就是对的那 N 单；勾了反而会把
      N 拉低（实测：全勾 20 行 → 11 变 7；只勾「卖家发货」那档 → 11 变 3）。
      ⇒ 勾选这条路（`_select_rows` / `select_all_confirmable` / `--only`）现在只作为
        **显式指定单号**时才走（`--only`）；默认一律「零勾选直接点批量」。

    返回 `{ok, btn, drawer, final, why}`。
    """
    out = {"ok": False, "btn": "", "drawer": "", "final": "", "why": ""}
    st = _read_state(pg)
    sel = str(st.get("selected") or "0")
    if sel not in ("0", ""):
        log("   ⚠ 页面上已选 %s 行 —— 不勾才对，先刷新清掉再点（勾选会把 N 拉低）" % sel)
    b = json.loads(pg.evaluate(JS_FIND_BATCH_BTN) or "{}")
    out["btn"] = str(b.get("t") or "")
    log("[②] 直接点「%s」（不勾任何单）disabled=%s inView=%s"
        % (out["btn"] or "?", b.get("disabled"), b.get("inView")))
    if not b.get("x"):
        out["why"] = "页面上没有「批量确认发货」按钮（当前没有可确认的单）"
        return out
    if b.get("disabled"):
        out["why"] = "「%s」是灰的（平台认为当前没有可确认单）" % out["btn"]
        return out
    if not b.get("inView"):
        out["why"] = "按钮不在视口内（滚动后仍不可点）"
        return out
    pg.dispatch_mouse(b["x"], b["y"])
    time.sleep(5)
    db = json.loads(pg.evaluate(JS_FIND_DRAWER_BTN) or "{}")
    out["drawer"] = str(db.get("t") or "")
    if not db.get("found"):
        out["why"] = "点了批量确认发货，但抽屉没弹出来"
        return out
    log("[③] 抽屉「%s」" % out["drawer"])
    pg.dispatch_mouse(db["x"], db["y"])
    time.sleep(5)
    fd = json.loads(pg.evaluate(JS_FIND_FINAL) or "{}")
    if fd.get("need") and fd.get("found"):
        out["final"] = "确认"
        pg.dispatch_mouse(fd["x"], fd["y"])
        time.sleep(10)
    elif fd.get("need"):
        out["why"] = "二级弹窗『是否确认发货』出现了但找不到「确认」"
        return out
    out["ok"] = True
    return out


def select_all_confirmable(pg, log=print) -> dict:
    """**本页按按钮上的 N 勾满**：按钮写「批量确认发货 11」，就勾出 11 行。

    ★ 2026-11-20 用户口径（连说两次）：「不用一个个点击」「我发现一个直接批量点击」。
      平台那颗按钮的 N 就是**现在能确认的单数**，脚本只要把 N 行勾出来点它就完事。
      硬约束（真机实测）：**勾到没有面单的行会让 N 变小**（20 行全勾 → 11 变 7），
      所以只能用**平台自己的「发货信息」列有没有内容**作为勾选依据，不能按单号格式猜。

    分页：KLS 订单页的「每页」下拉里**没有 500**（实测 `下拉里没有 500 选项`），
    列表只有 20 行/页 ⇒ 必须**翻页累计**，否则永远只勾到第 1 页那几条。

    返回 `{picked, btn, pages, rows}`。
    """
    info = {"picked": 0, "btn": "", "pages": 0, "rows": 0}
    for pno in range(1, MAX_PAGES + 1):
        if pno > 1 and not _goto_page(pg, pno, log):
            break
        info["pages"] = pno
        n_before = info["picked"]
        r = json.loads(pg.evaluate(JS_PICK_BY_PLATFORM) or "{}")
        picks = r.get("picks") or []
        btn = ""
        try:
            st = _read_state(pg)
            btn = next((t for t in (st.get("btns") or {}) if "批量确认发货" in t), "")
        except Exception:  # noqa: BLE001
            pass
        want = int((re.search(r"(\d+)", btn or "") or [0, 0])[1] or 0)
        info["btn"] = btn or info["btn"]
        info["rows"] += len(picks)
        # ★ 按档位勾：先勾「有运单号」的，勾满 N 就停，不再碰只写了「卖家发货」的那些
        #   （实测全勾会把按钮从 11 拉到 3 ⇒ 平台少认，勾多等于白勾）。
        picks.sort(key=lambda x: int(x.get("tier") or 9))
        todo = picks
        if want:
            todo = picks[:max(0, want - info["picked"])]
        tiers = sorted({int(p.get("tier") or 9) for p in todo})
        log("   第 %d 页：候选 %d 行（档位 %s）｜ 按钮「%s」｜ 还差 %d"
            % (pno, len(picks), tiers or "-", btn or "?",
               max(0, (want - info["picked"]) if want else len(todo))))
        for item in todo:
            if _click_row_checkbox(pg, item["po"]):
                info["picked"] += 1
                time.sleep(0.3)
        if want and info["picked"] >= want:
            log("   已勾满 %d 单（等于按钮的 N）→ 不再翻页" % info["picked"])
            break
        if not picks and pno > 1 and info["picked"] == n_before:
            break
    return info


JS_PICK_BY_PLATFORM = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const head = document.querySelector('thead');
  const heads = head ? [...head.querySelectorAll('th')].map(txt) : [];
  let ci = heads.findIndex(h => /发货信息|运单|物流信息/.test(h));
  const trs = [...document.querySelectorAll('tbody tr')].filter(vis)
    .filter(t => /PO-\d+-\d+/.test(txt(t)));
  const out = [];
  trs.forEach(tr => {
    const tds = [...tr.querySelectorAll('td')];
    // 表头没命中就按位置找：发货信息列在「订单状态」之后、靠近末尾
    const cells = ci >= 0 && tds[ci] ? [tds[ci]] : tds;
    const txts = cells.map(txt);
    // 分档：① 有运单号 ② 有包裹号 ③ 只写了「卖家发货/在线下单」——
    // 勾到第③档会把按钮数字拉低（实测 11→3），所以**按档位累加、勾满 N 就停**。
    const reTrack = /1Z[0-9A-Z]{16}|GFUS\d{8,}|SPX[A-Z0-9]{6,}|SWX\d{6,}|YWE\d{8,}/;
    const rePkg = /PK-\d{8,}/;
    let tier = 0;
    if (txts.some(s => reTrack.test(s))) tier = 1;
    else if (txts.some(s => rePkg.test(s))) tier = 2;
    else if (txts.some(s => /卖家发货|在线下单|运单号/.test(s))) tier = 3;
    else return;
    const po = (txt(tr).match(/PO-\d+-\d+/)||[''])[0];
    out.push({po, tier, s: (txts.find(s=>reTrack.test(s)||rePkg.test(s)||/卖家发货|在线下单/.test(s))||'').slice(0, 50)});
  });
  return JSON.stringify({picks: out, headMatch: ci});
}"""


def _click_row_checkbox(pg, po: str) -> bool:
    """勾某一行的复选框（已勾则跳过）。返回是否点了/已勾。"""
    r = json.loads(pg.evaluate(r"""(ord) => {
      const vis = e => { const r = e.getBoundingClientRect();
        return e.getClientRects().length>0 && r.width>0 && r.height>0; };
      const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
      const tr = [...document.querySelectorAll('tbody tr')].filter(vis)
        .find(t => txt(t).indexOf(ord) >= 0);
      if (!tr) return '{}';
      tr.scrollIntoView({block: 'center'});
      const lab = tr.querySelector('label[data-testid=beast-core-checkbox]')
                || tr.querySelector('label');
      if (!lab) return '{}';
      if (lab.getAttribute('data-checked') === 'true') return JSON.stringify({already:true});
      const box = lab.querySelector('[data-testid=beast-core-checkbox-checkIcon]') || lab;
      const r = box.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x+r.width/2),
                             y: Math.round(r.y+r.height/2),
                             inView: r.top>=0 && r.bottom<=window.innerHeight});
    }""", po) or "{}")
    if r.get("already"):
        return True
    if not r.get("x") or not r.get("inView"):
        return False
    pg.dispatch_mouse(int(r["x"]), int(r["y"]))
    return True


def _select_by_platform(pg, log=print) -> int:
    """**只勾平台认账的那些行**：按「发货信息」列有内容来挑，不按单号格式猜。

    ★ 2026-11-20 用户真机（KLS 截图 `批量确认发货 11` / `已选订单: 0`）：
      · 旧判据「运单号正则 + 去手动确认」只挑出 **2** 单，按钮却写着 11；
      · 直接全选勾了 20 个，按钮反而掉到 **7** —— 勾到没面单号的行会把它拉低。
      ⇒ 正确做法介于两者之间：以**表格行里那一列到底有没有内容**为准。
        平台的「发货信息」列形如
          `卖家发货（在线下单） GOFO PK-3266439783383173296 运单号: GFUS…`
        没有面单的行这一列是空的/只写「待发货」。
    返回勾完后的「已选订单」数（0＝没勾上，交给调用方回退）。
    """
    try:
        d = json.loads(pg.evaluate(r"""() => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
          const head = document.querySelector('thead');
          const heads = head ? [...head.querySelectorAll('th')].map(txt) : [];
          const ci = heads.findIndex(h => /发货信息|运单|物流信息/.test(h));
          const trs = [...document.querySelectorAll('tbody tr')].filter(vis)
            .filter(t => /PO-\d+-\d+/.test(txt(t)));
          const picks = [];
          trs.forEach(tr => {
            const tds = [...tr.querySelectorAll('td')];
            let cell = ci >= 0 && tds[ci] ? txt(tds[ci]) : '';
            if (!cell) {
              // 表头没对上：退回「整行里出现过运单号形态」的粗判
              cell = /1Z[0-9A-Z]{16}|GFUS\d{8,}|SPX[A-Z0-9]{6,}|SWX\d{6,}/.test(txt(tr))
                     ? 'has-track' : '';
            }
            if (!cell || /^[-—\\/]?$/.test(cell)) return;
            const po = (txt(tr).match(/PO-\d+-\d+/)||[''])[0];
            picks.push({po, cell: cell.slice(0, 60)});
          });
          return JSON.stringify({headMatch: ci, picks});
        }""") or "{}")
    except Exception as ex:  # noqa: BLE001
        log("   ⚠ 平台口径探针失败：%s" % str(ex)[:60])
        return 0
    picks = d.get("picks") or []
    log("   平台口径：带发货信息的行 %d 条（表头命中 %s）"
        % (len(picks), d.get("headMatch")))
    if not picks:
        return 0
    miss = 0
    for i, p in enumerate(picks, 1):
        r = json.loads(pg.evaluate(r"""(ord) => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
          const tr = [...document.querySelectorAll('tbody tr')].filter(vis)
            .find(t => txt(t).indexOf(ord) >= 0);
          if (!tr) return '{}';
          tr.scrollIntoView({block: 'center'});
          const lab = tr.querySelector('label[data-testid=beast-core-checkbox]')
                    || tr.querySelector('label');
          if (!lab) return '{}';
          if (lab.getAttribute('data-checked') === 'true') return JSON.stringify({already:true});
          const box = lab.querySelector('[data-testid=beast-core-checkbox-checkIcon]') || lab;
          const r = box.getBoundingClientRect();
          return JSON.stringify({x: Math.round(r.x+r.width/2),
                                 y: Math.round(r.y+r.height/2),
                                 inView: r.top>=0 && r.bottom<=window.innerHeight});
        }""", p["po"]) or "{}")
        if r.get("already"):
            continue
        if not r.get("x") or not r.get("inView"):
            miss += 1
            continue
        pg.dispatch_mouse(int(r["x"]), int(r["y"]))
        time.sleep(0.35)
        if i % 10 == 0:
            log("      已勾 %d/%d …" % (i, len(picks)))
    st = _read_state(pg)
    n = int(st.get("selected") or 0)
    btn = next((t for t in (st.get("btns") or {}) if "批量确认发货" in t), "")
    log("   勾完：已选 %d ｜ 按钮「%s」%s" % (n, btn or "?", ("（%d 行没勾上）" % miss) if miss else ""))
    return n


def run(store: str, only=None, commit: bool = True, out_json: str = "", log=print,
        skip_done_check: bool = False) -> dict:
    """**唯一实现**：其他脚本/skill 要「确认发货」就调这个，别再各写一份。

    ★ 2026-11-20 用户令：「skill 的 confirm / overdue 两条都改成调用零勾选直点批量
      确认发货，一处改动三处（skill / 发货总链 / 独立脚本）统一」。
      ⇒ 下面这几个入口全部收敛到本函数：
        · `scripts/power/temu_confirm_ship.py`（命令行）
        · `run_temu_buylabel.py --action pending / overdue`（skill temu-buylabel）
        · `run_temu_fulfill.py` 的 `temu_confirm` 步
      行为（真机实测过）：**不勾任何单** → 点「批量确认发货 N」→ 抽屉「确认发货 N」
      → 二级「确认」，然后等 30s、刷新、按「待发货」计数下降判失败。
      勾选反而会把平台算好的 N 拉低（全勾 20 行 → 11→7），所以默认一律零勾选；
      只有显式给 `only`（指定单号）才走逐单勾选。

    返回 `{"ok","store","before","after","verdict","target","batched","why","out"}`。
    """
    store = canon_store(store)
    logs = []

    def _log(s):
        """记一行。调用方传的 `log` 若就是 print（命令行默认），别再 print 第二遍。"""
        logs.append(str(s))
        if log is not None and log is not _log:
            try:
                log(s)
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            print(s, flush=True)
        except Exception:  # noqa: BLE001
            pass

    res = {"ok": False, "store": store, "before": -1, "after": -1,
           "verdict": "", "target": 0, "batched": 0, "why": "", "out": out_json}
    pg = _page(store, _log)
    pg = _goto_pending(pg, _log)
    _set_page_size_500(pg, _log)

    st = _read_state(pg)
    before = parse_tab_count(st.get("pending"))
    btn = next((t for t in (st.get("btns") or {}) if "批量确认发货" in t), "")
    n_btn = int((re.search(r"(\d+)", btn or "") or [0, 0])[1] or 0)
    res["before"] = before
    _log("[①] %s 待发货 %s ｜ 平台可确认 N=%d"
         % (store, st.get("pending") or "-", n_btn))

    targets = [x.strip() for x in (only or []) if str(x).strip()]
    if targets:
        _log("[②] 指定 %d 单 → 逐单勾选（批量按钮会和勾选互斥，只能走这条）" % len(targets))
        _select_rows(pg, targets, _log)
        picked = int(_read_state(pg).get("selected") or 0)
        if picked != len(targets):
            res.update(why="勾选数 %d ≠ 目标 %d" % (picked, len(targets)))
            _log("[FAIL] " + res["why"])
            return res
    elif n_btn == 0 and not skip_done_check:
        res.update(ok=True, verdict="nothing_to_confirm", target=0)
        _log("[OK] 平台可确认数 0（都已经确认过 / 都还没面单号）")
        return res
    else:
        _log("[②] 不勾任何单 —— 直接点平台那颗「%s」" % (btn or "批量确认发货"))

    if not commit:
        res.update(ok=True, verdict="dry_run", target=len(targets) or n_btn)
        _log("[闸门] commit=False → 只扫描，不提交")
        _write_out(out_json, res, st, targets)
        return res

    r = click_batch_confirm(pg, _log)
    if not r.get("ok"):
        res.update(why=r.get("why") or "批量点不出抽屉")
        _log("[FAIL] " + res["why"])
        return res
    res["batched"] = len(targets) or n_btn

    # 判据：等 30s → 刷新 → 重切待发货 → 计数下降（2026-11-06 用户口径）
    _log("    等 30s 让平台计数刷新…")
    time.sleep(30)
    after = -1
    want = len(targets) or n_btn
    for rnd in range(1, 4):
        pg.evaluate("() => { location.reload(); return 1; }")
        time.sleep(12)
        pg = _goto_pending(pg, _log)
        st3 = _read_state(pg)
        after = parse_tab_count(st3.get("pending") or "")
        _log("    [第 %d 轮刷新] 待发货 %s" % (rnd, st3.get("pending") or "-"))
        if after >= 0 and before - after >= want:
            break
        if rnd < 3:
            time.sleep(15)
    j = judge_confirm(before, after, want)
    res.update(ok=bool(j.get("ok")), after=after, verdict=j.get("verdict") or "",
               target=want, why=j.get("why") or "")
    _log("[判] 待发货 %s → %s ⇒ %s %s" % (before, after, res["verdict"], res["why"]))
    _write_out(out_json, res, st, targets)
    try:
        os.makedirs(PEEK, exist_ok=True)
        with open(os.path.join(PEEK, "temu_confirm_ship_%s.png" % store), "wb") as f:
            f.write(pg.screenshot())
    except Exception:  # noqa: BLE001
        pass
    return res


def _write_out(out_json: str, res: dict, st: dict, targets: list) -> None:
    """落盘扫描/结果（`run_temu_fulfill` 的逐单复验要读 `confirmable`）。"""
    if not out_json:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
        g = pick_confirmable(st.get("rows") or [])
        payload = {"store": res.get("store"), "pending": st.get("pending"),
                   "confirmable": targets or [r["po"] for r in g["confirmable"]],
                   "verdict": res.get("verdict"), "batched": res.get("batched"),
                   "before": res.get("before"), "after": res.get("after"),
                   "track_only": [r["po"] for r in g["track_only"]],
                   "no_track": [r["po"] for r in g["no_track"]]}
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        res["out"] = out_json
    except OSError:
        pass


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="TEMU 待发货 → 批量确认发货（零勾选，单一实现）")
    ap.add_argument("--store", required=True, help="BEL / BHY / KinglamanSport")
    ap.add_argument("--only", default="",
                    help="只确认这些单号（逗号分隔）。缺省＝不勾任何单，直接点平台那颗批量按钮")
    ap.add_argument("--commit", action="store_true", help="真确认（改平台状态）")
    ap.add_argument("--out", default="", help="扫描结果 json 落盘路径")
    ap.add_argument("--all", action="store_true", help="兼容旧参，无效果（现在默认就是批量）")
    a = ap.parse_args()
    store = canon_store(a.store)
    only = [x.strip() for x in a.only.split(",") if x.strip()]
    out_json = a.out or os.path.join(_HERE, "out", time.strftime("%Y-%m-%d"),
                                     "temu_confirmable_%s.json" % store)
    res = run(store, only=only, commit=bool(a.commit), out_json=out_json)
    print("\\n[RESULT] " + json.dumps(
        {k: res.get(k) for k in ("store", "before", "after", "target",
                                 "batched", "verdict", "ok", "why")},
        ensure_ascii=False))
    return 0 if res.get("ok") else (0 if res.get("verdict") == "dry_run" else 42)


if __name__ == "__main__":
    raise SystemExit(main())
