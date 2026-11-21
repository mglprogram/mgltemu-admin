# -*- coding: utf-8 -*-
"""把「恒信加州3仓」库存写成 ⌈领星可用 ÷ 2⌉（用户 2026-11-20：你现在就可以去更新恒信仓的库存，拿 BHY 测试）。

流程（已真机跑通）：
    货号区切 SKU(模糊查询) → 输货号 → 查询（结果 1 行）→ 行内「修改库存」
      → 弹窗「站点与发货仓关系设置」→ goods/stock-edit 编辑页
      → 按「尺码 × 恒信加州3仓」写绝对库存 → 页底「提交」→ 重开回读

安全阀：
  · **只改「恒信加州3」那一行**；加州三仓 / kansas 一个字不碰（用户 2026-11-20 明确「恒信仓的库存」）
  · 默认 dry-run；`--apply` 才写，`--codes` 限定货号，`--limit` 限定个数
  · 目标值 0 且当前也是 0 → 跳过（不发无意义的提交）
  · 提交后重开编辑页回读，逐行核对，不一致就报红

用法：
    python scripts/goods/apply_hx_half.py --codes BHY-203311-4SetA            # dry-run
    python scripts/goods/apply_hx_half.py --codes BHY-203311-4SetA --apply    # 真写
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import bel_stock_set as B  # noqa: E402
import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}
LIST_URL = "https://agentseller.temu.com/goods/list"
SNAP = ROOT / "data" / "stock-snapshot"
HX = "恒信加州3"          # 用户口径里「恒信仓」那一行在编辑页上的名字（实测也出现过「恒信加州3仓」）

FIND_LINK = r"""
(function (code) {
  // ⚠ 2026-11-21 真机：按货号搜到「全部 1」后，`tr.innerText` 里**不含**货号（它只在某个
  //   span 里），所以「行内含货号」这个条件永远不成立 ⇒ 一直 no-link。
  //   判据改成：行里有「修改库存」这个 <a> 就算命中（搜索已把列表收敛到 1 条商品）。
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
  for (var i = 0; i < trs.length; i++) {
    var tr = trs[i];
    var links = Array.prototype.slice.call(tr.querySelectorAll('a'))
      .filter(function (e) { return t(e) === '\u4fee\u6539\u5e93\u5b58'; });
    if (!links.length) continue;
    if ((tr.innerText || '').indexOf(code) < 0 && trs.length > 1) continue;
    for (var k = 0; k < links.length; k++) {
      var p = window.__zr && window.__zr.findProps(links[k]);
      if (p && typeof p.onClick === 'function') { window.__zr.call(links[k], 'onClick'); return 'zr'; }
    }
    links[0].click();
    return 'dom';
  }
  return 'no-link';
})(%s)
"""

DIALOG_LINK = r"""
(function () {
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var b = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
    var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
    if (st.position !== 'fixed' && st.position !== 'absolute') return false;
    var r = d.getBoundingClientRect();
    return r.width > 400 && r.height > 200 && (t(d).indexOf('\u5e93\u5b58\u589e\u51cf') >= 0
                                               || t(d).indexOf('\u4fee\u6539\u5e93\u5b58') >= 0);
  });
  if (!b.length) return 'no-dialog';
  var a = Array.prototype.slice.call(b[0].querySelectorAll('a'))
    .filter(function (e) { return /站点与发货仓/.test(t(e)); })[0];
  if (!a) return 'no-link';
  if (window.__zr) { var p = window.__zr.findProps(a);
    if (p && typeof p.onClick === 'function') { window.__zr.call(a, 'onClick'); return 'zr'; } }
  a.click(); return 'dom';
})()
"""

SEARCH = r"""
(function (code) {
  var ins = Array.prototype.slice.call(document.querySelectorAll('input'));
  // ① 找到「货号」区的条件框（值以 SKU(/SKC( 开头的那一排的最后一个）
  var condIdx = -1;
  for (var i = 0; i < ins.length - 1; i++) {
    if (/^(SKU|SKC)\(/.test((ins[i].value || '').trim())) { condIdx = i; }
  }
  if (condIdx < 0) return 'no-cond';
  var state = 'cond=' + (ins[condIdx].value || '');
  // ② 不是 SKU(模糊查询) 就先切过去（点开 → 选 li）
  if ((ins[condIdx].value || '').trim() !== 'SKU(\u6a21\u7cca\u67e5\u8be2)') {
    ins[condIdx].click();
    var lis = Array.prototype.slice.call(document.querySelectorAll('li'));
    var want = null;
    for (var j = 0; j < lis.length; j++) {
      var t = (lis[j].innerText || '').replace(/\s+/g, ' ').trim();
      if (t === 'SKU(\u6a21\u7cca\u67e5\u8be2)') { want = lis[j]; break; }
    }
    if (want) {
      var p = window.__zr.findProps(want);
      if (p && typeof p.onClick === 'function') { window.__zr.call(want, 'onClick'); }
      else { want.click(); }
      state += '->switched';
    } else {
      state += '->no-li';
    }
  }
  // ③ 输货号（条件框后面第一个可编辑框）
  var target = -1;
  for (var k = condIdx + 1; k < ins.length; k++) {
    if (!ins[k].readOnly) { target = k; break; }
  }
  if (target < 0) return state + '|no-target';
  window.__zr.write(ins[target], code);
  // ④ 查询
  var b = window.__zr.btn('\u67e5\u8be2');
  if (b) window.__zr.call(b, 'onClick');
  return state + '|typed:' + ins[target].value;
})(%s)
"""

#: 编辑页每行：尺码（合并单元格要继承）+ 发货仓 + 输入框 index
READ_EDIT = r"""
(function () {
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
  var size = '', out = [];
  trs.forEach(function (tr, ri) {
    var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
    var ins = Array.prototype.slice.call(tr.querySelectorAll('input'));
    var editable = [];
    ins.forEach(function (box, ii) {
      if (!box.readOnly) editable.push({idx: ii, val: box.value});
    });
    if (!editable.length) return;
    var m = cells.filter(function (c) {
      return /^(XXS|XS|S|M|L|XL|XXL|XXXL|3XL|4XL|5XL|\d+Y)$/.test(c); })[0];
    if (m) size = m;
    var wh = cells.filter(function (c) { return /仓|加州|恒信|kansas/i.test(c); })[0] || '';
    out.push({row: ri, size: size, sizeOwn: !!m, wh: wh,
              val: editable[0].val, rowInputs: ins.length, cellCount: cells.length});
  });
  return JSON.stringify(out);
})()
"""

WRITE_ROW = r"""
(function (ri, val) {
  var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
  var tr = trs[ri];
  if (!tr) return 'no-row';
  var box = Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) {
    return !i.readOnly; })[0];
  if (!box) return 'no-input';
  if (window.__zr) window.__zr.write(box, String(val));
  else { box.value = String(val); box.dispatchEvent(new Event('input', {bubbles: true})); }
  return 'ok:' + box.value;
})(%s, %s)
"""

CLICK_SUBMIT = r"""
(function () {
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var bs = Array.prototype.slice.call(document.querySelectorAll('button'))
    .filter(function (b) { return t(b) === '\u63d0\u4ea4' && b.getClientRects().length; });
  if (!bs.length) return 'no-btn';
  var b = bs[0];
  var p = window.__zr && window.__zr.findProps(b);
  if (p && typeof p.onClick === 'function') { window.__zr.call(b, 'onClick'); return 'zr'; }
  b.click(); return 'dom';
})()
"""

CONFIRM = r"""
(function () {
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var bs = Array.prototype.slice.call(document.querySelectorAll('button'))
    .filter(function (b) { return /^(\u786e\u5b9a|\u786e\u8ba4)$/.test(t(b)) && b.getClientRects().length; });
  if (!bs.length) return 'no-confirm';
  var b = bs[bs.length - 1];
  var p = window.__zr && window.__zr.findProps(b);
  if (p && typeof p.onClick === 'function') { window.__zr.call(b, 'onClick'); return 'zr'; }
  b.click(); return 'dom';
})()
"""


def load_targets(store: str, codes: list, day: str) -> dict:
    p = SNAP / ("%s.json" % day)
    if not p.exists():
        raise SystemExit("没有当天快照：%s" % p)
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = ((d.get("stores") or {}).get(store) or {}).get("rows") or []
    tgt: dict = {}
    for r in rows:
        if codes and r["code"] not in codes:
            continue
        tgt.setdefault(r["code"], {})[str(r["size"]).upper()] = int(r.get("hxHalf") or 0)
    return tgt


async def open_edit(port: int, store: str, code: str) -> dict | None:
    """新开干净页 → 货号搜索 → 修改库存 → 站点与发货仓 → 返回 stock-edit 页 dict。"""
    # ★ 2026-11-20：复用**自有**列表页，别每次新开 —— 反复跑会把窗口塞满标签页，
    #   之后新页加载慢、操作列出不来，就变成一串 no-link（实测第 6 次开始必挂）。
    tid = B.load_work_tab(store) or ""
    ids = [x.get("id") for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
           if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if tid not in ids:
        tid = await cdp_net.new_tab(port, LIST_URL, foreground=True)
        B.save_work_tab(store, tid)
        print("  新开列表页", tid[:8])
    else:
        print("  复用自有列表页", tid[:8])
    await asyncio.sleep(12)
    page = None
    for _ in range(20):
        for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port):
            if x.get("id") == tid and x.get("webSocketDebuggerUrl"):
                page = x
                break
        if page:
            break
        await asyncio.sleep(1.5)
    if not page:
        print("  ✗ 新页没枚举出来")
        return None
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.any_page_ok = True
    await tab.open()
    await tab.ensure()
    await zreact.inject(tab.session)
    await B.page_ready(tab, 60)
    await B.close_overlays(tab)
    # ⚠ 2026-11-20 真机：切到 SKU(模糊查询) 后**不能用旧关键词**再查——必须先清空另一个查询区的
    #   关键词（商品ID区 idx1 常留着上一个 SPU/货号），否则结果 0 行、后面的「修改库存」全 no-link。
    #   清完再按「条件框已是 SKU(模糊查询) → 写货号 → 查询」走。
    await tab.session.eval(
        "(function(){var ins=[].slice.call(document.querySelectorAll('input'));"
        "ins.forEach(function(i,n){if(!i.readOnly && n<=1 && (i.value||'').trim()){"
        "window.__zr.write(i,'');}});return 'cleared';})()", timeout=25)
    await asyncio.sleep(0.6)
    # ★ 2026-11-21 修「一直 no-link」：`tab.session.eval` 在这张列表页上**经常返回 None**
    #   （页面被 qiankun 包装过），而 `B.ev()` 会走 replMode 重试并返回真实字符串。
    #   所有页面操作用 `B.ev/B.ev_json`，不要再直接用 cap.eval。
    await B.ev(tab, "(function(){return 'probe';})()")
    r = await B.ev(tab, SEARCH % json.dumps(code))
    print("  搜索:", r)
    await asyncio.sleep(4)
    # ⚠ 2026-11-21 真机：这条链路**结果出来得慢**，刚开始查完还有 15 行（上一次的结果），
    #   要等 3~6 秒才收敛到 5 行。所以「修改库存」要边等边找，而不是等一次就判定 no-link。
    c = None
    for attempt in range(6):
        c = await B.ev(tab, FIND_LINK % json.dumps(code))
        print("  找「修改库存」第 %d 次: %s" % (attempt + 1, c))
        if c and c != "no-link":
            break
        await asyncio.sleep(3)
    await asyncio.sleep(3)
    d = await B.ev(tab, DIALOG_LINK)
    print("  点站点与发货仓:", d)
    if d in (None, "no-dialog", "no-link"):
        for _ in range(3):
            await asyncio.sleep(2)
            c = await B.ev(tab, FIND_LINK % json.dumps(code))
            d = await B.ev(tab, DIALOG_LINK)
            print("  重试（修改库存=%s / 站点与发货仓=%s）" % (c, d))
            if d in ("zr", "dom"):
                break
    # ⚠ 2026-11-21 用户：一个货号开出好几个「设置库存」页。
    #   根因在这里：以前取 `stock-edit` 页列表的**最后一个**，但**上一次的旧页还开着**时，
    #   新页排在哪不一定 ⇒ 每次可能挑到旧页、又把新的留在后台（越积越多）。
    #   ⇒ 先用**那一刻已存在的 stock-edit id 集合**做基线，只认基线之外的新页（就是本次开的）。
    baseline = {x.get("id") for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                if x.get("type") == "page" and "stock-edit" in (x.get("url") or "")}
    for _ in range(15):
        await asyncio.sleep(1.5)
        cands = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                 if x.get("type") == "page" and "stock-edit" in (x.get("url") or "")
                 and x.get("id") not in baseline]
        if cands:
            await asyncio.sleep(3)
            print("  本次新开的编辑页:", cands[-1]["id"][:8])
            return cands[-1]
    return None


async def read_rows(page: dict) -> list:
    async with cdp_net.NetCapture(page["webSocketDebuggerUrl"], match=()) as cap:
        await zreact.inject(cap)
        for _ in range(6):
            v, _ = await cap.eval(READ_EDIT, timeout=25)
            rows = json.loads(v or "[]")
            if rows:
                return rows
            await asyncio.sleep(2)
    return []


def _ledger(store: str, code: str, sizes: dict) -> None:
    """记一笔「恒信仓写成了多少」的台账，便于事后核对/回滚参考。"""
    p = ROOT / "data" / "hx-half-ledger.json"
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": []}
    items = old.get("items") or []
    now = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for sz, v in sizes.items():
        items.append({"store": store, "code": code, "size": sz, "hx": int(v), "at": now,
                      "by": "apply_hx_half"})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")


async def _close_new_edit_pages(port: int, before_ids: set) -> int:
    """异步版：关掉本次新开的 stock-edit 页，返回关闭个数。"""
    closed = 0
    try:
        ws = cdp_net.http_json("http://127.0.0.1:%d/json/version" % port)["webSocketDebuggerUrl"]
    except Exception:  # noqa: BLE001
        return 0
    for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port):
        if x.get("type") != "page" or "stock-edit" not in (x.get("url") or ""):
            continue
        if x.get("id") in before_ids:
            continue
        try:
            await cdp_net._one_shot(ws, "Target.closeTarget", {"targetId": x["id"]})
            closed += 1
        except Exception:  # noqa: BLE001
            pass
    return closed


async def main() -> int:
    ap = argparse.ArgumentParser(description="恒信加州3仓 = ⌈领星可用/2⌉（BHY 测试）")
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--codes", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    tgt = load_targets(a.store, codes, a.date)
    if a.limit:
        tgt = dict(list(tgt.items())[:a.limit])
    print("货号 %d 个 / 尺码 %d 个%s" % (len(tgt), sum(len(v) for v in tgt.values()),
                                     "" if a.apply else "（dry-run）"))
    port = probe_port(WINDOWS[a.store])
    if not port:
        print("!! 探不到窗口")
        return 3
    done = 0
    # ★ 2026-11-21：开跑前记下**已经存在**的 stock-edit 页；跑完（含异常）把本次新开的全关掉。
    #   用户口径：「设置完一个关一个」——实测不这么做会一次堆 11~24 个标签。
    before_ids = {x.get("id") for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                  if x.get("type") == "page" and "stock-edit" in (x.get("url") or "")}
    try:
        for code, sizes in tgt.items():
            print("\n== %s 目标 %s" % (code, "，".join("%s→%d" % kv for kv in sorted(sizes.items()))))
            ed = await open_edit(port, a.store, code)
            if not ed:
                print("  ✗ 没打到编辑页")
                continue
            rows = await read_rows(ed)
            # ★ 2026-11-20 用户新口径：**两仓合计不超过一半**
            #   恒信加州3仓 = max(0, 一半 − 加州三仓已有)；加州三仓现场读，读到才用。
            ca3 = {}
            for r in rows:
                wh = str(r.get("wh") or "")
                if any(k in wh for k in ("加州三仓", "加州3仓")) and not any(k in wh for k in ("恒信",)):
                    try:
                        ca3[str(r.get("size") or "").upper()] = int(str(r.get("val") or "0") or 0)
                    except ValueError:
                        ca3[str(r.get("size") or "").upper()] = 0
            want = []
            for r in rows:
                sz = str(r.get("size") or "").upper()
                if HX not in str(r.get("wh") or ""):
                    continue
                if sz not in sizes:
                    continue
                half = int(sizes[sz])
                already = int(ca3.get(sz, 0))
                target = max(0, half - already)
                want.append((r, target, half, already))
            if not want:
                print("  ✗ 编辑页没找到「恒信加州3」行")
                continue
            for r, v, half, already in want:
                print("   %-4s 现 %-6s → 目标 %-6s（一半 %-5s − 加州三仓 %-5s）%s"
                      % (r["size"], r["val"], v, half, already,
                         "（不变）" if str(r["val"]) == str(v) else ""))
            if not a.apply:
                # ★ dry-run 也报「是否已达标」，随时能查某货号有没有同步过
                good = all(str(r["val"]) == str(v) for r, v, _h, _a in
                           [(x[0], x[1], x[2], x[3]) for x in want])
                print("   dry-run：%s" % ("已达标 ✅" if good else "需要更新"))
                # ⚠ 2026-11-21 用户：「设置完一个关一个」——dry-run 也要关，否则每查一次多挂一个页
                try:
                    ws = cdp_net.http_json("http://127.0.0.1:%d/json/version" % port)["webSocketDebuggerUrl"]
                    await cdp_net._one_shot(ws, "Target.closeTarget", {"targetId": ed["id"]})
                    try:
                        await B.close_dialog(tab)
                    except Exception:  # noqa: BLE001
                        pass
                    print("   已关闭该货号的「设置库存」页")
                except Exception as e:  # noqa: BLE001
                    print("   ⚠ 关页失败：%s" % e)
                continue
            async with cdp_net.NetCapture(ed["webSocketDebuggerUrl"], match=()) as cap:
                await zreact.inject(cap)
                for r, v, _half, _already in want:
                    if str(r["val"]) == str(v):
                        continue
                    w, _ = await cap.eval(WRITE_ROW % (json.dumps(r["row"]), json.dumps(v)), timeout=25)
                    print("   写 %-4s → %s" % (r["size"], w))
                s, _ = await cap.eval(CLICK_SUBMIT, timeout=25)
                print("   提交:", s)
                await asyncio.sleep(2)
                c, _ = await cap.eval(CONFIRM, timeout=25)
                print("   二次确认:", c)
            await asyncio.sleep(4)
            # ★ 只有回读到目标值才记成功；否则报红，别让「写了一半」被当完成
            back = await read_rows(ed)
            if not back:
                print("   ⚠ 回读失败（编辑页没读出来），不算成功")
                continue
            got = {str(r["size"]).upper(): r["val"] for r in back if HX in str(r.get("wh") or "")}
            tgt_map = {str(r["size"]).upper(): v for r, v, _h, _a in want}
            ok = all(str(got.get(sz)) == str(v) for sz, v in tgt_map.items())
            print("   回读 %s → %s" % (got, "✅" if ok else "❌"))
            if ok:
                done += 1
                _ledger(a.store, code, sizes)
            # ⚠ 2026-11-21 用户：「设置完一个关一个」。
            #   这个货号处理完就把它自己开的「设置库存」页关掉，否则每跑一次就多挂一个标签
            #   （实测一次残留 4 个 stock-edit 页，窗口越跑越慢，还会拖垮后续的 eval）。
            try:
                ws = cdp_net.http_json("http://127.0.0.1:%d/json/version" % port)["webSocketDebuggerUrl"]
                await cdp_net._one_shot(ws, "Target.closeTarget", {"targetId": ed["id"]})
                # 弹窗也一并取消掉（同一张列表页还要复用去改下一个货号）
                try:
                    await B.close_dialog(tab)
                except Exception:  # noqa: BLE001
                    pass
                print("   已关闭该货号的「设置库存」页")
            except Exception as e:  # noqa: BLE001
                print("   ⚠ 关页失败：%s" % e)
    finally:
        # 无论成功/异常，把**本次新开**的 stock-edit 页全部收掉（含中途失败留下的）
        n = await _close_new_edit_pages(port, before_ids)
        if n:
            print("\n清理本次新开的「设置库存」页 %d 个" % n)
    print("\n完成 %d 个货号（%s）" % (done, "已写后台" if a.apply else "dry-run 未写"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
