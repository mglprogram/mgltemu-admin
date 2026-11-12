# -*- coding: utf-8 -*-
"""BEL 改库存端到端实验（v2）。

    python scripts/goods/bel_stock_set.py --spu 43933794478 --size L --value 0
    python scripts/goods/bel_stock_set.py --spu 43933794478 --size L --value 0 --apply

⛔ 2026-11-09 用户明确要求：**在 BEL 窗口自己的页里操作，用完即关**。
   ⇒ 本会话**只认**本脚本建的 targetId，所有 eval / 输入都切到该 target；
   绝不 fallback 到窗口里用户自己的「商品列表」页（v1 就是这么误改到用户页上的）。

步骤：下拉(LI)切 SPU → 填 SPU → 查询 → 悬停行点「修改库存」→
      「增减」只读 input + 右侧箭头开 <LI> 浮层选「增加/减少」→ 填数 →
      dry-run 取消；--apply 则 确认 → 风险框再确认 → 重开弹窗以服务端值判定。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
import run_stock_topup as R  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

SHOT = Path(r"D:\AI-workplace\TEMU\_logs")
URL = "https://agentseller.temu.com/goods/list"
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}

JS_DIALOG = r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var b = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 400 && r.height > 200 && t(d).indexOf('库存增减') >= 0 &&
         d.querySelectorAll('tbody tr').length > 0;
});
b.sort(function (a, c) { return a.querySelectorAll('tbody tr').length - c.querySelectorAll('tbody tr').length; });
"""


async def ev(tgt, body, timeout=60):
    return await zreact.call_js(tgt.session, body, timeout=timeout)


async def ev_json(tgt, body) -> dict:
    raw = await ev(tgt, body)
    if not str(raw).startswith("{"):
        return {}
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}


async def mouse(tgt, tp, x, y):
    await tgt.session.send("Input.dispatchMouseEvent",
                           {"type": tp, "x": x, "y": y, "button": "left",
                            "clickCount": 1}, timeout=20)
    await asyncio.sleep(0.15)


async def click_xy(tgt, xy):
    x, y = xy
    await mouse(tgt, "mouseMoved", x, y)
    await mouse(tgt, "mousePressed", x, y)
    await mouse(tgt, "mouseReleased", x, y)


async def shot(tgt, name):
    SHOT.mkdir(parents=True, exist_ok=True)
    r = await tgt.session.send("Page.captureScreenshot", {"format": "png"}, timeout=30)
    p = SHOT / name
    p.write_bytes(base64.b64decode(r.get("data") or ""))
    return p


#: 工作页台账（用户 2026-11-09/11-10：窗口里只留**一张**商品列表工作页，反复复用，不要每次新开）
WORK_TAB_FILE = Path(r"D:\AI-workplace\TEMU\_logs\work_tab.json")


def load_work_tab(store: str) -> str:
    try:
        d = json.loads(WORK_TAB_FILE.read_text(encoding="utf-8"))
        return str((d.get(store) or {}).get("tid") or "")
    except Exception:  # noqa: BLE001
        return ""


