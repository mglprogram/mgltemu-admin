# -*- coding: utf-8 -*-
"""TEMU「修改库存 + 开预售」pipeline（BEL / BHY 商品列表）。

把用户 2026-11-09 在 SPU 43933794478 上带出来的完整操作固化成一条命令：

    ① 按 SPU 查询（条件切 SPU → 填 SPU → 查询 → 只剩该款尺码行）
    ② 打开「修改库存」弹窗
    ③ 清零（需要开预售的尺码：当前库存 → 0，`减少 = 当前值`；仅 dry-run 时跳过）
    ④ 横滚到最右（「在途库存预售 / 补货日期 / 在途预售库存」列默认不可见）
    ⑤ 逐行点「已关闭」开关（外层 div 的 React onClick）→ 风险框「确认」
    ⑥ 选补货日期：点日期框 → 日历里选**当天之后最远可点**的日期 → 回读校验
    ⑦ 填在途预售库存：有 7 天日均 = ceil(日均 × 7)；没有日均 = 20
    ⑧ 同款多尺码全部填完 → 只点一次弹窗「确认」→ 风险框再「确认」
    ⑨ 重开弹窗逐行回读核对（服务端值）—— 不一致就报 FAILED

    python scripts/goods/stock_presale_pipeline.py --spu 43933794478 --sizes S,M,L,XL
    python scripts/goods/stock_presale_pipeline.py --spu 43933794478 --sizes S,M,L,XL --apply
    python scripts/goods/stock_presale_pipeline.py --spu 43933794478 --sizes M --stock add --qty 20
    python scripts/goods/stock_presale_pipeline.py --store BHY --spu 9590192318 --sizes S

口径（唯一事实来源）
--------------------
· 加库存：`--stock add --qty N` 每行「增加 N」；`--stock set --qty N` 设为 N（自动算增减）
· 预售：`--sizes` 里的尺码全部开预售（先清零，再开）
· 在途预售库存：`--presale-qty auto`（默认）= 有日均 ceil(日均×7)，无日均 20
· 窗口：BEL → TM-BEL-MGL，BHY → TM-BHY-YJF（紫鸟店铺窗口）
· **复用同一张工作页**：找到窗口里已有的商品列表页就用它，没有才新开；
  跑完默认**不关**（用户 2026-11-09：没必要重复开页），`--close` 才关
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
import bel_stock_set as B  # noqa: E402
import bel_stock_batch as Batch  # noqa: E402
import topup_by_sell_days as T  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

DEFAULT_PRESALE_QTY = 20


# ------------------------------------------------------------------ 数据口径
def daily_map() -> dict:
    """「款-变体·尺码」→ 7 天日均（库存跟踪 stock-track.json 的 daily）。"""
    try:
        track = json.loads((T.DATA / "stock-track.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for src in (track.get("sources") or {}).values():
        for st in src.get("styles") or []:
            for v in st.get("variants") or []:
                for r in v.get("rows") or []:
                    k = ("%s-%s" % (st["code"], v["variant"]), r["size"])
                    d = float(r.get("daily") or 0)
                    if d > out.get(k, 0):
                        out[k] = d
    return out


def presale_qty(code: str, size: str, dm: dict, mode: str = "auto", fixed: int = DEFAULT_PRESALE_QTY,
                transit: int | None = None):
    """在途预售库存（用户 2026-11-09 定）：

    · auto       = 有 7 天日均 → ceil(日均 × 7)；无日均 → 默认 20
    · 在途兜底   = 该尺码的**在途件数 < 设定值**时，只加设定值的 1/3（向上取整，至少 1，且不超过在途件数）
    """
    if mode != "auto":
        want, why = int(fixed), "指定 %d" % int(fixed)
    else:
        base = code.split("-", 1)[1] if "-" in code else code
        d = dm.get((base, size)) or 0
        if d <= 0:
            want, why = DEFAULT_PRESALE_QTY, "无 7 天日均 → 默认 %d" % DEFAULT_PRESALE_QTY
        else:
            want = max(1, math.ceil(d * 7))
            why = "日均 %.4f × 7 = %.2f → %d" % (d, d * 7, want)
    if transit is not None and transit >= 0 and transit < want:
        q = max(1, math.ceil(want / 3))
        why += "；在途 %d < 设定 %d ⇒ 只加 1/3 = %d" % (transit, want, q)
        want = q
    return want, why


# ------------------------------------------------------------------ 预售闸门
def stocktrack_gate(store: str, code: str, size: str, dm: dict, near_days: int = 7) -> dict:
    """开预售的**前提闸门**（用户 2026-11-09 口径）。

    ① 该款·尺码必须出现在「库存跟踪 → 可售 ≤3 天」筛选里（stock-track 的 sellDays ≤ 3
       且 7 天日均 > 0）；不在里面 = 不该开预售。
    ② 它的「在途」对应到头程跟踪的「预测/上架」日期：当天距上架日 **≤7 天** → 可以开预售；
       **>7 天** → 必须先清零库存（等货真的快到了再开）。
    返回 {ok, sellDays, transit, eta, days, reason}。
    """
    import presale_candidates as P
    base = code.split("-", 1)[1] if "-" in code else code
    daily = dm.get((base, size)) or 0
    rows = P.build(3)                          # 库存跟踪「可售 ≤3 天」那一批
    hit = next((r for r in rows if r["store"] == store and r["code"].endswith(base) and r["size"] == size), None)
    if daily <= 0 or not hit:
        return {"ok": False, "sellDays": hit and hit.get("days"), "transit": hit and hit.get("inboundQty"),
                "eta": "", "days": None, "reason": "不在「可售≤3天」筛选中（无销量/无在途）→ 不开预售"}
    days = hit.get("days")
    return {"ok": days is not None and 0 <= days <= near_days,
            "sellDays": hit.get("days"), "transit": hit.get("inboundQty"), "eta": hit.get("eta"),
            "days": days,
            "reason": ("在途预计上架 %s（%d 天 ≤ %d）→ 可以开预售" % (hit.get("eta"), days, near_days))
                      if days is not None and 0 <= days <= near_days
                      else ("在途预计上架 %s（%s 天 > %d）→ 不满足，改走「只清零库存」"
                            % (hit.get("eta"), days, near_days))}


# ------------------------------------------------------------------ 取工作页
def pick_work_page(windows_store: str) -> tuple[int, str]:
    """复用窗口里已有的商品列表页（优先），否则新开一张。返回 (port, tid, opened)。"""
    port = probe_port(T.WINDOWS[windows_store])
    if not port:
        return 0, "", False
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if pages:
        pg = pages[-1]
        return port, pg.get("id") or pg.get("targetId"), False
    return port, "", False


async def open_tab(port: int, tid: str, opened: bool) -> "B.OwnTab":
    if not tid:
        tid = await cdp_net.new_tab(port, B.URL, foreground=True)  # noqa: tab-safety 前台必需
        opened = True
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.any_page_ok = True
    tab.is_new = opened
    return tab


# ------------------------------------------------------------------ 各步
async def step_prepare(tab, spu: str, store: str) -> bool:
    print("① 按 SPU 查询：%s" % spu)
    if not await tab.open():
        print("   !! 工作页打不开")
        return False
    for _ in range(4):
        try:
            await zreact.inject(tab.session)
            break
        except Exception:  # noqa: BLE001
            print("   注入失败，重连…")
            await asyncio.sleep(3)
            await tab.open()
    if not await B.page_ready(tab, 60):
        print("   !! 商品列表 60s 内没加载出来")
        return False
    print("   条件→SPU:", await B.pick_query_type(tab, "SPU"))
    st = await B.search(tab, spu)
    print("   结果:", st)
    return bool(st.get("hit"))


async def step_open_dialog(tab, spu: str) -> bool:
    print("② 打开「修改库存」")
    if await B.dialog_open(tab):
        return True
    how = await B.open_stock_dialog(tab, spu)
    print("   打开方式:", how)
    return await B.dialog_open(tab)


async def step_stock(tab, plan: dict, mode: str, value, apply: bool) -> dict:
    """③ 改库存（plan: {attr: (方向, 数量)}）。返回 {attr: 期望值}。"""
    d = await Batch.rows_of(tab)
    cur = {r["attr"]: int(r["cur"] or 0) for r in d.get("rows", [])}
    if not plan:
        return {}
    print("③ 库存改动：")
    want = {}
    for attr, (direction, qty) in plan.items():
        c = cur.get(attr, 0)
        new = c + qty if direction == "增加" else max(0, c - qty)
        want[attr] = new
        print("   %-10s 当前 %-4s %s %-4s → %s" % (attr, c, direction, qty, new))
    if not apply:
        print("   (dry-run 不填)")
        return want
    print("   逐行填写：", await Batch.fill_all_rows(tab, plan))
    d2 = await Batch.rows_of(tab)
    bad = [(r["attr"], r["after"], want[r["attr"]]) for r in d2.get("rows", [])
           if r["attr"] in want and r["after"] != str(want[r["attr"]])]
    if bad:
        raise RuntimeError("提交前回读不一致：%s" % bad)
    return want


async def step_presale(tab, sizes: list, attrs_of_size: dict, qty_of: dict, apply: bool) -> dict:
    """④~⑦ 横滚 → 逐行开预售 → 日期 → 在途量。返回 {attr: (日期, 在途量)}。"""
    print("④ 横向滚到最右")
    await T.scroll_right(tab)
    await asyncio.sleep(0.8)
    done = {}
    for size in sizes:
        attr = attrs_of_size.get(size)
        if not attr:
            print("   尺码 %s 不在弹窗里，跳过" % size)
            continue
        qty, why = qty_of[size]
        print("⑤ %-10s 开预售（在途量 %s：%s）" % (attr, qty, why))
        if not apply:
            done[attr] = ("(dry-run)", qty)
            continue
        # 行滚到可视区中间，避免被底部确认框压住
        await B.ev(tab, B.JS_DIALOG + r"""
