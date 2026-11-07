# -*- coding: utf-8 -*-
"""temu_export_by_select.py — 走**订单列表 → 勾选 → 导出订单 → 右上角下载记录**这条导出路线。

用户 2026-11-06 口径（截图指引）：
    「导出订单：勾选订单信息 → 导出订单，就可以在右上角找到导出的订单表」

为什么改走这条（不再死磕 export-order.html）：
    `export-order.html` 要现填「订单日期」区间，而那个日历是双月视图、点格子还会翻月；
    实测点错面板会导成 `2026-12-05 ~ 2027-01-09` 这种完全错的区间。
    而订单列表这条路：**当前页/筛出来的单直接导出**，不需要碰日历，
    导出完自动落在右上角「下载记录」里，直接下载即可。

流程：
    ① 确保在「待发货」页签
    ② 每页拉到最大（本仓口径 500；BHY 页面的每页控件可能是 readonly 显示，读不到就跳过）
    ③ 表头全选（已全选就不再点，避免切换成取消）
    ④ 点「导出订单」
    ⑤ 等右上角「近期下载记录」出现新文件 → 点它下载
    ⑥ 校验：下载到本地 xlsx 且行数 > 0
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

PEEK = os.path.join(_HERE, "out", "_peek")
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}


def _page(store: str):
    import cdp_transport as C  # noqa: PLC0415
    import ziniao_store as zs  # noqa: PLC0415
    win = WINDOWS.get(store, store)
    port = zs.probe_port_by_window(win)
    if not port:
        raise RuntimeError("窗口 %s 未开" % win)
    tgs = [t for t in zs.list_page_targets(int(port))
           if "/mmsos/orders" in (t.get("url") or "")
           and "parentOrderSnList=P" not in (t.get("url") or "")]
    if not tgs:
        raise RuntimeError("窗口 %s 里没有订单列表页" % win)
    pg = C.CdpPage(int(port), tgs[0])
    # ★ 2026-11-08 修：窗口在后台/没被激活时，该 tab 的渲染视口是 **0×0**，
    #   于是 getBoundingClientRect 全变负数（实测 BHY: x=-386,y=-300）、
    #   表头全选框点不到、「导出订单」读成 disabled ⇒ 整店导出失败 rc=42。
    #   先把这个 tab 提到前台，视口恢复 2133×1012 才能点到。
    try:
        pg.cdp_session().send("Page.bringToFront", {})
        time.sleep(1.5)
        w = int(float(pg.evaluate("() => window.innerWidth") or 0))
        if w <= 0:
            print("   ⚠ 视口仍为 0×0（%s），点击大概率无效" % win)
    except Exception as ex:  # noqa: BLE001
        print("   ⚠ bringToFront 失败（继续）：%s" % str(ex)[:80])
    return pg, int(port)


JS_STATE = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
      && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const body = document.body.innerText||'';
  const tabs = [...document.querySelectorAll('[role=tab],[class*=tab]')].filter(vis).map(txt)
    .filter(t => /^待发货\(\d+\)$/.test(t));
  const sel = (body.match(/已选订单[：:]\s*(\d+)/)||[])[1]||'';
  const total = (body.match(/共有\s*(\d+)\s*条/)||[])[1]||'';
  const btns = [...document.querySelectorAll('button')].filter(vis).map(txt);
  const exp = btns.find(t => /^导出订单$/.test(t)) || '';
  const rows = [...document.querySelectorAll('tr')].filter(t => /PO-\d+-\d+/.test(t.innerText||'')).length;
  return JSON.stringify({pending: tabs[0]||'', selected: sel, total, hasExport: !!exp, rows});
}"""


