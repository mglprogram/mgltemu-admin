# -*- coding: utf-8 -*-
"""按「可售天数 ≤ N」自动补库存 + 顺带开在途预售（TEMU 在售款）。

链路（用户 2026-11-09 定）
-------------------------
① 库存数据：stock-track.json 的尺码行（sellDays ≤ --days 且 有日销）
   —— 对应面板「库存跟踪 → 可售 ≤ 3 天」筛选按钮点出来的那批行
② SPU：goods.json（在售款明细）按「店铺 + 款号」取 SPU ID
③ 商品列表（BEL / BHY 窗口，自己开页、跑完关）→ 按 SPU 查询 → 修改库存：
   ③1 该尺码「库存增减」增加 = ⌈日销 × 天数⌉（--stock-mode add 时不分差）
   ③2 若该尺码有**未来 7 天内到货的在途**（shipment-track.json 预测上架日）
      ⇒ 把该行「在途库存预售」开关打开 → 「确认开启在途库存预售吗」→ 确认；
        补货日期选**当天之后最远**的日期；在途预售库存 = 在途件数
   ③3 点弹窗「确认」→ 风险框再确认 → **重开弹窗按服务端值校验**

    python scripts/goods/topup_by_sell_days.py                 # dry-run
    python scripts/goods/topup_by_sell_days.py --apply
    python scripts/goods/topup_by_sell_days.py --store BEL --days 3 --no-presale
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
import bel_stock_set as B  # noqa: E402
import bel_stock_batch as Batch  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

ROOT = Path(r"D:\AI-workplace\TEMU")
DATA = ROOT / "temu-admin" / "public" / "data"
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}


def _load(name: str) -> dict:
    p = DATA / name
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def transit_eta() -> dict:
    """「款·变体·尺码」→ {eta: 预计上架日, qty: 在途件数}（来自 shipment-track）。"""
    ship = _load("shipment-track.json")
    lead = ship.get("leadTime") or {}
    med = int((lead.get("overall") or {}).get("median") or 8)
    out = {}
    for r in ship.get("rows") or []:
        if r.get("status") in (4, 5):          # 已上架 / 已取消 不算在途
            continue
        eta = ""
        if r.get("arriveDate"):
            try:
                eta = (datetime.strptime(r["arriveDate"][:10], "%Y-%m-%d")
                       + timedelta(days=med)).strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                eta = ""
        eta = eta or (r.get("expectedDate") or "")
        for s in r.get("skus") or []:
            style = (s.get("style") or "").replace("BS2-", "").replace("BH-", "")
            key = (style, s.get("size"))
            cur = out.setdefault(key, {"eta": "", "qty": 0})
            if eta and eta > (cur["eta"] or ""):
                cur["eta"] = eta
            cur["qty"] += int(s.get("qty") or 0)
    return out


def load_plan(days: int, horizon: int, stores: list) -> list:
    track = _load("stock-track.json")
    goods = _load("goods.json")
    spu_of = {}
    for st, s in (goods.get("stores") or {}).items():
        for it in s.get("items") or []:
            spu_of[(st, it.get("code"))] = it.get("spuId")
    eta_map = transit_eta()
    out, seen = [], set()
    for src in (track.get("sources") or {}).values():
        for style in src.get("styles") or []:
            for var in style.get("variants") or []:
                for r in var.get("rows") or []:
                    sd, daily = r.get("sellDays"), float(r.get("daily") or 0)
                    if sd is None or float(sd) > days or daily <= 0:
                        continue
                    base = "%s-%s" % (style["code"], var["variant"])
                    if (base, r["size"]) in seen:
                        continue
                    seen.add((base, r["size"]))
                    trans = eta_map.get((base, r["size"])) or {}
                    for st in stores:
                        code = "%s-%s" % (st, base)
                        spu = spu_of.get((st, code))
                        if not spu:
                            continue
                        out.append({"store": st, "code": code, "size": r["size"], "spu": str(spu),
                                    "sellDays": sd, "stock": r.get("stock"), "daily": daily,
                                    "need": math.ceil(daily * horizon),
                                    "eta": trans.get("eta") or "",
                                    "transitQty": int(trans.get("qty") or 0)})
    return out


JS_DIALOG_SCROLL = B.JS_DIALOG + r"""
if (!b.length) return 'no';
var sc = Array.prototype.slice.call(b[0].querySelectorAll('*')).filter(function (e) {
  return e.scrollWidth > e.clientWidth + 50 && e.clientWidth > 300; })[0];