if (!b.length) return 'no';
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (tr) tr.scrollIntoView({block: 'center'});
return 'ok';""" % json.dumps(attr))
        await asyncio.sleep(0.4)
        r = await T.enable_presale(tab, attr, int(qty))
        print("   →", r)
        date_v = await T.date_value(tab, attr)
        if not date_v:
            print("   ⚠ 日期为空，提交可能被拒")
        done[attr] = (date_v, int(qty))
    return done


async def step_submit(tab, apply: bool) -> bool:
    print("⑧ 提交（一次确认 + 风险框确认）")
    if not apply:
        print("   (dry-run：不提交)")
        return False
    r = await B.confirm(tab)
    print("   ", r)
    await asyncio.sleep(3)
    print("   弹窗还开?", await B.dialog_open(tab))
    return not await B.dialog_open(tab)


async def step_verify(tab, spu: str, want_stock: dict, want_presale: dict) -> bool:
    print("⑨ 重开弹窗核对服务端值")
    await asyncio.sleep(2)
    if not await B.dialog_open(tab):
        await B.open_stock_dialog(tab, spu)
        await asyncio.sleep(3)
    ok = True
    for attr, exp in want_stock.items():
        cur = await B.ev(tab, B.JS_DIALOG + r"""
if (!b.length) return '';
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (!tr) return '';
var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
for (var i = 0; i < cells.length; i++) if (/^\d+$/.test(cells[i])) return cells[i];
return '';""" % json.dumps(attr))
        good = str(cur) == str(exp)
        ok = ok and good
        print("   库存 %-10s 服务端=%-4s 期望=%-4s %s" % (attr, cur, exp, "✅" if good else "❌"))
    for attr, (wdate, wqty) in want_presale.items():
        st = await B.ev(tab, B.JS_DIALOG + r"""