def save_work_tab(store: str, tid: str) -> None:
    try:
        WORK_TAB_FILE.parent.mkdir(parents=True, exist_ok=True)
        d = {}
        if WORK_TAB_FILE.exists():
            d = json.loads(WORK_TAB_FILE.read_text(encoding="utf-8"))
        d[store] = {"tid": tid, "at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        WORK_TAB_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


class OwnTab:
    """只操作自己建的页；建页 → attach 会话 → 用完 close。"""

    def __init__(self, port: int, tid: str):
        self.port = port
        self.tid = tid
        self.session = None

    async def open(self) -> bool:
        # reuse-first（用户 2026-11-09 要求）：有可用的自有工作页就**复用**，不再重复开页。
        # 2026-11-10 追加：按「窗口里的工作页台账」复用；台账失效就认领窗口里已有的列表页？
        #   ❌ 不认领用户的页 —— 只认台账；台账没有才新建，新建后写台账。
        reuse = getattr(self, "reuse_tid", "") or load_work_tab(getattr(self, "store", ""))
        pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % self.port)
                 if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
        keep = next((x for x in pages if (reuse and reuse[:8] in (x.get("id") or "") + (x.get("targetId") or ""))), None)
        if keep:
            # ⚠ 台账里存的是完整 tid，但 tid 可能随页面重启变化 ⇒ 按**前 8 位**认，命中就用真实 id
            self.tid = keep.get("id") or keep.get("targetId")
        elif reuse and not pages:
            self.tid = reuse
        tgt = await self.find()
        if not tgt and getattr(self, "any_page_ok", False):
            tgt = self.find_any()
        if not tgt:
            return False
        if getattr(self, "store", ""):
            save_work_tab(self.store, self.tid)
        self.session = await cdp_net.NetCapture(tgt["webSocketDebuggerUrl"]).__aenter__()
        # ⚠ 必须挂断开回调：标签页在初始化/崩溃重载时会直接断 CDP，否则下一条命令抛
        #   ConnectionClosedError 把整批打断（现象就是"弹窗开着，进程却突然没了"）。
        asyncio.ensure_future(self._watch_close(self.session))
        return True

    @staticmethod
    async def _watch_close(session):
        try:
            await session._ws.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        session.dead = True

    async def ensure(self, retries: int = 4) -> bool:
        """会话可用性检查：断线/崩溃就重新 attach 同一个 targetId（不新建标签）。"""
        for _ in range(retries):
            s = self.session
            if s is not None and not getattr(s, "dead", False):
                try:
                    await s.send("Runtime.evaluate",
                                 {"expression": "1", "returnByValue": True}, timeout=15)
                    return True
                except Exception:  # noqa: BLE001
                    pass
            if s is not None:
                try:
                    await s.__aexit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
                self.session = None
            await asyncio.sleep(3)
            if not await self.find():
                return False
            await self.open()
        return False

    def find_any(self):
        """--keep 排障专用：页被别的进程关掉/重建后，按 URL 找回同类的页（兜底）。"""
        pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % self.port)
                 if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
        if not pages:
            return None
        self.tid = pages[-1].get("id") or pages[-1].get("targetId")
        return pages[-1]

    async def find(self):
        return next((x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % self.port)
                     if (x.get("id") or x.get("targetId")) == self.tid), None)

    async def assert_own(self) -> bool:
        """确认当前会话真的挂在自己建的页上（防误操作用户的页）。"""
        try:
            r = await self.session.send("Target.getTargetInfo", {}, timeout=15)
            cur = (r.get("targetInfo") or {}).get("targetId")
        except Exception:  # noqa: BLE001
            return False
        return cur == self.tid

    async def keep_tab(self):
        """把本页从"退出时自动关闭"名单里摘掉（--keep 模式用，留给你自己关）。"""
        try:
            cdp_net._AUTO_CLOSE[:] = [(p, t) for (p, t) in cdp_net._AUTO_CLOSE if t != self.tid]
        except Exception:  # noqa: BLE001
            pass

    async def close(self):
        if self.session:
            try:
                await self.session.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self.session = None
        await cdp_net.close_tab(self.port, self.tid)