if (sc) sc.scrollLeft = sc.scrollWidth;
return 'ok';
"""


# ------------------------------------------------------------------ 预售（在途）
async def scroll_right(tgt) -> str:
    """把修改库存弹窗的表格横向滚到最右 —— 「在途库存预售 / 补货日期 / 在途预售库存」
    三列默认在视口外（用户截图里就是要向右滑才看得到），不滚过去点不到也填不了。"""
    return await B.ev(tgt, JS_DIALOG_SCROLL)


async def pick_farthest_date(tgt, attr: str, tries: int = 3) -> str:
    """点开该行的「在途库存补货日期」→ 日历里选**最后一个可点**的日期 → 回读实际值。

    ⚠ 2026-11-12 实测（BHY-202504-5SetA）：
      · 日期框是 readOnly 的「请选择日期」，必须点开日历选，直接写不进去；
      · 旧实现按「日号最大」挑格子 —— 日历一次渲染 3 个月，会挑到别的月的 30/31，
        点了不回读（日志「点 30 号(未回读)」），提交被平台拒；
      · 正确做法：在可见的 `RPR_outerPickerWrapper` 里，取**最后一个未 disabled 的
        `RPR_cell`**（日历按时间顺序渲染，平台只放行「今天 ~ 今天+7」）→ 点它 → 回读。
    """
    r = await B.ev_json(tgt, B.JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false, why: 'no-dialog'});
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (!tr) return JSON.stringify({ok: false, why: 'no-row'});
var inp = Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) {
  return i.readOnly && (i.placeholder || '').indexOf('\u9009\u62e9\u65e5\u671f') >= 0; })[0];
if (!inp) return JSON.stringify({ok: false, why: 'no-date-input'});
inp.scrollIntoView({block: 'center'});
var q = inp.getBoundingClientRect();
return JSON.stringify({ok: true, xy: [Math.round(q.x + q.width / 2), Math.round(q.y + q.height / 2)]});"""
                            % json.dumps(attr))
    if not r.get("ok"):
        return ""
    cells_js = r"""
var wrap = Array.prototype.slice.call(document.querySelectorAll('[class*=RPR_outerPickerWrapper]'))
  .filter(function (w) { return w.getClientRects().length; })[0];
if (!wrap) return JSON.stringify({ok: false});
var cells = Array.prototype.slice.call(wrap.querySelectorAll('[class*=RPR_cell]'))
  .filter(function (c) {
    if (String(c.className).indexOf('RPR_disabled') >= 0) return false;
    return c.getClientRects().length && /^\d{1,2}$/.test((c.innerText || '').trim()); });
if (!cells.length) return JSON.stringify({ok: false});
var last = cells[cells.length - 1], q = last.getBoundingClientRect();
return JSON.stringify({ok: true, day: (last.innerText || '').trim(),
  xy: [Math.round(q.x + q.width / 2), Math.round(q.y + q.height / 2)]});"""
    c = {}
    for _ in range(max(1, tries)):        # 点输入框是 toggle，可能第一下反而把它关了
        await B.click_xy(tgt, r["xy"])
        await asyncio.sleep(1.4)
        c = await B.ev_json(tgt, cells_js)
        if c.get("ok"):
            break
    if not c.get("ok"):
        return ""
    await B.click_xy(tgt, c["xy"])
    await asyncio.sleep(1.3)
    await scroll_right(tgt)
    await asyncio.sleep(0.5)
    return await date_value(tgt, attr) or ("" if not c.get("day") else "%s(未回读)" % c["day"])


async def date_value(tgt, attr: str) -> str:
    r = await B.ev_json(tgt, B.JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false});
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
var inp = tr && Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) {
  return /^\d{4}-\d{2}-\d{2}$/.test(i.value || ''); })[0];
