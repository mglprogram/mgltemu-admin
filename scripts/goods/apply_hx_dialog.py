# -*- coding: utf-8 -*-
"""恒信仓库存：**直接在「修改库存」弹窗里加减**（用户 2026-11-21 定的路径）。

用户原话：「回溯到之前直接点击修改库存，直接在弹窗加减库存，这一步操作不去打开设置库存页面了。」

所以：
    · 不再点「站点与发货仓关系设置」、不再开 `goods/stock-edit` 页；
    · 在弹窗里对「恒信加州3仓」那一行「库存增减」直接填数（增加/减少），多尺码一次提交；
    · 弹窗先按 SPU 开，再按**货号**切块定位（同 SPU 多 SKC 也不串）；
    · 加州三仓已有库存从弹窗里读（决定「两仓合计≤一半」要补多少）。

口径（用户 2026-11-23 最终版）：
    恒信加州3仓 = max(0, ⌈领星可用 × 80%⌉)      ← **不再扣加州已有**
    加州3仓 / Kansas仓 = 0                      ← 弹窗里按「减少当前值」清零
     · 目标 > 当前 → 「增加」差值；目标 < 当前 → 「减少」差值；相等 → 跳过
     · 只写恒信那一行，加州三仓 / kansas 数字一律不动

用法：
    python scripts/goods/apply_hx_dialog.py --codes BHY-203311-4SetA            # dry-run
    python scripts/goods/apply_hx_dialog.py --codes BHY-203311-4SetA --apply    # 真写
    python scripts/goods/apply_hx_dialog.py --limit 5 --apply                   # 批量
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

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF", "KinglamanSport": "TM-KinglamanSport"}


def canon(store: str) -> str:
    """业务简称 → 快照/窗口里用的店名（快照里 KLS 叫 KinglamanSport）。"""
    return "KinglamanSport" if str(store).upper() == "KLS" else store


def _probe_port(window: str) -> int:
    """探端口：探不到就重试 4 次（用户 2026-11-23 C 项）。"""
    p = 0
    for i in range(4):
        try:
            p = probe_port(window) or 0
        except Exception as e:  # noqa: BLE001
            print("  探端口异常（第 %d 次）：%s" % (i + 1, str(e)[:90]))
            p = 0
        if p:
            return p
        if i < 3:
            import time as _t
            _t.sleep(5)
    return p
LIST_URL = "https://agentseller.temu.com/goods/list"
SNAP = ROOT / "data" / "stock-snapshot"
HX = "恒信"

#: 弹窗每行：属性集（含尺码）、发货仓、当前库存
READ_DLG = r"""
(function () {
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var b = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
    var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
    if (st.position !== 'fixed' && st.position !== 'absolute') return false;
    var r = d.getBoundingClientRect();
    return r.width > 400 && r.height > 200 && t(d).indexOf('\u5e93\u5b58\u589e\u51cf') >= 0;
  });
  if (!b.length) return JSON.stringify({err: 'no-dialog'});
  b.sort(function (a, c) { return a.querySelectorAll('tbody tr').length - c.querySelectorAll('tbody tr').length; });
  var box = b[0];
  var out = [], code = '', size = '';
  Array.prototype.slice.call(box.querySelectorAll('tbody tr')).forEach(function (tr, i) {
    var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
    var joined = cells.join(' | ');
    var m = joined.match(/\u8d27\u53f7\uff1a\s*([A-Za-z0-9.\-]+)/);
    if (m) code = m[1];
    var sz = cells.filter(function (c) {
      return /^[^\s]+-(XXS|XS|S|M|L|XL|XXL|XXXL|3XL|4XL|5XL|\d+Y)$/.test(c); })[0] || '';
    if (sz) size = sz.split('-').pop();
    var wh = cells.filter(function (c) { return /\u4ed3|\u52a0\u5dde|\u6052\u4fe1|kansas/i.test(c); })[0] || '';
    var cur = '';
    for (var k = 0; k < cells.length; k++) {
      if (cells[k] === '\u5356\u5bb6\u81ea\u586b\u5199\u5e93\u5b58' && cells[k + 1] && /^\d+$/.test(cells[k + 1])) {
        cur = cells[k + 1]; break;
      }
    }
    if (!cur) {
      var nx = 0;
      for (var j = 0; j < cells.length; j++) {
        if (/\u4ed3|\u52a0\u5dde|\u6052\u4fe1|kansas/i.test(cells[j])) {
          for (var q = j + 1; q < cells.length; q++) {
            if (/^\d+$/.test(cells[q])) { nx = cells[q]; break; }
            if (/\u4ed3/.test(cells[q])) break;
          }
        }
      }
      cur = nx ? String(nx) : '';
    }
    out.push({i: i, code: code, attr: sz, size: size, wh: wh, cur: cur});
  });
  var lastWh = '';
  out.forEach(function (r) {
    if (r.wh) { lastWh = r.wh; } else { r.wh = lastWh; }
  });
  return JSON.stringify({rows: out});
})()
"""


def load_targets(store: str, codes: list, day: str) -> dict:
    p = SNAP / ("%s.json" % day)
    if not p.exists():
        raise SystemExit("没有当天快照：%s" % p)
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = ((d.get("stores") or {}).get(canon(store)) or {}).get("rows") or []
    tgt: dict = {}
    for r in rows:
        if codes and r["code"] not in codes:
            continue
        tgt.setdefault(r["code"], {"spu": r.get("spu") or "",
                                   "sizes": {}})
        tgt[r["code"]]["sizes"][str(r["size"]).upper()] = int(r.get("hxHalf") or 0)
    return tgt


async def open_dialog(port: int, store: str, spu: str) -> "B.OwnTab | None":
    tid = B.load_work_tab(store) or ""
    ids = [x.get("id") for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
           if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if tid not in ids:
        tid = await cdp_net.new_tab(port, LIST_URL, foreground=True)
        B.save_work_tab(store, tid)
        await asyncio.sleep(12)
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.any_page_ok = True
    await tab.open()
    await tab.ensure()
    await zreact.inject(tab.session)
    await B.page_ready(tab, 60)
    await B.close_overlays(tab)
    if await B.dialog_open(tab):
        await B.close_dialog(tab)
    await B.pick_query_type(tab, "SPU")
    await B.search(tab, spu)
    if not await B.dialog_open(tab):
        await B.open_stock_dialog(tab, spu)
        if not await B.wait_dialog(tab, 14):
            return None
    return tab


async def main() -> int:
    ap = argparse.ArgumentParser(description="弹窗内直接加减恒信仓库存（不开设置库存页）")
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--codes", default="")
    ap.add_argument("--all", action="store_true", help="按当天快照里所有 hxHalf>0 的货号")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    if a.all:
        # --all：把当天快照里所有 hxHalf>0 的货号都拿来（pipeline/定时任务用）
        _d = json.loads((SNAP / ("%s.json" % a.date)).read_text(encoding="utf-8"))
        _rows = ((_d.get("stores") or {}).get(canon(a.store)) or {}).get("rows") or []
        codes = sorted({r["code"] for r in _rows if int(r.get("hxHalf") or 0) > 0})
    tgt = load_targets(a.store, codes, a.date)
    if a.limit:
        tgt = dict(list(tgt.items())[:a.limit])
    port = _probe_port(WINDOWS[canon(a.store)]) if canon(a.store) in WINDOWS else 0
    if not port:
        print("!! 探不到窗口 %s" % a.store)
        return 3
    done = 0
    miss_streak = 0          # 连续「弹窗打不开」（用户 2026-11-23 C 项：卡顿要能自愈）
    for code, info in tgt.items():
        sizes = info["sizes"]
        spu = info["spu"]
        print("\n== %s（SPU %s）目标 %s" % (code, spu, "，".join("%s→%d" % kv for kv in sorted(sizes.items()))))
        tab = None
        for attempt in range(2):
            try:
                tab = await open_dialog(port, a.store, spu)
            except Exception as e:  # noqa: BLE001
                print("  ⚠ open_dialog 异常：%s" % str(e)[:120])
                tab = None
            if tab:
                break
            if attempt == 0:
                print("  ↻ 弹窗打不开 → 重载列表页后重试一次")
                try:
                    tid2 = B.load_work_tab(canon(a.store)) or ""
                    if tid2:
                        ws = cdp_net.http_json("http://127.0.0.1:%d/json/version" % port)["webSocketDebuggerUrl"]
                        await cdp_net._one_shot(ws, "Target.activateTarget", {"targetId": tid2})
                        await cdp_net._one_shot(ws, "Page.reload", {"targetId": tid2})
                        print("  已重载列表页 %s" % tid2[:8])
                except Exception as e:  # noqa: BLE001
                    print("  ⚠ 重载失败：%s" % str(e)[:100])
                await asyncio.sleep(10)
        if not tab:
            miss_streak += 1
            print("  ✗ 弹窗没打开（连续第 %d 个）" % miss_streak)
            if miss_streak >= 3:
                print("!! 连续 %d 个货号都打不开弹窗 → 本轮提前收工，避免空转" % miss_streak)
                break
            continue
        miss_streak = 0
        raw, _exc = await tab.session.eval(READ_DLG, timeout=30)
        print("  弹窗原始:", str(raw)[:700])
        d = json.loads(raw or "{}")
        if d.get("err"):
            print("  ✗ %s" % d["err"])
            await B.close_dialog(tab)
            continue
        ca3: dict = {}
        me: dict = {}
        other: dict = {}     # 要清零的仓（加州3仓 / kansas）→ {尺码: {仓名: 当前值}}
        _rows = d.get("rows") or []
        # 弹窗里 code 列可能缺失（有的模板只到颜色/尺码）：一个都没有时按「整弹窗」兜底
        _has_code = any(str(r.get("code") or "").strip() for r in _rows)
        for r in _rows:
            rc = str(r.get("code") or "").strip()
            if _has_code and rc and rc.upper() != code.upper():
                continue
            sz = str(r.get("size") or "").upper()
            wh = str(r.get("wh") or "")
            if not sz:
                continue
            try:
                v = int(str(r.get("cur") or "0") or 0)
            except ValueError:
                v = 0
            if HX in wh and "加州三" not in wh:
                me[sz] = {"attr": r.get("attr"), "cur": v}
            elif "加州三" in wh or ("加州3仓" in wh) or ("加州" in wh and HX not in wh) \
                    or "kansas" in wh.lower() or "堪萨斯" in wh:
                # 用户 2026-11-23：加州3仓 + Kansas仓 **一律清零**，恒信直接 = ⌈可用×80%⌉
                if v > 0:
                    other.setdefault(sz, {})[wh] = {"attr": r.get("attr"), "cur": v}
                ca3[sz] = v
        if not me:
            print("  ✗ 弹窗里没找到「恒信」行")
            await B.close_dialog(tab)
            continue
        plan = []
        for sz, half in sorted(sizes.items()):
            if sz not in me:
                print("   %-4s ✗ 弹窗里没有这个尺码" % sz)
                continue
            # ★ 口径（用户 2026-11-23）：恒信 = max(0, ⌈领星可用×80%⌉)，**不再扣加州已有**；
            #   加州3仓 / kansas 仓另按「减少当前值」清零。
            target = max(0, int(half))
            cur = me[sz]["cur"]
            delta = target - cur
            print("   %-4s 当前 %-5s → 目标 %-5s（可用×80%% 取整）%s"
                  % (sz, cur, target, "" if delta else "（不变）"))
            if delta:
                plan.append((sz, me[sz]["attr"], "增加" if delta > 0 else "减少", abs(delta)))
            for wh, info in (other.get(sz) or {}).items():
                want0 = max(0, int(info["cur"]))          # 目标就是 0
                if info["cur"] <= 0:
                    print("   %-4s %s 已是 0，跳过" % (sz, wh))
                    continue
                plan.append((sz, info["attr"], "减少", want0))
                print("   %-4s %s 清零（减少 %s）" % (sz, wh, want0))
        if not plan:
            print("  已达标 ✅")
            await B.close_dialog(tab)
            continue
        if not a.apply:
            print("  dry-run：将写 %d 个尺码" % len(plan))
            await B.close_dialog(tab)
            continue
        for sz, attr, direction, qty in plan:
            r = await B.set_row(tab, attr, direction, str(qty), code=code)
            print("   写 %-4s %s%d ← %s" % (sz, direction, qty, r))
        print("   提交:", await B.confirm(tab))
        await asyncio.sleep(3)
        # ⚠ 2026-11-21：提交后弹窗会关，重开后要等它**真的出现数据行**再读；
        #   早了会读到空（明明写成功却报 ❌）。
        if not await B.dialog_open(tab):
            await B.open_stock_dialog(tab, spu)
            await B.wait_dialog(tab, 14)
        raw2 = None
        for _ in range(8):
            await asyncio.sleep(2)
            raw2, _exc = await tab.session.eval(READ_DLG, timeout=30)
            try:
                _d = json.loads(raw2 or "{}")
            except Exception:  # noqa: BLE001
                _d = {}
            if _d.get("rows"):
                break
            if not await B.dialog_open(tab):
                await B.open_stock_dialog(tab, spu)
                await B.wait_dialog(tab, 14)
        d2 = json.loads(raw2 or "{}")
        got = {}
        for r in d2.get("rows") or []:
            if str(r.get("code") or "").upper() != code.upper():
                continue
            if HX in str(r.get("wh") or "") and "加州三" not in str(r.get("wh") or ""):
                got[str(r.get("size") or "").upper()] = r.get("cur")
        expect = {sz: max(0, int(sizes[sz])) for sz in sizes if sz in me}
        ok = all(str(got.get(sz)) == str(v) for sz, v in expect.items())
        print("   回读 %s → %s" % (got, "✅" if ok else "❌ 期望 %s" % expect))
        if ok:
            done += 1
        await B.close_dialog(tab)
    print("\n完成 %d 个货号（%s）" % (done, "已写后台" if a.apply else "dry-run 未写"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