def set_page_size_500(pg, log=print) -> int:
    """把订单列表「每页」拉到 500（用户 2026-11-06 截图指引：「改成每页 500 条记录即可」）。

    ★ 真机结构（BHY/BEL 一致）：
        ul[data-testid=beast-core-pagination]
          └ li.PGT_sizeChanger_123          ← 「每页」+ 下拉
              └ div[data-testid=beast-core-select]
                  └ div[data-testid=beast-core-select-header]  ← **点这里展开**
        展开后选项是 `li.cIL_item_123`（5/10/20/50/100/500）。
      坑：① 控件在页面**最底部**，不 scrollIntoView 就取到视口外坐标（CDP 点了静默无效）；
          ② 那个 `<input readonly value="20">` 只是展示，改 value 没用，必须点下拉项；
          ③ 选项浮层出现在控件**上方**（y 变小），别按"往下找"。
    返回最终渲染行数。
    """
    cur = pg.evaluate(r"""() => {
      const b = (document.body.innerText||'').replace(/\s+/g,' ');
      const sel = b.match(/已选订单[：:]\s*(\d+)/);
      const rows = [...document.querySelectorAll('tr')]
        .filter(t => /PO-\d+-\d+/.test(t.innerText||'')).length;
      return JSON.stringify({rows, selected: sel?sel[1]:'0'});
    }""")
    st = json.loads(str(cur) or "{}")
    rows = int(st.get("rows") or 0)
    total = int(json.loads(pg.evaluate(JS_STATE) or "{}").get("total") or 0)
    if total and rows >= total:
        log("   每页已够（%d 行 / 共 %d 条）" % (rows, total))
        return rows

    b = json.loads(pg.evaluate(r"""() => {
      const sc = document.querySelector('li.PGT_sizeChanger_123');
      if (!sc) return '{}';
      sc.scrollIntoView({block:'center'});
      const r = sc.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }""") or "{}")
    if not b.get("x"):
        log("   ⚠ 没找到每页控件（li.PGT_sizeChanger_123）")
        return rows
    pg.dispatch_mouse(b["x"], b["y"])
    time.sleep(2.5)
    opt = json.loads(pg.evaluate(r"""() => {
      const vis = e => { const r = e.getBoundingClientRect();
        return e.getClientRects().length>0 && r.width>0 && r.height>0; };
      const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
      const it = [...document.querySelectorAll('li.cIL_item_123')].filter(vis)
        .find(e => txt(e) === '500');
      if (!it) return '{}';
      const r = it.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }""") or "{}")
    if not opt.get("x"):
        log("   ⚠ 下拉里没有 500 选项")
        return rows
    pg.dispatch_mouse(opt["x"], opt["y"])
    time.sleep(8)
    st2 = json.loads(pg.evaluate(JS_STATE) or "{}")
    log("   每页 500 后：%d 行 / 共 %s 条" % (st2.get("rows") or 0, st2.get("total")))
    return int(st2.get("rows") or 0)


