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
    """「款-变体·尺码」→ 7 天日均。

    两个来源合并（用户 2026-11-11 指正：领星明细里就有 7 天日均，不要只认库存跟踪）：
      ① stock-track.json（库存跟踪那 14 个款）
      ② linxing-stock.json（领星全仓明细，字段 daily；覆盖 203805 这类没进库存跟踪的款）
    同名键取较大值，避免某一侧缺数把有用的日均盖掉。
    """
    out = {}
    try:
        lx = json.loads((T.DATA / "linxing-stock.json").read_text(encoding="utf-8"))
        for x in lx.get("items") or []:
            name = (x.get("productName") or "").replace("BS2-", "").replace("BH-", "")
            if "-" not in name:
                continue
            base, size = name.rsplit("-", 1)
            d = float(x.get("daily") or 0)
            if d > out.get((base, size), 0):
                out[(base, size)] = d
    except Exception:  # noqa: BLE001
        pass
    try:
        track = json.loads((T.DATA / "stock-track.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    for src in (track.get("sources") or {}).values():
        for st in src.get("styles") or []:
            for v in st.get("variants") or []:
                for r in v.get("rows") or []:
                    k = ("%s-%s" % (st["code"], v["variant"]), r["size"])
                    d = float(r.get("daily") or 0)
                    if d > out.get(k, 0):
                        out[k] = d
    return out


def eta_map() -> dict:
    """(款号-套系, 尺码) → 最近一票在途的**预测上架日**（date）。

    口径（用户 2026-11-12 定 v3）：交期 L 用真实头程 ETA，不用固定 45 天。
    来源 shipment-track.json 的 skus[]（productName 形如 BS2-202504-5SetA-M）。
    只取「还没上架」且预测上架日 >= 今天的，取最早的那一票。
    """
    out: dict = {}
    try:
        st = json.loads((T.DATA / "shipment-track.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    today = date.today()
    for row in st.get("rows") or []:
        eta_s = str(row.get("predictedShelf") or "")[:10]
        if not eta_s:
            continue
        try:
            eta = datetime.strptime(eta_s, "%Y-%m-%d").date()
        except ValueError:
            continue
        if eta < today:                      # 已经到架/过期的票不算未来交期
            continue
        for s in row.get("skus") or []:
            name = str(s.get("productName") or s.get("style") or "")
            for pre in ("BS2-", "BH2-", "BH-", "BS-"):
                if name.startswith(pre):
                    name = name[len(pre):]
                    break
            if "-" not in name:
                continue
            base, size = name.rsplit("-", 1)
            k = (base, size)
            if k not in out or eta < out[k]:
                out[k] = eta
    return out


def v3_gate(store: str, code: str, size: str, dm: dict, etas: dict,
            policy: str = "v3") -> dict:
    """v3 判定（用户 2026-11-12 定稿）：用「在库天数 D0」决定要不要动，用「ETA」决定能不能开预售。

        D0 = 恒信可用 ÷ 7天日均            （仓里现成的货还能卖几天）
        L  = 最近一票在途的预测上架日 − 今天（无在途/无 ETA ⇒ L 未知）

        D0 ≥ L + 2 天                      → 不动（撑得到下次到货）
        D0 < L + 2 且 ETA ≤ 7 天           → 开预售（清零 + 开）
        D0 < L + 2 且 ETA > 7 天 / 无 ETA  → 只清零
        D0 < L + 2 且 无在途               → 只清零 + 拉急单
    交期未知时退回旧口径（可售 ≤3 天算缺货），但**只清零不开预售**（不确定就别锁 7 天）。
    """
    base = code.split("-", 1)[1] if "-" in code else code
    rows = [r for r in linxing_rows()
            if ("%s-%s" % (r["code"], r["variant"]) == base) and r["size"] == size]
    if not rows:
        return {"ok": False, "action": "忽略", "transit": None, "eta": "",
                "d0": None, "days": None,
                "reason": "领星总库存里没有这个款-尺码（店铺没这个货号）→ 忽略"}
    r = rows[0]
    avail = float(r.get("avail") or 0)
    transit = float(r.get("transit") or 0)
    daily = float(r.get("daily") or float(dm.get((base, size)) or 0))
    eta = etas.get((base, size))
    if daily <= 0:
        return {"ok": False, "action": "忽略", "transit": transit, "eta": str(eta or ""),
                "d0": None, "days": None, "reason": "没有 7 天日均 → 不判"}
    d0 = avail / daily
    L = (eta - date.today()).days if eta else None
    # ★ 用户 2026-11-12 追加的硬线：**可售 ≤1 天一律清零**（不能挂着继续卖）。
    #   这条不看交期、也不管有没有 ETA —— 已经只剩一天的货，挂着就是等着超卖。
    #   能开预售的（在途 7 天内到）顺带开；开不了的（ETA>7 / 无在途）只清零。
    if d0 <= 1.0:
        if transit > 0 and L is not None and L <= 7:
            return {"ok": True, "action": "开预售", "transit": transit, "eta": str(eta),
                    "d0": d0, "days": d0,
                    "reason": "可售 %.2f 天 ≤1 天（硬线清零），在途 %d 件、%s 到（≤7天）→ 清零+开预售"
                              % (d0, transit, eta)}
        why = ("交期 %d 天 > 7 天" % L) if L is not None else ("无在途" if transit <= 0 else "头程没有预测上架日")
        return {"ok": False, "action": "只清零", "transit": transit, "eta": str(eta or ""),
                "d0": d0, "days": d0,
                "reason": "可售 %.2f 天 ≤1 天（硬线清零）；%s → 只清零、不开预售" % (d0, why)}
    if L is not None and d0 >= L + 2:
        return {"ok": False, "action": "不动", "transit": transit, "eta": str(eta),
                "d0": d0, "days": d0,
                "reason": "在库 %.1f 天 ≥ 交期 %d 天 + 2 缓冲 → 撑得到到货，不动" % (d0, L)}
    if transit <= 0:
        return {"ok": False, "action": "只清零", "transit": 0, "eta": str(eta or ""),
                "d0": d0, "days": d0,
                "reason": "在库 %.1f 天但无在途 → 清零止损 + 拉急单" % d0}
    if L is not None and L <= 7:
        return {"ok": True, "action": "开预售", "transit": transit, "eta": str(eta),
                "d0": d0, "days": d0,
                "reason": "在库 %.1f 天 < 交期 %d 天，在途 %d 件、%s 到（≤7天）→ 清零+开预售"
                          % (d0, L, transit, eta)}
    why_eta = ("交期 %d 天 > 7 天" % L) if L is not None else "头程没有预测上架日"
    return {"ok": False, "action": "只清零", "transit": transit, "eta": str(eta or ""),
            "d0": d0, "days": d0,
            "reason": "在库 %.1f 天 < 交期，且%s → 清零不开预售（开了也履约不了）" % (d0, why_eta)}


def v3_presale_qty(daily: float, transit: float) -> int:
    """v3 在途预售库存：锁 7 天窗口，但不超过在途件数，最低 5 件。"""
    base = max(5, math.ceil(daily * 7)) if daily > 0 else DEFAULT_PRESALE_QTY
    if transit and transit > 0:
        base = min(base, int(transit))
    return max(5, int(base))


def presale_qty(code: str, size: str, dm: dict, mode: str = "auto", fixed: int = DEFAULT_PRESALE_QTY,
                transit: int | None = None):
    """在途预售库存（用户 2026-11-09 定稿口径）。

    设定值 = 有 7 天日均 → ceil(日均 × 7)；无日均 → 默认 20
    设 T = 该尺码的在途件数：
      · T > 设定值 + 50      → 加 设定值
      · T == 设定值          → 加 ceil(日均 × 3)
      · 设定值 < T ≤ 设定值+50（稍微多一点）→ 加 ceil(日均 × 3)
      · T < 设定值           → 加 ceil(日均 × 3)
    无日时时无法算「日均 × 3」→ 退化为设定值的 1/3（向上取整，至少 1），并在说明里写清楚。
    """
    base = code.split("-", 1)[1] if "-" in code else code
    d = dm.get((base, size)) or 0
    # ⚠ 5 参数调用时第 4 个位置参数是 mode；历史脚本可能直接传 daily，这里兼容
    if isinstance(mode, (int, float)) and not isinstance(mode, bool):
        d, mode = float(mode), "auto"
    if mode != "auto":
        base_q, why = int(fixed), "指定 %d" % int(fixed)
    elif d <= 0:
        base_q, why = DEFAULT_PRESALE_QTY, "无 7 天日均 → 默认 %d" % DEFAULT_PRESALE_QTY
    else:
        base_q = max(1, math.ceil(d * 7))
        why = "日均 %.4f × 7 = %.2f → 设定 %d" % (d, d * 7, base_q)
    if transit is None:
        return base_q, why
    three = max(1, math.ceil(d * 3)) if d > 0 else max(1, math.ceil(base_q / 3))
    day3_why = ("日均 %.4f × 3 = %.2f → %d" % (d, d * 3, three)) if d > 0 else ("无日均 ⇒ 设定值 1/3 = %d" % three)
    if transit > base_q + 50:
        return base_q, why + "；在途 %d > 设定+50 ⇒ 加设定值 %d" % (transit, base_q)
    if transit > base_q:
        return three, why + "；在途 %d 只比设定多 %d（≤50）⇒ 加 3 天量 %d（%s）" % (
            transit, transit - base_q, three, day3_why)
    if transit == base_q:
        return three, why + "；在途 == 设定 ⇒ 加 3 天量 %d（%s）" % (three, day3_why)
    return three, why + "；在途 %d < 设定 ⇒ 加 3 天量 %d（%s）" % (transit, three, day3_why)


# ------------------------------------------------------------------ 取工作页
def pick_work_page(windows_store: str) -> tuple[int, str]:
    """复用窗口里已有的商品列表页（优先），否则新开一张。返回 (port, tid, opened)。"""
    port = probe_port(T.WINDOWS[windows_store])
    if not port:
        return 0, "", False
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    want = B.load_work_tab(windows_store)
    if want and any((p.get("id") or p.get("targetId")) == want for p in pages):
        return port, want, False
    return port, "", False


async def open_tab(port: int, tid: str, opened: bool, store: str = "") -> "B.OwnTab":
    if not tid:
        tid = B.load_work_tab(store) if store else ""
        tid = await cdp_net.new_tab(port, B.URL, foreground=True)  # noqa: tab-safety 前台必需
        opened = True
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.any_page_ok = True
    tab.is_new = opened
    tab.store = store
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
def linxing_rows() -> list:
    """领星总库存面板（linxing-stock.json）逐尺码：不是只跟 14 个款，全产品都在。

    用户 2026-11-11：判断「可售 ≤3 天」就去看库存跟踪 → 领星总库存这个面板。
    """
    try:
        d = json.loads((T.DATA / "linxing-stock.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for x in d.get("items") or []:
        name = (x.get("productName") or "").replace("BS2-", "").replace("BH-", "")
        if "-" not in name:
            continue
        base, size = name.rsplit("-", 1)
        avail = float(x.get("avail") or 0)
        daily = float(x.get("daily") or 0)
        days = (avail / daily) if daily > 0 else None
        out.append({"name": x.get("productName"), "code": x.get("code"), "variant": x.get("variant"),
                    "size": size, "avail": avail, "total": float(x.get("total") or 0),
                    "transit": float(x.get("transit") or 0), "daily": daily, "sellDays": days})
    return out


def stocktrack_gate(store: str, code: str, size: str, dm: dict, near_days: int = 7) -> dict:
    """开预售的前提闸门（用户 2026-11-11 定：以「领星总库存」面板为准）。

    ① 该「款-变体·尺码」在领星明细里，且 **可售天数 = 可用 ÷ 7 天日均 ≤ 3**
    ② 在途 > 0（有货在路上）。头程「预测上架」能算出来时要求 ≤7 天；算不出来只提示。
    """
    base = code.split("-", 1)[1] if "-" in code else code
    rows = [r for r in linxing_rows()
            if ("%s-%s" % (r["code"], r["variant"]) == base) and r["size"] == size]
    if not rows:
        return {"ok": False, "transit": None, "eta": "", "days": None,
                "reason": "领星总库存里没有这个款-尺码（店铺没这个货号）→ 忽略"}
    r = rows[0]
    days, transit = r.get("sellDays"), r.get("transit") or 0
    if days is None or days > 3:
        return {"ok": False, "transit": transit, "eta": "", "days": days,
                "reason": "可售天数 %s > 3 → 不需要开预售" % ("无日均" if days is None else round(days, 1))}
    if transit <= 0:
        return {"ok": False, "transit": 0, "eta": "", "days": days,
                "reason": "可售 %.1f 天但无在途 → 无货可预售" % days}
    return {"ok": True, "transit": transit, "eta": "", "days": days,
            "reason": "领星可售 %.1f 天 ≤ 3 且在途 %d 件 → 可以开预售" % (days, transit)}


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
    tab = await open_tab(port, tid, opened, store)
    print("工作页:", str(tab.tid)[:16], "(复用)" if not opened else "(新开)")
    code_guess = args.code or "%s-SPU%s" % (store, args.spu)
    try:
        if not await step_prepare(tab, args.spu, store):
            print("!! 查询没命中该 SPU")
            return 4
        if not await step_open_dialog(tab, args.spu):
            print("!! 修改库存弹窗没打开")
            return 4
        # ⚠ 同一 SPU 可能挂多个 SKC/货号（如 BHY 203805 的 BHC + 3BHL），
        #   属性集两块重名 ⇒ 一律按「货号 + 尺码」定位行（用户 2026-11-10）
        blocks = await B.skc_blocks(tab)
        cur = {}
        for blk in blocks:
            for r in blk.get("rows") or []:
                # ⚠ 2026-11-12 修：target_code 是 **大写**（BHY-202504-5SETA），
                #   而弹窗里的货号是原始大小写（BHY-202504-5SetA）。
                #   之前 cur 用原样 key 存、用大写去取 → 一律取不到 → stock_plan 空 →
                #   「③ 库存改动」整步被跳过（清零没做），只剩开预售。
                cur[((blk.get("code") or "").upper(), r.get("attr"))] = int(r.get("cur") or 0)
        print("   弹窗 SKC:", [(blk.get("code"), len(blk.get("rows") or [])) for blk in blocks])
        # 目标货号：--code 指定哪个货号（同 SPU 多 SKC 时关键），大小写不敏感
        target_code = (getattr(args, "code", "") or code_guess or "").upper()
        attrs_of_size = {}
        for blk in blocks:
            if target_code and (blk.get("code") or "").upper() != target_code:
                continue
            for r in blk.get("rows") or []:
                for s in ("4XL", "3XL", "XXL", "XL", "XS", "S", "M", "L"):
                    if (r.get("attr") or "").endswith("-" + s):
                        attrs_of_size.setdefault(s, r["attr"])
                        break
        print("   目标货号:", target_code or "(未指定)", "→ 命中尺码:", {k: v for k, v in attrs_of_size.items()})

        stock_plan, qty_of = {}, {}
        allowed, gate, auto_zero = [], {}, []
        policy = getattr(args, "policy", "v3")
        etas = eta_map() if policy == "v3" else {}
        print("   预售闸门（%s）：" % ("v3：在库天数 vs 头程 ETA" if policy == "v3"
                                     else "旧口径：可售≤3天 + 在途>0"))
        for sz in sizes:
            g = (v3_gate(store, code_guess, sz, dm, etas) if policy == "v3"
                 else stocktrack_gate(store, code_guess, sz, dm))
            gate[sz] = g
            if policy == "v3":
                print("   %-3s 在库%-6s 交期L=%-5s ETA=%-11s 在途=%-5s → [%s] %s"
                      % (sz, ("%.1f天" % g["d0"]) if g.get("d0") is not None else "—",
                         ((g["eta"] and (date.fromisoformat(g["eta"]) - date.today()).days) or "—")
                         if g.get("eta") else "—",
                         g.get("eta") or "-", g.get("transit"), g.get("action") or "-", g["reason"]))
            else:
                print("   %-3s 可售天数=%-4s 在途=%-5s 预测上架=%-10s → %s"
                      % (sz, g.get("sellDays"), g.get("transit"), g.get("eta") or "-", g["reason"]))
            if g["ok"]:
                allowed.append(sz)
            elif g.get("action") == "只清零":
                auto_zero.append(sz)                # v3：交期不匹配 → 只清库存，不锁预售
        if sizes and not allowed:
            print("   !! 没有一个尺码满足开预售前提；本次只按 --stock 处理库存")
        sizes = allowed
        zero_extra = [x.strip().upper() for x in (getattr(args, "zero_sizes", "") or "").split(",") if x.strip()]
        zero_extra = sorted(set(zero_extra) | set(auto_zero))
        for s in sorted(set(sizes) | set(zero_extra)):
            attr = attrs_of_size.get(s)
            if not attr:
                continue
            c = cur.get((target_code, attr), 0)
            if args.stock == "add":
                stock_plan[attr] = ("增加", int(args.qty))
            elif c > 0:
                stock_plan[attr] = ("减少", c)          # 开预售前必须清零
            if s not in zero_extra:
                g0 = (gate.get(s) or {})
                if policy == "v3":
                    _base = code_guess.split("-", 1)[1] if "-" in code_guess else code_guess
                    qty = v3_presale_qty(float(dm.get((_base, s)) or 0),
                                         float(g0.get("transit") or 0))
                    qty_of[s] = (qty, "v3：锁 7 天量，且 ≤ 在途 %s" % (g0.get("transit") or "-"))
                else:
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
    ap.add_argument("--policy", choices=["v3", "v2"], default="v3",
                    help="v3=在库天数 vs 头程 ETA（默认，2026-11-12 用户定）；v2=旧口径 可售≤3天")
    ap.add_argument("--close", action="store_true", help="跑完关掉本次新开的页（默认保留复用）")
    a = ap.parse_args()
    return asyncio.run(run(a))


if __name__ == "__main__":
    raise SystemExit(main())