# ------------------------------------------------------------------ 页面步骤
async def page_ready(tgt, seconds: int = 45) -> bool:
    """页面就绪判定。

    ⚠ 2026-11-10 踩过：上一轮筛选过的表格可能**暂时 0 行**（或窗口里同时跑着别的脚本），
      以前只认「有行」会误判「列表没加载出来」并整批失败。现在认：
      文档 ready + 有可见输入框 + 没有 loading 遮罩；查询后再看命中。
    """
    for _ in range(max(1, seconds // 3)):
        await asyncio.sleep(3)
        st = await ev_json(tgt, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var ins = Array.prototype.slice.call(document.querySelectorAll('input')).filter(function (i) {
  return i.getClientRects().length; });
var loading = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  return d.getClientRects().length && /loading|Loading|spinner|Spinner/.test(String(d.className)); }).length;
return JSON.stringify({ready: document.readyState, inputs: ins.length, rows: document.querySelectorAll('tbody tr').length, loading: loading});""")
        if (st.get("ready") == "complete" and (st.get("inputs") or 0) > 3 and not st.get("loading")):
            return True
    return False


async def close_overlays(tgt) -> str:
    """关掉挡住筛选区的**大浮层**：待办 Modal / 消息中心抽屉。

    ⚠ 2026-11-11 用户指正：以前会把页面上的下拉浮层（SKU(精准查询)、包含/不包含）也点掉，
      那是一般操作区，**不要去动**。这里只认带遮罩的 MDL_* 弹窗和右侧消息抽屉。
    """
    return await ev(tgt, r"""
var n = 0;
function clickEl(e) {
  var p = window.__zr.findProps(e);
  if (p && typeof p.onClick === 'function') { window.__zr.call(e, 'onClick'); n++; return true; }
  return false;
}
// ① 页面级 Modal 的右上关闭（带遮罩的那种）
Array.prototype.slice.call(document.querySelectorAll('.MDL_iconWrapper_5-125-0, .MDL_closeBtn_5-125-0')).forEach(function (e) {
  var box = e.closest('.MDL_outerWrapper_5-125-0, .MDL_container_5-125-0');
  if (!box || !e.getClientRects().length) return;
  if ((box.innerText || '').indexOf('\u5f85\u529e') >= 0 || (box.innerText || '').indexOf('\u60a8\u6709') >= 0) clickEl(e);
});
return 'overlays-closed=' + n;""") or ""


async def reload_page(tgt, seconds: int = 45) -> bool:
    """刷新工作页（用户 2026-11-10：浮层问题最简单就是刷新页面）。

    浮层（消息中心/待办/翻译气泡）盖住筛选区时，有时连下拉都点不开；
    刷新一次页面就能清掉这些 DOM，比逐个找关闭按钮稳。刷新后等页面就绪。
    """
    try:
        await tgt.session.send("Page.reload", {"ignoreCache": False}, timeout=30)
    except Exception:  # noqa: BLE001
        return False
    return await page_ready(tgt, seconds)


async def pick_query_type(tgt, want: str) -> bool:
    """商品ID查询的条件下拉：SKC → SKU → SPU（用户 2026-11-11：只点这一个下拉）。"""
    # ⚠ 2026-11-12：**页面顶部的「待办任务」浮层会挡住筛选区**，这时点条件下拉完全没反应
    #   （实测批量跑 40 个款，全都卡在「条件→SPU: False / hit 0」）。
    #   进下拉之前先把这种大浮层关掉（只关「待办/您有」那类 MDL 弹窗）。
    if not await dialog_open(tgt):
        try:
            await close_overlays(tgt)
        except Exception:  # noqa: BLE001
            pass
    cur = await ev(tgt, "return window.__zr.inputs()[0].value;")
    if cur == want:
        return True
    # ⚠ 用户 2026-11-11：**只点「商品ID查询」这一个下拉**；
    #   「货号 SKU(精准查询)」那行、右侧「商品属性 包含/不包含」都不要去碰。
    xy = await ev_json(tgt, r"""
var i = window.__zr.inputs()[0];
var r = i.getBoundingClientRect();
return JSON.stringify({xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]});""")
    if not xy.get("xy"):
        return False
    for attempt in range(3):
        await click_xy(tgt, xy["xy"])
        await asyncio.sleep(1.2)
        opt = await ev_json(tgt, r"""
var t = function (e) { return (e.innerText || '').trim(); };
var li = Array.prototype.slice.call(document.querySelectorAll('li')).filter(function (e) {
  return t(e) === %s && e.getClientRects().length; });
if (!li.length) return JSON.stringify({ok: false});
var r = li[li.length - 1].getBoundingClientRect();
return JSON.stringify({ok: true, xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]});"""
                           % json.dumps(want))
        if opt.get("ok"):
            await click_xy(tgt, opt["xy"])
            await asyncio.sleep(1)
            if (await ev(tgt, "return window.__zr.inputs()[0].value;")) == want:
                return True
        await asyncio.sleep(1)
    # ⚠ 用户 2026-11-11：「出现弹窗直接刷新就会消失」——有弹窗说明已切好/已就绪，
    #   不要再刷新（刷新会清掉 zreact 与页面状态，下一步就 THREW）。
    if await dialog_open(tgt):
        return False
    await reload_page(tgt)
    await asyncio.sleep(2)
    return False


async def search(tgt, spu: str) -> dict:
    """按 SPU 查询。

    ⚠ 用户 2026-11-11 明确：**不要点「重置」、不要碰「货号」行和右侧「商品属性」下拉**。
      只做三件事：确保条件是 SPU → 在「商品ID查询」输入框写 SPU → 点「查询」。
      旧实现点「重置」会把条件也清掉，还会顺带展开别的下拉（截图里那两个框就是被它点开的）。
    """
    await pick_query_type(tgt, "SPU")
    await ev(tgt, "return window.__zr.write(window.__zr.inputs()[1], %s);" % json.dumps(spu))
    await asyncio.sleep(0.5)
    await ev(tgt, "return window.__zr.call(window.__zr.btn('查询'), 'onClick');")
    key = json.dumps("SPU ID： " + spu)
    st = {}
    for _ in range(8):
        await asyncio.sleep(2)
        st = await ev_json(tgt, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var trs = document.querySelectorAll('tbody tr');
return JSON.stringify({rows: trs.length,
  hit: Array.prototype.filter.call(trs, function (tr) { return t(tr).indexOf(%s) >= 0; }).length});""" % key)
        if st.get("hit"):
            return st
    return st


