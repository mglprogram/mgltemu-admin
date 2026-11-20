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
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
  for (var i = 0; i < trs.length; i++) {
    if ((trs[i].innerText || '').indexOf(code) < 0) continue;
    var links = Array.prototype.slice.call(trs[i].querySelectorAll('a,span,div'))
      .filter(function (e) { return t(e) === '\u4fee\u6539\u5e93\u5b58'; });
    if (!links.length) continue;
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
  var wrote = 0;
  for (var i = 0; i < ins.length - 1; i++) {
    if (/^SKU\(/.test((ins[i].value || '').trim()) && !ins[i + 1].readOnly) {
      window.__zr.write(ins[i + 1], code);
      wrote++;
    }
  }
  var b = window.__zr.btn('\u67e5\u8be2');
  if (b) window.__zr.call(b, 'onClick');
  return 'wrote:' + wrote;
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
    tid = await cdp_net.new_tab(port, LIST_URL, foreground=True)
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
    await tab.session.eval(
        "(function(){var ins=[].slice.call(document.querySelectorAll('input'));"
        "ins.forEach(function(i,n){if(!i.readOnly && n<5 && (i.value||'').trim()){"
        "window.__zr.write(i,'');}});return 'cleared';})()", timeout=25)
    r = await tab.session.eval(SEARCH % json.dumps(code), timeout=25)
    print("  搜索:", r[0] if isinstance(r, tuple) else r)
    await asyncio.sleep(4)
    c = await tab.session.eval(FIND_LINK % json.dumps(code), timeout=25)
    c = c[0] if isinstance(c, tuple) else c
    print("  点修改库存:", c)
    if c in (None, "no-link"):
        # ⚠ 2026-11-20 真机：有时行还没渲染完/操作列要悬停才出现 ⇒ 等一会重试几次
        for _ in range(3):
            await asyncio.sleep(3)
            c = await tab.session.eval(FIND_LINK % json.dumps(code), timeout=25)
            c = c[0] if isinstance(c, tuple) else c
            print("  重试点修改库存:", c)
            if c and c != "no-link":
                break
    await asyncio.sleep(3)
    d = await tab.session.eval(DIALOG_LINK, timeout=25)
    d = d[0] if isinstance(d, tuple) else d
    print("  点站点与发货仓:", d)
    if d in (None, "no-dialog", "no-link"):
        for _ in range(3):
            await asyncio.sleep(2)
            c = await tab.session.eval(FIND_LINK % json.dumps(code), timeout=25)
            c = c[0] if isinstance(c, tuple) else c
            d = await tab.session.eval(DIALOG_LINK, timeout=25)
            d = d[0] if isinstance(d, tuple) else d
            print("  重试（修改库存=%s / 站点与发货仓=%s）" % (c, d))
            if d in ("zr", "dom"):
                break
    for _ in range(15):
        await asyncio.sleep(1.5)
        ed = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
              if x.get("type") == "page" and "stock-edit" in (x.get("url") or "")]
        if ed:
            await asyncio.sleep(3)
            return ed[-1]
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
    for code, sizes in tgt.items():
        print("\n== %s 目标 %s" % (code, "，".join("%s→%d" % kv for kv in sorted(sizes.items()))))
        ed = await open_edit(port, a.store, code)
        if not ed:
            print("  ✗ 没打到编辑页")
            continue
        rows = await read_rows(ed)
        want = []
        for r in rows:
            sz = str(r.get("size") or "").upper()
            if HX not in str(r.get("wh") or ""):
                continue
            if sz not in sizes:
                continue
            want.append((r, sizes[sz]))
        if not want:
            print("  ✗ 编辑页没找到「恒信加州3」行")
            continue
        for r, v in want:
            print("   %-4s 现 %-6s → 目标 %-6s %s" % (r["size"], r["val"], v,
                                                     "（不变）" if str(r["val"]) == str(v) else ""))
        if not a.apply:
            # ★ dry-run 也报「是否已达标」，随时能查某货号有没有同步过
            good = all(str(r["val"]) == str(v) for r, v in want)
            print("   dry-run：%s" % ("已达标 ✅" if good else "需要更新"))
            continue
        async with cdp_net.NetCapture(ed["webSocketDebuggerUrl"], match=()) as cap:
            await zreact.inject(cap)
            for r, v in want:
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
        ok = all(str(got.get(sz)) == str(v) for sz, v in sizes.items())
        print("   回读 %s → %s" % (got, "✅" if ok else "❌"))
        if ok:
            done += 1
            _ledger(a.store, code, sizes)
    print("\n完成 %d 个货号（%s）" % (done, "已写后台" if a.apply else "dry-run 未写"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