if (!b.length) return '{}';
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (!tr) return '{}';
var tog = Array.prototype.slice.call(tr.querySelectorAll('div')).filter(function (e) {
  var p = window.__zr.findProps(e); return p && typeof p.onClick === 'function' && /已/.test(e.innerText||''); })[0];
var date = Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) { return /^\d{4}-\d{2}-\d{2}$/.test(i.value||''); })[0];
var q = Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) { return (i.placeholder||'').indexOf('5000')>=0; })[0];
return JSON.stringify({state: tog ? (tog.innerText||'').trim() : '', date: date ? date.value : '', qty: q ? q.value : ''});"""
                             % json.dumps(attr))
        try:
            d = json.loads(st or "{}")
        except Exception:  # noqa: BLE001
            d = {}
        good = (d.get("state") == "已开启" and str(d.get("qty")) == str(wqty))
        ok = ok and good
        print("   预售 %-10s 开关=%-4s 日期=%-10s 在途量=%-4s 期望 %s/%s %s"
              % (attr, d.get("state"), d.get("date"), d.get("qty"), wdate, wqty, "✅" if good else "❌"))
    return ok


# ------------------------------------------------------------------ 清单（先出清单，再执行）
def build_report(dm: dict) -> list:
    """汇总「可售 ≤3 天」的款-尺码，按头程预测上架日分成两类：

    · 开预售：在途预计上架 ≤7 天（库存可以留/清零后开预售）
    · 只清零：在途预计上架 >7 天（或没有在途）→ 不满足开预售前提，只把库存清零
    另外附上「在售款明细」里的 SPU 与 TEMU 当前库存，方便直接执行。
    """
    import presale_candidates as P
    goods = P.load("goods.json")
    stock_of, spu_of = {}, {}
    for st, s0 in (goods.get("stores") or {}).items():
        for it in s0.get("items") or []:
            code = it.get("code") or ""
            spu_of[(st, code)] = it.get("spuId")
            for sz, info in (it.get("sizes") or {}).items():
                pass
    out = []
    for r in P.build(3):                       # 库存跟踪「可售 ≤3 天」那一批
        days = r.get("days")
        # ⚠ 上架日已过期（负数天）= 早该到货还没上架，属于异常单，不算「7 天内」也不是在途
        if days is None or days < 0:
            act = "忽略(上架日已过期/无日期，异常单)"
        elif days <= 7:
            act = "开预售"
        else:
            act = "只清零"
        out.append({**r, "action": act, "stock": r.get("temuStock")})
    return out


def print_report(rows: list) -> None:
    if not rows:
        print("库存跟踪里「可售 ≤3 天」且有在途的款-尺码：0 条")
        return
    print("%-24s %-5s %-12s %-5s %-11s %-6s %-6s %s"
          % ("货号", "尺码", "SPU", "天数", "预测上架", "TEMU", "在途", "动作"))
    for r in rows:
        print("%-24s %-5s %-12s %-5s %-11s %-6s %-6s %s"
              % (r["code"], r["size"], r.get("spu") or "-", r["days"], r.get("eta") or "-",
                 r.get("temuStock"), r.get("inboundQty"), r["action"]))
    n1 = sum(1 for r in rows if r["action"] == "开预售")
    print("共 %d 条：开预售 %d / 只清零 %d" % (len(rows), n1, len(rows) - n1))


# ------------------------------------------------------------------ 主流程
async def run(args) -> int:
    store = args.store.upper()
    sizes = [s.strip().upper() for s in args.sizes.split(",") if s.strip()] if args.sizes else []
    dm = daily_map()
    # ⓪ 先出清单：所有相关款-尺码汇总成「只清零 / 开预售」两份，再决定进哪些款
    if args.plan_only or args.report:
        rows = build_report(dm)
        print_report(rows)
        if args.plan_only:
            return 0
        targets = {(r["store"], r["spu"]) for r in rows
                   if r["action"] in ("开预售", "只清零") and r["spu"]}
        if not targets:
            print("清单里没有要动库存/开预售的款")
            return 0
        rc = 0
        for st, spu in sorted(targets):
            g = [r for r in rows if r["store"] == st and r["spu"] == spu and r["action"] in ("开预售", "只清零")]
            sizes = ",".join(sorted({r["size"] for r in g if r["action"] == "开预售"}))
            zero_only = sorted({r["size"] for r in g if r["action"] == "只清零"})
            if zero_only:
                sub_sizes = sizes
                sizes = sub_sizes  # 只清零的尺码也走 --stock zero，但不进预售

            sub = argparse.Namespace(**vars(args))
            sub.store, sub.spu, sub.code, sub.sizes = st, spu, g[0]["code"], sizes
            sub.qty = g[0].get("stock") or 0
            sub.zero_sizes = ",".join(sorted({r["size"] for r in g if r["action"] == "只清零"}))
            sub.stock = "zero"
            rc = max(rc, await run(sub))
        return rc
    port, tid, opened = pick_work_page(store)
    if not port:
        print("!! 探不到 %s 窗口（%s）" % (store, T.WINDOWS.get(store)))
        return 3
    tab = await open_tab(port, tid, opened)
    print("工作页:", str(tab.tid)[:16], "(复用)" if not opened else "(新开)")
    code_guess = args.code or "%s-SPU%s" % (store, args.spu)
    try:
        if not await step_prepare(tab, args.spu, store):
            print("!! 查询没命中该 SPU")
            return 4
        if not await step_open_dialog(tab, args.spu):
            print("!! 修改库存弹窗没打开")
            return 4
        d = await Batch.rows_of(tab)
        cur = {r["attr"]: int(r["cur"] or 0) for r in d.get("rows", [])}
        attrs_of_size = {}
        for r in d.get("rows", []):
            for s in ("4XL", "3XL", "XXL", "XL", "XS", "S", "M", "L"):
                if r["attr"].endswith("-" + s):
                    attrs_of_size.setdefault(s, r["attr"])
                    break
        print("   弹窗行:", {r["attr"]: r["cur"] for r in d.get("rows", [])})

        stock_plan, qty_of = {}, {}
        allowed, gate = [], {}
        print("   预售闸门（库存跟踪 可售≤3天 + 头程 预测上架≤7天）：")
        for sz in sizes:
            g = stocktrack_gate(store, code_guess, sz, dm)
            gate[sz] = g
            print("   %-3s 可售天数=%-4s 在途=%-5s 预测上架=%-10s → %s"
                  % (sz, g["sellDays"], g["transit"], g["eta"] or "-", g["reason"]))
            if g["ok"]:
                allowed.append(sz)
        if sizes and not allowed:
            print("   !! 没有一个尺码满足开预售前提；本次只按 --stock 处理库存")
        sizes = allowed
        zero_extra = [x.strip().upper() for x in (getattr(args, "zero_sizes", "") or "").split(",") if x.strip()]
        for s in sorted(set(sizes) | set(zero_extra)):
            attr = attrs_of_size.get(s)
            if not attr:
                continue
            want = args.qty if args.stock == "add" else 0
            c = cur.get(attr, 0)
            if args.stock == "add":
                stock_plan[attr] = ("增加", int(args.qty))
            elif c > 0:
                stock_plan[attr] = ("减少", c)          # 开预售前必须清零
            if s not in zero_extra:
                g0 = (gate.get(s) or {})
                qty_of[s] = presale_qty(code_guess, s, dm, args.presale_qty_mode, args.presale_qty,
                                        transit=g0.get("transit"))
        want_stock = await step_stock(tab, stock_plan, args.stock, args.qty, args.apply)
        presale_want = await step_presale(tab, sizes, attrs_of_size, qty_of, args.apply)
        closed = await step_submit(tab, args.apply)
        if not args.apply:
            await B.ev(tab, B.JS_DIALOG + r"""