def main() -> int:
    ap = argparse.ArgumentParser(description="订单列表勾选导出（不走 export-order 页）")
    ap.add_argument("--store", required=True)
    ap.add_argument("--out-dir", default="", help="下载落点（默认紫鸟默认下载目录）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    pg, port = _page(a.store)
    print("① 页面:", pg.evaluate("() => location.href")[:100])
    print("   ", pg.evaluate(JS_STATE))

    # ② 切到待发货（若不在）
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
        print("② 切到:", d.get("t"))

    # ②.5 每页 500（用户指引：改成每页 500 条记录即可）
    print("②.5 拉每页到 500…")
    set_page_size_500(pg)

    # ③ 全选（**已勾选就不再点**：勾选是切换式，再点会取消）
    st = json.loads(pg.evaluate(JS_STATE) or "{}")
    print("③ 当前:", json.dumps(st, ensure_ascii=False))
    if int(st.get("selected") or 0) == 0:
        hd = json.loads(pg.evaluate(r"""() => {
          const ths = [...document.querySelectorAll('th')].filter(th => {
            const r = th.getBoundingClientRect(); return r.width>0 && r.height>0; });
          const th0 = ths.find(th => !(th.innerText||'').trim()
                                && th.getBoundingClientRect().width < 60);
          if (!th0) return '{}';
          th0.scrollIntoView({block:'center'});
          const lab = th0.querySelector('label[data-testid=beast-core-checkbox]') || th0.querySelector('label') || th0;
          const r = lab.getBoundingClientRect();
          return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                                 checked: lab.getAttribute ? lab.getAttribute('data-checked') : ''});
        }""") or "{}")
        print("   全选框:", hd)
        if hd.get("x"):
            pg.dispatch_mouse(hd["x"], hd["y"])
            time.sleep(3)
            st = json.loads(pg.evaluate(JS_STATE) or "{}")
            print("   全选后:", json.dumps(st, ensure_ascii=False))

    # ④ 点「导出订单」
    b = json.loads(pg.evaluate(r"""() => {
      const vis = e => { const r = e.getBoundingClientRect();
        return e.getClientRects().length>0 && r.width>0 && r.height>0; };
      const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
      const el = [...document.querySelectorAll('button')].filter(vis)
        .find(x => /^导出订单$/.test(txt(x)));
      if (!el) return '{}';
      el.scrollIntoView({block:'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                             disabled: el.disabled===true});
    }""") or "{}")
    print("④ 导出订单按钮:", b)
    if not b.get("x") or b.get("disabled"):
        print("[FAIL] 导出按钮不可点")
        return 42
    if a.dry_run:
        print("[dry-run] 未点导出")
        return 0

    # ④.2 设好下载落点（**关键**）：右上角「近期下载记录」是**浏览器自带 UI**，
    #      页面 DOM 里查不到它（实测 `[class*=download]` 全空）。
    #      所以不走"点右上角"，而是让 CDP 把文件直接下到指定目录。
    # ★ 2026-11-06 修（我被自己的"空目录"骗了半天）：
    #   `Browser.setDownloadBehavior` 在紫鸟窗口上**不生效**，文件始终落在
    #   **紫鸟窗口的默认下载目录**：
    #     %APPDATA%\ziniaobrowserdatas\ziniao browser\<窗口名>\订单导出*.xlsx
    #   旧代码建了个自建目录并只检查它 ⇒ 明明导出成功却报「没出现下载记录」。
    #   正解：**按修改时间扫紫鸟默认目录**，取点击之后新增/更新的那个文件。
    win_dir = os.path.join(os.environ.get("APPDATA", ""), "ziniaobrowserdatas",
                           "ziniao browser", WINDOWS.get(a.store, a.store))
    print("④.2 紫鸟默认下载目录:", win_dir)
    before_files = {}
    if os.path.isdir(win_dir):
        for f in glob.glob(os.path.join(win_dir, "*.xlsx")):
            try:
                before_files[f] = os.path.getmtime(f)
            except OSError:
                pass
    # 仍尝试改目录（某些窗口版本有效），失败不影响后续扫目录
    try:
        pg.cdp_session().send("Browser.setDownloadBehavior",
                              {"behavior": "allow", "downloadPath": win_dir})
    except Exception:  # noqa: BLE001
        pass

    pg.dispatch_mouse(b["x"], b["y"])
    time.sleep(4)

    # ④.5 确认弹窗「已选 N 个订单，确认要导出吗？」→ 点「确认导出」
    #      ★ 2026-11-06：不点这个弹窗，导出根本不会生成（下一步永远找不到下载记录）。
    cd = json.loads(pg.evaluate(r"""() => {
      const vis = e => { const r = e.getBoundingClientRect();
        return e.getClientRects().length>0 && r.width>0 && r.height>0; };
      const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
      const body = document.body.innerText||'';
      const asked = /确认要导出吗/.test(body);
      const el = [...document.querySelectorAll('button')].filter(vis)
        .find(x => /^确认导出$/.test(txt(x)));
      if (!el) return JSON.stringify({asked, found: false});
      const r = el.getBoundingClientRect();
      return JSON.stringify({asked, found: true, x: Math.round(r.x + r.width/2),
                             y: Math.round(r.y + r.height/2)});
    }""") or "{}")
    print("④.5 确认弹窗:", cd)
    if cd.get("found"):
        pg.dispatch_mouse(cd["x"], cd["y"])
        time.sleep(10)
    elif cd.get("asked"):
        print("[FAIL] 弹窗在但找不到「确认导出」")
        return 42

    # ⑤ 扫紫鸟默认下载目录，取点击之后新增/更新的那个 xlsx
    print("⑤ 等文件落盘…")
    newest = ""
    for i in range(30):
        time.sleep(2)
        cands = []
        if os.path.isdir(win_dir):
            for f in glob.glob(os.path.join(win_dir, "*.xlsx")):
                try:
                    mt = os.path.getmtime(f)
                except OSError:
                    continue
                if before_files.get(f) != mt:
                    cands.append((mt, f))
        if cands:
            newest = max(cands)[1]
            print("   第 %ds 发现新文件：%s" % ((i + 1) * 2, os.path.basename(newest)))
            break
    if not newest:
        print("[FAIL] 60s 内紫鸟下载目录没有新文件：%s" % win_dir)
        return 42

    # ⑥ 校验行数
    try:
        import pandas as pd  # noqa: PLC0415
        df = pd.read_excel(newest, sheet_name=0)
        print("⑥ 文件：%s ｜ %d 行 × %d 列"
              % (os.path.basename(newest), len(df), len(df.columns)))
        if len(df) == 0:
            print("[FAIL] 导出文件是空的")
            return 42
    except Exception as ex:  # noqa: BLE001
        print("⑥ ⚠ 读不出行数（文件可能还在写）：%s" % str(ex)[:60])

    print("\n[RESULT] " + json.dumps({"store": a.store, "file": newest,
                                      "ok": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
