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
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}

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

    可确认 = 有运单号 且 行内有「去手动确认」。
    ⚠ 只有运单号不够：还要平台认为能确认（否则勾了会让按钮变 0）。
    """
    rows = rows or []
    manual = [r for r in rows if r.get("track") and r.get("manual")]
    track_only = [r for r in rows if r.get("track") and not r.get("manual")]
    no_track = [r for r in rows if not r.get("track")]
    return {"confirmable": manual, "track_only": track_only, "no_track": no_track,
            "n_confirmable": len(manual)}


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
        if "500" in str(d.get("t") or ""):
            return 500
        pg.dispatch_mouse(int(d["x"]), int(d["y"]))
        time.sleep(1.8)
        o = json.loads(pg.evaluate(r"""() => {
          const vis = e => { const r=e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          // ★ 2026-11-06 修（BEL 报「下拉里没有 500 选项」）：
          //   订单页的选项文本是**纯数字** `500`（不是导出页那种 `500条/页`），
          //   旧写法只认 `=== '500条/页'` ⇒ 永远匹配不上。
          const txt = e => (e.innerText||'').replace(/\s+/g,'').trim();
          const it = [...document.querySelectorAll('li.cIL_item_123,li')].filter(vis)
            .find(e => txt(e) === '500');
          if (!it) return '{}';
          const r = it.getBoundingClientRect();
          return JSON.stringify({x: Math.round(r.x+r.width/2),
                                 y: Math.round(r.y+r.height/2)});
        }""") or "{}")
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


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="TEMU 待发货 → 批量确认发货（单一入口）")
    ap.add_argument("--store", required=True, help="BEL / BHY")
    ap.add_argument("--only", default="", help="只确认这些单号（逗号分隔；缺省＝全部可确认单）")
    ap.add_argument("--commit", action="store_true", help="真确认（改平台状态）")
    ap.add_argument("--out", default="", help="扫描结果 json 落盘路径")
    a = ap.parse_args()

    logs = []

    def log(s):
        print(s, flush=True)
        logs.append(str(s))

    pg = _page(a.store, log)
    pg = _goto_pending(pg, log)
    _set_page_size_500(pg, log)

    # ① 扫描
    st = _read_state(pg)
    log("[①] %s 待发货 %s ｜ 本页 %d 行 ｜ 每页 %s"
        % (a.store, st.get("pending") or "-", len(st.get("rows") or []),
           st.get("pageSize") or "-"))
    total = int((re.search(r"\((\d+)\)", str(st.get("pending") or "")) or [0, 0])[1] or 0)
    if total and len(st.get("rows") or []) < total:
        log("   ⚠ **只扫到 %d / %d 行**（每页没拉到 500）⇒ 结论可能漏单"
            % (len(st.get("rows") or []), total))

    g = pick_confirmable(st.get("rows") or [])
    log("    可确认 %d / 有号无『去手动确认』%d / 无运单号 %d"
        % (g["n_confirmable"], len(g["track_only"]), len(g["no_track"])))
    for r in g["confirmable"][:10]:
        log("      %-26s %s" % (r["po"], r["track"]))
    if g["n_confirmable"] > 10:
        log("      …共 %d 单" % g["n_confirmable"])

    target = [r["po"] for r in g["confirmable"]]
    if a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
        bad = [w for w in want if w not in target]
        if bad:
            log("[FAIL] 这些单不在「可确认」里（可能还没面单号）：%s" % ", ".join(bad[:6]))
            return 42
        target = want
    out_json = a.out or os.path.join(_HERE, "out", time.strftime("%Y-%m-%d"),
                                     "temu_confirmable_%s.json" % a.store)
    os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"store": a.store, "pending": st.get("pending"),
                   "confirmable": target,
                   "track_only": [r["po"] for r in g["track_only"]],
                   "no_track": [r["po"] for r in g["no_track"]]},
                  f, ensure_ascii=False, indent=1)
    log("    扫描结果已存 %s" % out_json)

    if not target:
        log("[OK] 没有可确认的单（都已经确认过 / 都还没面单号）")
        print("\n[RESULT] " + json.dumps({"store": a.store, "verdict": "nothing_to_confirm",
                                          "ok": True}, ensure_ascii=False))
        return 0
    if not a.commit:
        log("[闸门] 未给 --commit → 只扫描，不确认")
        print("\n[RESULT] " + json.dumps({"store": a.store, "verdict": "dry_run",
                                          "confirmable": len(target), "ok": True},
                                         ensure_ascii=False))
        return 0

    before = parse_tab_count(st.get("pending"))

    # ② 精确勾选
    sel = _select_rows(pg, target, log)
    st2 = _read_state(pg)
    picked = int(st2.get("selected") or 0)
    log("[②] 已选 %d（目标 %d）" % (picked, len(target)))
    if sel["miss"]:
        log("    ⚠ 这些行没找到/不在视口：%s" % ", ".join(sel["miss"][:5]))
    if picked != len(target):
        log("[FAIL] 勾选数 %d ≠ 目标 %d ⇒ 停，避免只确认一部分" % (picked, len(target)))
        return 42

    # ③ 顶部按钮 → 抽屉 → 抽屉底部按钮
    b = json.loads(pg.evaluate(JS_FIND_BATCH_BTN) or "{}")
    log("[③] 顶部「%s」 disabled=%s inView=%s"
        % (b.get("t"), b.get("disabled"), b.get("inView")))
    if not b.get("x") or b.get("disabled") or not b.get("inView"):
        log("[FAIL] 顶部批量确认发货按钮不可点（勾到没面单的单？）")
        return 42
    pg.dispatch_mouse(b["x"], b["y"])
    time.sleep(5)

    db = json.loads(pg.evaluate(JS_FIND_DRAWER_BTN) or "{}")
    log("[③] 抽屉底部「%s」 found=%s" % (db.get("t"), db.get("found")))
    if not db.get("found") or db.get("disabled"):
        log("[FAIL] 抽屉没弹出来 / 抽屉按钮不可点")
        return 42
    pg.dispatch_mouse(db["x"], db["y"])
    time.sleep(5)

    # ④ 二级弹窗「是否确认发货？」→「确认」
    fd = json.loads(pg.evaluate(JS_FIND_FINAL) or "{}")
    log("[④] 二级弹窗 need=%s found=%s" % (fd.get("need"), fd.get("found")))
    if fd.get("need") and fd.get("found"):
        pg.dispatch_mouse(fd["x"], fd["y"])
        time.sleep(10)
    elif fd.get("need"):
        log("[FAIL] 二级弹窗弹出来了但找不到「确认」")
        return 42
    else:
        log("    ⚠ 没有出现二级弹窗（可能版本不同），按已提交继续判")

    # ⑤ 判据：只认「待发货」计数下降。
    # ★ 2026-11-06 用户口径：「确认完后，等待 30s 刷新检查」。
    #   真机教训：原来只等 4×4s 就读，平台计数还没刷新，读到 `460 → 460`
    #   就下了 `no_effect`（**实际是成功的**，稍后是 460→393，正好少 67）。
    #   ⇒ 固定：等 30s → **刷新页面** → 重新切「待发货」→ 再读数；
    #     还不够再等一轮，最多 3 轮。只认「刷新后」的数，不认旧渲染。
    log("    等 30s 让平台计数刷新…")
    time.sleep(30)
    after = -1
    for round_i in range(1, 4):
        pg.evaluate("() => { location.reload(); return 1; }")
        time.sleep(12)
        pg = _goto_pending(pg, log)
        st3 = _read_state(pg)
        after_tab = st3.get("pending") or ""
        after = parse_tab_count(after_tab)
        log("    [第 %d 轮刷新] 待发货 %s" % (round_i, after_tab or "-"))
        if after >= 0 and before - after >= len(target):
            break
        if round_i < 3:
            time.sleep(15)
    j = judge_confirm(before, after, len(target))
    log("[⑤] 待发货 %s → %s ⇒ %s %s"
        % (before, after, j["verdict"], j.get("why") or ""))

    try:
        os.makedirs(PEEK, exist_ok=True)
        raw = pg.screenshot()
        with open(os.path.join(PEEK, "temu_confirm_ship_%s.png" % a.store), "wb") as f:
            f.write(raw)
        log("    落图 temu_confirm_ship_%s.png" % a.store)
    except Exception:  # noqa: BLE001
        pass

    print("\n[RESULT] " + json.dumps({"store": a.store, "target": len(target),
                                      "before": before, "after": after,
                                      "verdict": j["verdict"], "ok": j["ok"],
                                      "why": j.get("why") or ""},
                                     ensure_ascii=False))
    return 0 if j["ok"] else 42


if __name__ == "__main__":
    raise SystemExit(main())