if (!b.length) return 'no-modal';
var bs = Array.prototype.slice.call(b[0].querySelectorAll('button')).filter(function (x) {
  return (x.innerText || '').trim() === '取消' && x.getClientRects().length; });
return bs.length ? window.__zr.call(bs[bs.length-1], 'onClick') : 'no-cancel-in-modal';""")
            print("   (dry-run 已取消弹窗)")
            return 0
        if not closed:
            print("!! 提交后弹窗没关，可能被拒或还有确认框没点")
            return 6
        ok = await step_verify(tab, args.spu, want_stock, presale_want)
        await B.ev(tab, B.JS_DIALOG + r"""
if (!b.length) return 'no-modal';
var bs = Array.prototype.slice.call(b[0].querySelectorAll('button')).filter(function (x) {
  return (x.innerText || '').trim() === '取消' && x.getClientRects().length; });
return bs.length ? window.__zr.call(bs[bs.length-1], 'onClick') : 'no-cancel-in-modal';""")
        print("\n结论:", "✅ 全部生效" if ok else "❌ 有行与服务端不一致")
        return 0 if ok else 7
    finally:
        if getattr(args, "close", False) and tab.is_new:
            await tab.close()
            print("已关掉本次新开的页")
        else:
            await tab.keep_tab()


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 修改库存 + 开预售 pipeline（默认 dry-run）")
    ap.add_argument("--store", default="BEL", choices=["BEL", "BHY"])
    ap.add_argument("--spu", default="", help="SPU ID（--report 模式下可省）")
    ap.add_argument("--sizes", default="", help="要开预售的尺码，逗号分隔，如 S,M,L,XL")
    ap.add_argument("--zero-sizes", default="", help="只清零、不开预售的尺码（清单里「只清零」那份）")
    ap.add_argument("--code", default="", help="款号（查日均用，如 BEL-T1036-3B）；不给则按 SPU 查不到日均→默认 20")
    ap.add_argument("--stock", choices=["zero", "add", "set", "none"], default="zero",
                    help="库存动作：zero=清零（开预售必需，默认）/add/set/none")
    ap.add_argument("--qty", type=int, default=20, help="--stock add/set 的数量（默认 20）")
    ap.add_argument("--presale-qty-mode", choices=["auto", "fixed"], default="auto",
                    help="auto=有日均 ceil(日均×7)，无日均默认 20")
    ap.add_argument("--presale-qty", type=int, default=DEFAULT_PRESALE_QTY)
    ap.add_argument("--report", action="store_true",
                    help="先出清单并按清单逐款执行（不指定 --spu 时用这个）")
    ap.add_argument("--plan-only", action="store_true", help="只打印清单，不动浏览器")
    ap.add_argument("--apply", action="store_true", help="真提交")
    ap.add_argument("--close", action="store_true", help="跑完关掉本次新开的页（默认保留复用）")
    a = ap.parse_args()
    return asyncio.run(run(a))


if __name__ == "__main__":
    raise SystemExit(main())