async def dialog_open(tgt) -> bool:
    n = await ev(tgt, JS_DIALOG + "return String(b.length);")
    try:
        return int(str(n)) > 0
    except (TypeError, ValueError):
        return False


async def wait_dialog(tgt, seconds: int = 14) -> bool:
    for _ in range(max(1, seconds)):
        if await dialog_open(tgt):
            return True
        await asyncio.sleep(1)
    return False


async def open_stock_dialog(tgt, spu: str) -> str:
    key = json.dumps("SPU ID： " + spu)
    for _ in range(8):
        d = await ev_json(tgt, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var tr = Array.prototype.filter.call(document.querySelectorAll('tbody tr'), function (x) {
  return t(x).indexOf(%s) >= 0; })[0];
if (!tr) return JSON.stringify({why: 'no-row'});
tr.scrollIntoView({block: 'center'});
var r = tr.getBoundingClientRect();
var a = Array.prototype.slice.call(tr.querySelectorAll('a'))
  .filter(function (e) { return t(e) === '修改库存'; })[0];
var out = {y: Math.round(r.y + r.height / 2)};
if (a) { var b = a.getBoundingClientRect();
  out.xy = [Math.round(b.x + b.width / 2), Math.round(b.y + b.height / 2)]; }
return JSON.stringify(out);""" % key)
        if d.get("y"):
            await mouse(tgt, "mouseMoved", 400, d["y"])       # 悬停出行内操作列
        await asyncio.sleep(1)
        if d.get("xy") and d["xy"][0] < 1900:
            await click_xy(tgt, d["xy"])
            if await wait_dialog(tgt, 12):
                return "real-mouse"
            break
    r = await ev(tgt, r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var tr = Array.prototype.filter.call(document.querySelectorAll('tbody tr'), function (x) {
  return t(x).indexOf(%s) >= 0; })[0];
var a = tr && Array.prototype.slice.call(tr.querySelectorAll('a'))
  .filter(function (e) { return t(e) === '修改库存'; })[0];
if (!a) return 'no-link';
return window.__zr.call(a, 'onClick');""" % key)
    if await wait_dialog(tgt, 12):
        return "react(%s)" % r
    return "failed(%s)" % r


async def skc_blocks(tgt) -> list:
    """把「修改库存」弹窗按 **SKC 区块**拆出来（一个 SPU 可能带多个 SKC / 多个货号）。

    用户 2026-11-10：BHY 203805 同一 SPU 下还有 BHC 复色，要按**货号**定位再改库存；
      属性集（混合色 / 混合色1）在两块里重复，只按属性集找会改错块。
    返回 [{skcId, code, rows:[{i, attr, cur}]}]，属性集形如「混合色1-L」。
    """
    raw = await ev(tgt, r"""
var txt = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var b = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null; try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 400 && r.height > 200 && txt(d).indexOf('库存增减') >= 0 &&
         d.querySelectorAll('tbody tr').length > 0;
});
b.sort(function (a, c) { return a.querySelectorAll('tbody tr').length - c.querySelectorAll('tbody tr').length; });
if (!b.length) return '[]';
var box = b[0];
var out = [], cur = null;
Array.prototype.slice.call(box.querySelectorAll('tbody tr')).forEach(function (tr, i) {
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(txt);
  var joined = cells.join(' | ');
  var m = joined.match(/\u8d27\u53f7\uff1a\s*([A-Za-z0-9\-]+)/);
  if (m) { cur = {code: m[1], skc: (joined.match(/SKC ID\uff1a\s*(\d+)/) || [])[1] || '', rows: []}; out.push(cur); }
  if (!cur) return;
  var attr = cells.filter(function (c) { return /^[^\s]+-(S|M|L|XL|XXL|3XL|4XL)$/.test(c); })[0] || '';
  var ci = -1;
  for (var k = 0; k < cells.length; k++) if (cells[k] === attr) { ci = k; break; }
  var stock = '';
  if (ci >= 0) for (var j = ci + 1; j < cells.length; j++) if (/^\d+$/.test(cells[j])) { stock = cells[j]; break; }
  cur.rows.push({i: i, attr: attr, cur: stock});
});
return JSON.stringify(out);""")
    try:
        return json.loads(raw or "[]") or []
    except Exception:  # noqa: BLE001
        return []