return JSON.stringify({ok: !!inp, v: inp ? inp.value : ''});""" % json.dumps(attr))
    return r.get("v") or ""


async def enable_presale(tgt, attr: str, qty: int, code: str = "") -> str:
    """行内「在途库存预售」：横滚 → 开关 → 风险框确认 → 日期框 → 填在途预售库存。

    ⚠ 2026-11-09 实测两次：开关和「在途预售库存」都能稳定写进去，**补货日期是平台
      自己带出来的**（同一个 SKU 7 天内只能开一次，平台按补货日算），
      我们点日期框弹日历再点格子时字段读不回 —— 所以日期只做「尝试 + 记录」，
      不因它失败就中断；下面的读回会把实际写入的日期打出来。
    """
    await scroll_right(tgt)
    if code:
        row = await row_of_code(tgt, code, attr.split("-")[-1])
        if not row.get("ok"):
            return "按货号定位失败:%s" % row
        if row.get("state") == "已开启":
            r = {"ok": True, "already": True}
        else:
            await B.click_xy(tgt, row["xy"])
            await asyncio.sleep(1.5)
            r = {"ok": True, "already": False}
        attr = row.get("attr") or attr
    else:
        r = await B.ev_json(tgt, B.JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false, why: 'no-dialog'});
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (!tr) return JSON.stringify({ok: false, why: 'no-row'});
if ((tr.innerText || '').indexOf('\u5df2\u5f00\u542f') >= 0)
  return JSON.stringify({ok: true, already: true});
var tog = Array.prototype.slice.call(tr.querySelectorAll('div')).filter(function (e) {
  var p = window.__zr.findProps(e);
  return p && typeof p.onClick === 'function' && e.getClientRects().length &&
         (e.innerText || '').indexOf('\u5df2') >= 0; })[0];
if (!tog) return JSON.stringify({ok: false, why: 'no-toggle'});
tog.scrollIntoView({block: 'center'});
var rr = tog.getBoundingClientRect();
return JSON.stringify({ok: true, already: false,
  xy: [Math.round(rr.x + rr.width / 2), Math.round(rr.y + rr.height / 2)]});"""
                            % json.dumps(attr))
    if not r.get("ok"):
        return "定位失败:%s" % r
    if r["already"]:
        return "已是开启状态"
    await B.click_xy(tgt, r["xy"])
    await asyncio.sleep(1.5)
    # ⚠ 「确认开启“在途库存预售”吗？」是独立的小弹窗（PT/PP 前缀组件）。
    #   2026-11-09 实测：先取「最小的那块含『确认开启』的层」，再取它**可见的**「确认」按钮；
    #   之前按外框取按钮会命中外层大弹窗而 `no-btn`。
    c = await B.ev(tgt, r"""
var t = function (e) { return (e.innerText || '').trim(); };
var box = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var s = d.innerText || '';
  if (s.indexOf('\u786e\u8ba4\u5f00\u542f') < 0) return false;
  var q = d.getBoundingClientRect();
  return q.width > 200 && q.width < 700 && q.height > 60 && q.height < 400;
});
if (!box.length) return 'no-warn';
// ⚠ 2026-11-12 修：原来是「取面积最小的、含『确认开启』的层」→ 经常挑中
//   `.enable-pre-sale_title__…`（只有标题文字，**没有按钮**）⇒ 返回 no-confirm-btn，
//   开关没打开，后面日期/在途量全部连带失败。
//   改成：只在**真的有可见「确认」按钮**的层里挑面积最小的。
var cands = [];
box.forEach(function (d) {
  var btns = Array.prototype.slice.call(d.querySelectorAll('button'))
    .filter(function (b) { return t(b) === '\u786e\u8ba4' && b.getClientRects().length; });
  if (!btns.length) return;
  var q = d.getBoundingClientRect();
  cands.push({ a: q.width * q.height, b: btns[btns.length - 1] });
});
if (!cands.length) return 'no-confirm-btn';
cands.sort(function (x, y) { return x.a - y.a; });
return window.__zr.call(cands[0].b, 'onClick');""")
    await asyncio.sleep(1.5)
    await scroll_right(tgt)                     # 确认后表格会重排/回滚，再次滚到最右
    await asyncio.sleep(0.8)
    # ① 日期框：点开日历 → 选**最后一个可点**的日期（= 当天之后最远可点）→ 回读
    picked = await pick_farthest_date(tgt, attr)
    # ② 在途预售库存
    q = await B.ev_json(tgt, B.JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false});
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (!tr) return JSON.stringify({ok: false});
var ins = Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) {
  return (i.placeholder || '').indexOf('5000') >= 0; });
