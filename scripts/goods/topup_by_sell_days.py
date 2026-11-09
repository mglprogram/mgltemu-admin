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


async def date_value(tgt, attr: str) -> str:
    r = await B.ev_json(tgt, B.JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false});
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
var inp = tr && Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) {
  return /^\d{4}-\d{2}-\d{2}$/.test(i.value || ''); })[0];
return JSON.stringify({ok: !!inp, v: inp ? inp.value : ''});""" % json.dumps(attr))
    return r.get("v") or ""


async def enable_presale(tgt, attr: str, qty: int) -> str:
    """行内「在途库存预售」：横滚 → 开关 → 风险框确认 → 日期框 → 填在途预售库存。

    ⚠ 2026-11-09 实测两次：开关和「在途预售库存」都能稳定写进去，**补货日期是平台
      自己带出来的**（同一个 SKU 7 天内只能开一次，平台按补货日算），
      我们点日期框弹日历再点格子时字段读不回 —— 所以日期只做「尝试 + 记录」，
      不因它失败就中断；下面的读回会把实际写入的日期打出来。
    """
    await scroll_right(tgt)
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
box.sort(function (a, b) {
  var x = a.getBoundingClientRect(), y = b.getBoundingClientRect();
  return (x.width * x.height) - (y.width * y.height); });
var btn = Array.prototype.slice.call(box[0].querySelectorAll('button,div,span'))
  .filter(function (b) { return t(b) === '\u786e\u8ba4' && b.getClientRects().length; })
  .sort(function (a, b) { return a.querySelectorAll('*').length - b.querySelectorAll('*').length; })[0];
if (!btn) return 'no-confirm-btn';
return window.__zr.call(btn, 'onClick');""")
    await asyncio.sleep(1.5)
    await scroll_right(tgt)                     # 确认后表格会重排/回滚，再次滚到最右
    await asyncio.sleep(0.8)
    # ① 日期框：点开、尽量选最远（失败不致命，只记录）
    picked = await date_value(tgt, attr)
    if not picked:
        dc = await B.ev_json(tgt, B.JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false});
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
var inp = tr && Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) {
  return /^\d{4}-\d{2}-\d{2}$/.test(i.value || '') ||
         (i.placeholder || '').indexOf('\u9009\u62e9\u65e5\u671f') >= 0; })[0];
if (!inp) return JSON.stringify({ok: false});
var rr = inp.getBoundingClientRect();
return JSON.stringify({ok: true, xy: [Math.round(rr.x + rr.width / 2), Math.round(rr.y + rr.height / 2)]});"""
                                % json.dumps(attr))
        if dc.get("ok"):
            await B.click_xy(tgt, dc["xy"])
            await asyncio.sleep(1.3)
            cells = (await B.ev_json(tgt, r"""
var out = [];
Array.prototype.slice.call(document.querySelectorAll('span,td,div')).forEach(function (e) {
  var s = (e.innerText || '').trim();
  if (!/^\d{1,2}$/.test(s)) return;
  var d = parseInt(s, 10);
  if (d < 1 || d > 31) return;
  var q = e.getBoundingClientRect();
  if (!e.getClientRects().length || q.width < 8 || q.width > 48 || q.height > 48) return;
  var cls = String(e.className) + ' ' + String((e.parentElement || {}).className || '');
  if (/disabled|Disable/i.test(cls)) return;
  out.push({d: d, x: Math.round(q.x + q.width / 2), y: Math.round(q.y + q.height / 2)});
});
return JSON.stringify({n: out.length, cells: out});""")).get("cells") or []
            cells = [c for c in cells if isinstance(c.get("d"), int)]
            if cells:
                far = max(cells, key=lambda c: c["d"])
                await B.click_xy(tgt, [far["x"], far["y"]])
                await asyncio.sleep(1.2)
                await scroll_right(tgt)
                await asyncio.sleep(0.6)
                picked = await date_value(tgt, attr) or ("点 %d 号(未回读)" % far["d"])
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
    return "开关=%s / 日期=%s / 在途预售库存=%s" % (c, picked or "平台未带出", q.get("v") or q)


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