async def rows_of_code(tgt, code: str, size: str) -> dict:
    """按「货号 + 尺码」定位行（避开同 SPU 下另一个 SKC 的同名尺码）。"""
    blocks = await skc_blocks(tgt)
    for b in blocks:
        if (b.get("code") or "").upper() != code.upper():
            continue
        for r in b.get("rows") or []:
            if (r.get("attr") or "").endswith("-" + size):
                return {**r, "code": b.get("code"), "skc": b.get("skc")}
    return {}


async def dialog_rows(tgt) -> dict:
    return await ev_json(tgt, JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false});
var rows = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).map(function (tr, i) {
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var ins = Array.prototype.slice.call(tr.querySelectorAll('input'));
  var attr = cells.filter(function (c) { return /^[^\s]+-(S|M|L|XL|XXL|3XL|4XL)$/.test(c); })[0] || '';
  var qty = ins.filter(function (x) { return x.placeholder === '请输入'; })[0];
  var dirInp = ins.filter(function (x) { return x.placeholder !== '请输入' && x.type === 'text'; })[0];
  var qtyTd = qty ? (qty.closest('td') || null) : null;
  // ⚠ td 数比表头少（商品信息是 rowspan 块），不能按 cells[attrIdx+5] 找当前库存。
  //   用锚点：属性集之后第一个纯数字 td = 当前库存；再往后第一格可能是
  //   「库存过低，存在风险」这类提示 ⇒ 数字锚点 +3，否则 +2 = 修改后库存。
  var ai = -1;
  for (var k = 0; k < cells.length; k++) { if (cells[k] === attr) { ai = k; break; } }
  var cur = '', after = '';
  if (ai >= 0) {
    var ci = -1;
    for (var j = ai + 1; j < cells.length; j++) {
      if (/^\d+$/.test(cells[j])) { ci = j; break; }
    }
    if (ci >= 0) {
      cur = cells[ci];
      // ⚠ 2026-11-09 实测：列序是「当前库存 | 库存增减(input) | 修改后库存 | …」，
      //   而「库存过低，存在风险」提示是**插在当前库存之后**的独立 td，
      //   所以"下一个数字"会错位。改成：qty 输入框所在 td 的右邻 = 修改后库存。
      after = '';
      if (qtyTd && qtyTd.nextElementSibling) after = t(qtyTd.nextElementSibling);
      if (after === '' || after === '请输入') {
        var ni = ni2(cells, ci);
        after = ni >= 0 ? cells[ni] : '';
      }
    }
  }
  return {i: i, attr: attr, cur: cur, dir: dirInp ? dirInp.value : '',
          qty: qty ? qty.value : '', after: after};
});