if (!ins.length) return JSON.stringify({ok: false, why: 'no-presale-input'});
return JSON.stringify({ok: true, v: window.__zr.write(ins[ins.length - 1], %s)});"""
                        % (json.dumps(attr), json.dumps(str(qty))))
    # ③ 同尺码的**其他仓库行**：在途预售库存一律填 0
    #   用户 2026-11-13：上链接时选了多个仓库，一个尺码会有 加州3 + kansas 两行；
    #   数量只填有货那仓（加州3），其余仓必须填 0，否则提交时平台静默不响应。
    #   ⚠ 这些附加仓库行**没有属性集文字**（它是上一行的合并单元格），
    #     所以只能按**行序**往里走：从主行往下，直到遇到下一个带 `-尺码` 的行。
    sib = await B.ev_json(tgt, B.JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false});
var rows = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr'));
var idx = rows.findIndex(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); });
if (idx < 0) return JSON.stringify({ok: false, why: 'no-main-row'});
var filled = 0, seen = 0;
for (var i = idx + 1; i < rows.length; i++) {
  var hasAttr = Array.prototype.some.call(rows[i].querySelectorAll('td'), function (c) {
    return /-(S|M|L|XL|XXL|3XL|4XL)$/.test(t(c)); });
  if (hasAttr) break;                       // 到下一个尺码了
  seen++;
  Array.prototype.slice.call(rows[i].querySelectorAll('input')).forEach(function (inp) {
    if ((inp.placeholder || '').indexOf('5000') < 0) return;
    if (String(inp.value || '').trim() !== '') return;
    window.__zr.write(inp, '0');
    filled++;
  });
}
return JSON.stringify({ok: true, subRows: seen, filled: filled});"""
                            % json.dumps(attr))
    return ("开关=%s / 日期=%s / 在途预售库存=%s / 其他仓填0=%s"
            % (c, picked or "平台未带出", q.get("v") or q, sib.get("filled")))