function ni2(cells, ci) {
  // 当前库存之后：跳过「库存过低，存在风险」提示，取下一个纯数字（或 '-'）
  for (var j = ci + 1; j < cells.length; j++) {
    var s = cells[j];
    if (s === '-') return j;
    if (/^\d+$/.test(s)) return j;
  }
  return -1;
}
return JSON.stringify({ok: true, rows: rows});""")


async def set_row(tgt, attr: str, direction: str, value: str, code: str = "") -> str:
    """按属性集定位一行并填「增减」。

    ⚠ 2026-11-12：一个 SPU 挂多个 SKC 时，属性集（混合色-S）会在两块里**重名**，
      只按 attr 取第一行会填到别的货号那块（清零白做）。给 code 就先跳到
      「货号：<code>」那一行，再只在这块里找。
    """
    d = await ev_json(tgt, JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false, why: 'no-dialog'});
var rows = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr'));
var CODE = %s;
if (CODE) {
  var st = 0;
  rows.forEach(function (x, i) { if ((x.innerText || '').indexOf(CODE) >= 0) st = i; });
  rows = rows.slice(st, st + 12);
}
var tr = rows.filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (!tr) return JSON.stringify({ok: false, why: 'no-row'});
var inp = Array.prototype.slice.call(tr.querySelectorAll('input'))
  .filter(function (i) { return i.placeholder !== '请输入'; })[0];
inp.scrollIntoView({block: 'center'});
var r = inp.getBoundingClientRect();
return JSON.stringify({ok: true, now: inp.value,
  arrow: [Math.round(r.right + 6), Math.round(r.y + r.height / 2)]});""" % (json.dumps(code), json.dumps(attr)))
    if not d.get("ok"):
        return "定位失败:%s" % d
    # ⚠ 实测：滚动/串行操作时箭头那一下有时"点了不弹" ⇒ 最多重试 3 次（每次重新取坐标）
    for attempt in range(3):
        d = await ev_json(tgt, JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false, why: 'no-dialog'});
var rows2 = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr'));
var CODE2 = %s;
if (CODE2) {
  var st2 = 0;
  rows2.forEach(function (x, i) { if ((x.innerText || '').indexOf(CODE2) >= 0) st2 = i; });
  rows2 = rows2.slice(st2, st2 + 12);
}
var tr = rows2.filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (!tr) return JSON.stringify({ok: false, why: 'no-row'});
var inp = Array.prototype.slice.call(tr.querySelectorAll('input'))
  .filter(function (i) { return i.placeholder !== '请输入' && i.type === 'text'; })[0];
if (!inp) return JSON.stringify({ok: false, why: 'no-dir-input'});
inp.scrollIntoView({block: 'center'});
var r = inp.getBoundingClientRect();
return JSON.stringify({ok: true, now: inp.value,
  arrow: [Math.round(r.right + 6), Math.round(r.y + r.height / 2)]});""" % (json.dumps(code), json.dumps(attr)))
        if not d.get("ok"):
            return "定位失败:%s" % d
        if d["now"] == direction:
            break
        # ⚠ 2026-11-11 实测（BHY）：点右侧那个 8px 小箭头**打不开**浮层，
        #   必须用真实鼠标点「增减」输入框**本体**；点完再在浮层里选方向。
        await click_xy(tgt, d["arrow"])
        await asyncio.sleep(1)
        cur_li = await ev_json(tgt, r"""
var li = Array.prototype.slice.call(document.querySelectorAll('li')).filter(function (e) {
  return /^(增加|减少)$/.test((e.innerText || '').trim()) && e.getClientRects().length; });
if (!li.length) return JSON.stringify({n: 0});
var r = li[0].getBoundingClientRect();
return JSON.stringify({n: li.length, xy: [Math.round(r.x + 12), Math.round(r.y + r.height / 2)]});""")
        if not cur_li.get("n"):
            body_r = await ev_json(tgt, JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false});
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
if (!tr) return JSON.stringify({ok: false});
var inp = Array.prototype.slice.call(tr.querySelectorAll('input'))
  .filter(function (i) { return i.readOnly && (i.value === '增加' || i.value === '减少'); })[0];
if (!inp) return JSON.stringify({ok: false});
inp.scrollIntoView({block: 'center'});
var q = inp.getBoundingClientRect();
return JSON.stringify({ok: true, xy: [Math.round(q.x + q.width / 2), Math.round(q.y + q.height / 2)]});""" % json.dumps(attr))
            if body_r.get("ok"):
                await click_xy(tgt, body_r["xy"])
                await asyncio.sleep(1.2)
        opt = await ev_json(tgt, r"""
var li = Array.prototype.slice.call(document.querySelectorAll('li')).filter(function (e) {
  return (e.innerText || '').trim() === %s && e.getClientRects().length; });
if (!li.length) return JSON.stringify({ok: false});
var r = li[li.length - 1].getBoundingClientRect();
return JSON.stringify({ok: true, xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]});"""
                               % json.dumps(direction))
        if not opt.get("ok"):
            await asyncio.sleep(0.8)
            continue
        await click_xy(tgt, opt["xy"])
        await asyncio.sleep(1)
    w = await ev(tgt, JS_DIALOG + r"""
if (!b.length) return 'no-dialog';
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
var qty = Array.prototype.slice.call(tr.querySelectorAll('input')).filter(function (i) { return i.placeholder === '请输入'; })[0];
return window.__zr.write(qty, %s);""" % (json.dumps(attr), json.dumps(str(value))))
    await asyncio.sleep(1.5)
    # ⚠ 自己校一遍方向（切行序可能让 set_row 的定位漂）：读回来不对就报错
    chk = await ev_json(tgt, JS_DIALOG + r"""
if (!b.length) return JSON.stringify({ok: false});
var tr = Array.prototype.slice.call(b[0].querySelectorAll('tbody tr')).filter(function (x) {
  return Array.prototype.some.call(x.querySelectorAll('td'), function (c) { return t(c) === %s; }); })[0];
var ins = Array.prototype.slice.call(tr.querySelectorAll('input'));
var d = ins.filter(function (x) { return x.placeholder !== '请输入' && x.type === 'text'; })[0];
var q = ins.filter(function (x) { return x.placeholder === '请输入'; })[0];
return JSON.stringify({dir: d ? d.value : '', qty: q ? q.value : ''});""" % json.dumps(attr))
    if chk.get("dir") != direction or str(chk.get("qty")) != str(value):
        return "❌ 回读不符（方向=%s 数量=%s，期望 %s %s）" % (
            chk.get("dir"), chk.get("qty"), direction, value)
    return "切「%s」+ 填 %s ✅ → %s" % (direction, value, w)


async def close_dialog(tgt) -> str:
    return await ev(tgt, r"""
var t = function (e) { return (e.innerText || '').trim(); };
var bs = Array.prototype.slice.call(document.querySelectorAll('button'))
  .filter(function (x) { return t(x) === '取消' && x.getClientRects().length; });
return bs.length ? window.__zr.call(bs[bs.length - 1], 'onClick') : 'no-cancel';""")


async def _click_risk_confirm(tgt) -> str:
    """点风险框「建议SKU下…」里的真「确认」按钮（带 onClick 的内层 button）。"""
    return await ev(tgt, r"""
var t = function (e) { return (e.innerText || '').trim(); };
var box = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var q = d.getBoundingClientRect(); var s = d.innerText || '';
  return q.width > 200 && q.width < 700 && q.height > 60 && q.height < 350 &&
         s.indexOf('\u5efa\u8baeSKU') >= 0 && d.querySelectorAll('button').length > 0;
});
if (!box.length) return 'no-risk';
var bs = Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (b) {
  return t(b) === '\u786e\u8ba4' && b.getClientRects().length; });
var real = bs.filter(function (b) {
  var p = window.__zr.findProps(b);
  return p && typeof p.onClick === 'function' &&
         box.some(function (d) { return d.contains(b); });
});
if (!real.length) return 'no-real-confirm';
real.sort(function (a, b) { return a.querySelectorAll('*').length - b.querySelectorAll('*').length; });
var q = real[0].getBoundingClientRect();
return window.__zr.call(real[0], 'onClick') + ' @' + Math.round(q.x) + ',' + Math.round(q.y);""")


async def confirm(tgt) -> str:
    r1 = await ev(tgt, JS_DIALOG + r"""
if (!b.length) return 'no-dialog';
var bs = Array.prototype.slice.call(b[0].querySelectorAll('button'))
  .filter(function (x) { return t(x) === '确认'; });
if (!bs.length) return 'no-confirm';
return window.__zr.call(bs[bs.length - 1], 'onClick');""")
    await asyncio.sleep(3)
    # ⚠ 2026-11-11 实测：风险框有两层 DOM，外层「确认」点不动；
    #   只认带 React onClick 的真按钮（见 _click_risk_confirm）
    r2 = await _click_risk_confirm(tgt)
    await asyncio.sleep(4)
    opened = await dialog_open(tgt)
    return "%s / 二次确认=%s / 弹窗%s" % (r1, r2, "仍开着" if opened else "已关")