async def row_of_code(tgt, code: str, size: str) -> dict:
    """按「货号 + 尺码」找弹窗里的那一行（返回 {i, attr, trText}）。

    ⚠ 2026-11-11 用户指正：一个 SPU 可能挂多个 SKC（BHY 203805 的 BHC + 3BHL），
      两块属性集都叫「混合色-L」⇒ 只能先锁货号所在区块，再在区块内找尺码。
    """
    return await B.ev_json(tgt, B.JS_DIALOG + r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
if (!b.length) return JSON.stringify({ok: false, why: 'no-modal'});
var trs = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr'));
var start = -1;
trs.forEach(function (tr, i) { if (t(tr).indexOf(%s) >= 0) start = i; });
if (start < 0) return JSON.stringify({ok: false, why: 'no-block'});
for (var i = start; i < Math.min(start + 6, trs.length); i++) {
  var cells = Array.prototype.slice.call(trs[i].querySelectorAll('td')).map(t);
  var attr = cells.filter(function (c) { return /^[^\s]+-(S|M|L|XL|XXL|3XL|4XL)$/.test(c); })[0] || '';
  if (attr && /-%s$/.test(attr)) {
    var tog = Array.prototype.slice.call(trs[i].querySelectorAll('div')).filter(function (e) {
      var p = window.__zr.findProps(e);
      return p && typeof p.onClick === 'function' && /\u5df2/.test(e.innerText || ''); })[0];
    var ins = Array.prototype.slice.call(trs[i].querySelectorAll('input'));
    var q = tog ? tog.getBoundingClientRect() : null;
    return JSON.stringify({ok: true, i: i, attr: attr,
      state: tog ? (tog.innerText || '').trim() : '',
      xy: q ? [Math.round(q.x + q.width / 2), Math.round(q.y + q.height / 2)] : null,
      hasInputs: ins.length});
  }
}
return JSON.stringify({ok: false, why: 'no-row'});""" % (json.dumps(code), size))


# ------------------------------------------------------------------ 主流程
async def do_store(store: str, items: list, stock_mode: str, apply: bool, presale: bool) -> None:
    spu = items[0]["spu"]
    port = probe_port(WINDOWS[store])
    if not port:
        print("!! 探不到 %s 窗口" % store)
        return
    tid = await cdp_net.new_tab(port, B.URL, foreground=True)  # noqa: tab-safety 前台必需，跑完关
    tab = B.OwnTab(port, tid)
    try:
        if not await tab.open():
            print("!! 新页没枚举出来")
            return
        await asyncio.sleep(12)
        for _ in range(4):
            try:
                await zreact.inject(tab.session)
                break
            except Exception:  # noqa: BLE001
                print("   注入失败，重连一次")
                await tab.close()
                await asyncio.sleep(3)
                await tab.open()
                await asyncio.sleep(4)
        if not await B.page_ready(tab, 60):
            print("!! 列表没加载出来")
            return
        await B.pick_query_type(tab, "SPU")
        await B.search(tab, spu)
        if not await B.open_stock_dialog(tab, spu):
            print("!! 修改库存弹窗没打开")
            return
        d = await B.dialog_rows(tab)
        cur = {r["attr"]: r["cur"] for r in d.get("rows", [])}
        plan, want, pre = {}, {}, {}
        for it in items:
            attr = next((a for a in cur if a.endswith("-" + it["size"])), None)
            if not attr:
                print("   %s 弹窗里没有尺码 %s，跳过" % (it["code"], it["size"]))
                continue
            c = int(cur[attr] or 0)
            add = it["need"] if stock_mode == "add" else max(0, it["need"] - c)
            if add > 0:
                plan[attr] = ("增加", add)
                want[attr] = c + add
                print("   %-6s 当前=%d 日销=%.2f 补 %d → 期望 %d"
                      % (attr, c, it["daily"], add, want[attr]))
            else:
                print("   %-6s 当前 TEMU 库存 %d ≥ %d，不补" % (attr, c, it["need"]))
            if presale and it["transitQty"] > 0 and it["eta"]:
                try:
                    dd = (datetime.strptime(it["eta"][:10], "%Y-%m-%d").date() - date.today()).days
                except Exception:  # noqa: BLE001
                    dd = 99
                if 0 <= dd <= 7:
                    pre[attr] = it
                    print("   %-6s 在途 %d 件，预计上架 %s（%d 天）⇒ 开预售"
                          % (attr, it["transitQty"], it["eta"], dd))
        if plan:
            print("   逐行填写：", await Batch.fill_all_rows(tab, plan))
        for attr, it in pre.items():
            print("   预售 %s：%s" % (attr, await enable_presale(tab, attr, it["transitQty"])))
        if not apply:
            print("   dry-run 截图:", await B.shot(tab, "topup_%s_%s_dry.png" % (store, spu)))
            print("   取消:", await B.close_dialog(tab))
            return
        if not plan and not pre:
            await B.close_dialog(tab)
            return
        print("   提交:", await B.confirm(tab))
        await asyncio.sleep(3)
        if not await B.dialog_open(tab):            # 改完必须重开弹窗校验
            await B.open_stock_dialog(tab, spu)
        d3 = await Batch.rows_of(tab)
        got = {r["attr"]: r["cur"] for r in d3.get("rows", [])}
        for a, w in want.items():
            print("   %-6s 服务端库存=%s 期望=%s %s" % (a, got.get(a), w,
                  "✅" if str(got.get(a)) == str(w) else "❌"))
    finally:
        await tab.close()


async def run(plan: list, stock_mode: str, apply: bool, presale: bool) -> int:
    groups = {}
    for p in plan:
        groups.setdefault((p["store"], p["spu"]), []).append(p)
    print("=== 计划：%d 个「店·SPU」组 / %d 个尺码 ===" % (len(groups), len(plan)))
    for (store, spu), items in groups.items():
        print(" %s SPU %-12s %-18s %s" % (store, spu, items[0]["code"],
              "、".join("%s(天数%s,日销%.2f,在途%s@%s)" % (i["size"], i["sellDays"], i["daily"],
                                                      i["transitQty"], i["eta"] or "—") for i in items)))
        try:
            await do_store(store, items, stock_mode, apply, presale)
        except Exception as e:  # noqa: BLE001
            print("!! %s SPU %s 失败：%s: %s" % (store, spu, type(e).__name__, str(e)[:200]))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BEL,BHY")
    ap.add_argument("--days", type=int, default=3, help="可售天数 ≤ 该值才处理")
    ap.add_argument("--horizon", type=int, default=3, help="补足未来多少天：日销 × horizon")
    ap.add_argument("--stock-mode", choices=("diff", "add"), default="diff")
    ap.add_argument("--presale", action="store_true", default=True)
    ap.add_argument("--no-presale", dest="presale", action="store_false")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    plan = load_plan(a.days, a.horizon, [s.strip().upper() for s in a.store.split(",") if s.strip()])
    if not plan:
        print("没有「可售天数 ≤ %d 且有销量」的尺码" % a.days)
        return 0
    return asyncio.run(run(plan, a.stock_mode, a.apply, a.presale))


if __name__ == "__main__":
    raise SystemExit(main())