# ------------------------------------------------------------------ 主流程
async def run(store: str, spu: str, size: str, value: int, apply: bool) -> int:
    port = probe_port(WINDOWS[store])
    if not port:
        print("!! 探不到 %s 窗口" % store)
        return 3
    before = {x.get("id") for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
              if x.get("type") == "page"}
    tid = await cdp_net.new_tab(port, URL, foreground=True)  # noqa: tab-safety 前台必需，跑完关
    tab = OwnTab(port, tid)
    print("本次自建页 target:", str(tid)[:16])
    try:
        if not await tab.open():
            print("!! 新页没枚举出来（不再 fallback 到用户的页）")
            return 3
        print("   自建页 = 本会话:", await tab.assert_own())
        await asyncio.sleep(12)
        await zreact.inject(tab.session)
        if not await page_ready(tab):
            print("!! 列表 45s 内没加载出来")
            return 5
        print("   窗口里原有页:", sorted(x[:8] for x in before))
        print("① 查询条件 → SPU:", await pick_query_type(tab, "SPU"))
        print("   查询:", await search(tab, spu))
        print("② 打开「修改库存」:", await open_stock_dialog(tab, spu))
        d = await dialog_rows(tab)
        if not d.get("ok"):
            print("!! 弹窗没打开")
            return 4
        for row in d["rows"]:
            print("   %-12s 当前库存=%-5s 增减=%-4s 填=%-5s 修改后=%s"
                  % (row["attr"], row["cur"], row["dir"], row["qty"], row["after"]))
        row = next((r for r in d["rows"] if r["attr"].endswith("-" + size)), None)
        if not row:
            print("!! 弹窗里没有尺码 %s" % size)
            await close_dialog(tab)
            return 4
        cur_n = int(row["cur"] or 0)
        direction = "减少" if value < cur_n else "增加"
        qty = abs(value - cur_n)
        print("③ 目标 %s：当前 %d → %d，即「%s %d」" % (row["attr"], cur_n, value, direction, qty))
        if qty:
            print("   ", await set_row(tab, row["attr"], direction, str(qty)))
            d2 = await dialog_rows(tab)
            for r2 in d2.get("rows", []):
                if r2["attr"] == row["attr"]:
                    print("   回读：增减=%s 填=%s 修改后=%s" % (r2["dir"], r2["qty"], r2["after"]))
        if not apply:
            print("   dry-run 截图:", await shot(tab, "bel_set_%s_%s_dry.png" % (spu, size)))
            print("④ (dry-run：不点确认) 取消:", await close_dialog(tab))
            return 0
        print("④ 提交:", await confirm(tab))
        await asyncio.sleep(3)
        if not await dialog_open(tab):
            await open_stock_dialog(tab, spu)
        d3 = await dialog_rows(tab)
        for r3 in d3.get("rows", []):
            if r3["attr"] == row["attr"]:
                print("⑤ 服务端当前库存 = %s %s" % (r3["cur"],
                      "✅" if str(r3["cur"]) == str(value) else "❌ 目标 %s" % value))
                return 0 if str(r3["cur"]) == str(value) else 6
        print("⑤ 回读失败（弹窗没能重开）")
        return 6
    finally:
        if getattr(run, "keep", False):
            await tab.keep_tab()
            print("已保留本次开的页（--keep）：%s" % tab.tid[:16])
        else:
            await tab.close()
            print("已关掉本次开的页")


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 指定 SPU 某尺码库存设为 N（默认 dry-run）")
    ap.add_argument("--store", default="BEL")
    ap.add_argument("--spu", required=True)
    ap.add_argument("--size", required=True)
    ap.add_argument("--value", type=int, required=True)
    ap.add_argument("--apply", action="store_true", help="真的提交")
    ap.add_argument("--keep", action="store_true",
                    help="跑完**不关**自己开的页，留给你看（默认关；见用户 2026-11-09 反馈）")
    a = ap.parse_args()
    if a.keep:
        run.keep = True
    return asyncio.run(run(a.store.upper(), a.spu.strip(), a.size.strip(), a.value, a.apply))


if __name__ == "__main__":
    raise SystemExit(main())
