# -*- coding: utf-8 -*-
"""TEMU「修改库存 + 开预售」写操作驱动（加州仓）。

分工（2026-10-12 实测后的务实方案）
-----------------------------------
· **筛选这一步留给人**:商品列表的「查询条件」下拉（SKC / SKU货号 / …）程序切不动 ——
  真实坐标点击能弹出浮层，但选项不在可点位置;直接调 React onClick 只命中表格单元格。
  所以脚本不去点筛选，而是要求页面上**已经**把目标款筛出来（1 行）。
· **其余全部自动**:打开「修改库存」弹窗 → 逐尺码填「增加 + 数量」→ 到货 ≤7 天的
  顺带开预售（开关 → 二次确认 → 补货日期取窗口内最大 → 预售库存）→ 提交 → 逐行回读
  「修改后库存」核对。

两道保险（按用户要求）
----------------------
1. 只对 **dry-run 计划里列出的款·尺码** 填数，计划外的行一律不动;
2. 提交后**逐行回读**「修改后库存」，与期望值不一致就报警（不回滚，但会明确报出来）。

踩坑记录（都实测过，别再试）
---------------------------
· 点按钮**必须**直调它的 React `__reactProps.onClick`;页内合成事件 / CDP 真实鼠标 /
  CLI `page click` 都不会触发（不报错、不发请求、界面不变）。
· 不要在 `Runtime.evaluate` 里跑带 IIFE 的表达式（返回值会被宿主再包一层调用吃掉）;
  改用 `Runtime.callFunctionOn`（见 `zreact.call_js`）。
· 写受控输入框走 `zreact` 的 `write`（内部调 React onChange），值是能写进去的。

用法
----
    # 0) 先在紫鸟窗口里手工把目标款筛出来（查询条件选 SKU货号，填款号，点查询）
    python scripts/goods/run_stock_topup.py --store BHY --code BHY-102204-6SetC
    python scripts/goods/run_stock_topup.py --store BHY --code BHY-102204-6SetC --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402
import temu_stock_topup as T  # noqa: E402

ROOT = Path(r"D:\AI-workplace\TEMU")
GOODS_JSON = ROOT / "temu-admin" / "public" / "data" / "goods.json"
CFG_PATH = ROOT / "scripts" / "power" / "data" / "temu_restock.json"
OUT_DIR = ROOT / "out" / "stock-topup"
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}
URL_GOODS = "https://agentseller.temu.com/goods/list"
PRESALE_MAX_DAYS = 7          # 到货 ≤7 天 → 顺带开预售（TEMU 规则）


# ------------------------------------------------------------------ 口径/计划
def load_cfg() -> dict:
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8-sig")) if CFG_PATH.exists() else {}
    t = cfg.setdefault("加库存", {})
    for k, v in {
        "TEMU库存上限": 10, "仓内现货下限": 0, "默认加量": 30, "充足阈值": 150,
        "不足比例": 0.2, "不足保底": 3, "目标天数": 30, "也受需求上限约束": False,
        "仓库": "帕沃尔·加州3仓", "弹窗仓库名": "加州三仓",
    }.items():
        t.setdefault(k, v)
    return cfg


def calc_add(wh_ca: float, temu_now: float, t: dict) -> tuple[int, str]:
    want = (float(t["默认加量"]) if wh_ca >= float(t["充足阈值"])
            else max(float(t["不足保底"]), wh_ca * float(t["不足比例"])))
    why = ("仓内 %d ≥ %s ⇒ 默认加 %s" % (wh_ca, t["充足阈值"], t["默认加量"])
           if wh_ca >= float(t["充足阈值"])
           else "仓内 %d < %s ⇒ %d×%s，保底 %s"
                % (wh_ca, t["充足阈值"], wh_ca, t["不足比例"], t["不足保底"]))
    add = T.round_half_up(want)
    if add > wh_ca:
        add = int(wh_ca)
        why += "；不超过仓内现货 ⇒ %d" % add
    return max(0, int(add)), why


def build_plan(store: str, code: str, t: dict) -> list:
    """按款号算计划：只含「TEMU 库存 ≤ 上限 且 加州仓现货 ≥ 下限」的尺码。"""
    goods = json.loads(GOODS_JSON.read_text(encoding="utf-8"))
    s = (goods.get("stores") or {}).get(store) or {}
    cfg_t = T.load_cfg()
    snap = T.fetch_snapshot(cfg_t, force=True, log=lambda m: None)
    #: 加量依据：**加州 + kansas 两仓之和**（用户 2026-10-12 修正）。
    #: 加库存动作仍然只落在加州仓，但"能加多少"看两仓合计的现货。
    keys = t.get("仓库关键字") or ["加州", "kansas"]
    if isinstance(keys, str):
        keys = [keys]
    board = {}
    for r in snap.get("rows") or []:
        nm = r.get("仓库名称") or ""
        if not nm or not any(k in nm for k in keys):
            continue
        board[r.get("小款号")] = board.get(r.get("小款号"), 0) + float(r.get("代发库存") or 0)
    plan = []
    want = code.upper()
    for r in s.get("lowStock", {}).get("rows", []):
        # ⚠ 款号大小写敏感：goods.json 里是 `BHY-102204-6SetC`，命令行常写成全大写，
        #   所以两边都 upper() 再比（2026-10-12 踩过：全大写导致计划为空）。
        if want and r["code"].upper() != want:
            continue
        # ⚠ 只加面板判为「可加库存」的行（2026-10-12 用户要求两处口径一致）：
        #   面板对**没有日均数据**的款标「仓里有货(未定量)」并不给量；脚本若照算
        #   就会出现「面板写未定量、脚本却给出 23 件」的矛盾（BEL-102105-6SetB 的 S/XXL）。
        if (r.get("verdict") or "") != "可加库存":
            continue
        parsed = T.parse_sku_code(r.get("skuCode") or r["code"], cfg_t)
        if not parsed["ok"]:
            continue
        conv = T.map_variant(parsed["variant"], cfg_t)
        key = "%s-%s-%s" % (parsed["product"], conv["看板变体"], parsed["size"])
        wh = board.get(key)
        if wh is None or wh <= 0:
            continue
        if (r.get("stock") or 0) > float(t["TEMU库存上限"]):
            continue
        if wh < float(t["仓内现货下限"]):
            continue
        add, why = calc_add(wh, float(r.get("stock") or 0), t)
        if add <= 0:
            continue
        d2a = r.get("daysToArrival")
        plan.append({"code": r["code"], "size": r["size"], "boardKey": key,
                     "temuNow": r.get("stock"), "whCa": wh, "add": add, "why": why,
                     "arrivalDate": r.get("arrivalDate") or "",
                     "daysToArrival": d2a,
                     "presale": bool(d2a is not None and 0 <= d2a <= PRESALE_MAX_DAYS)})
    plan.sort(key=lambda x: x["size"])
    return plan


def print_plan(plan: list, code: str) -> None:
    print("\n=== %s 加库存计划（仅加州仓）===" % code)
    if not plan:
        print("  （没有符合条件的尺码）")
        return
    print("%-6s %8s %9s %6s %-6s %-12s %s"
          % ("尺码", "TEMU现有", "加州仓现货", "加量", "开预售", "到货日", "依据"))
    for p in plan:
        print("%-6s %8s %9s %6s %-6s %-12s %s"
              % (p["size"], p["temuNow"], int(p["whCa"]), p["add"],
                 "是" if p["presale"] else "—", p["arrivalDate"] or "—", p["why"]))
    print("合计 %d 条 · 加库存 %d 件 · 开预售 %d 条"
          % (len(plan), sum(p["add"] for p in plan), sum(1 for p in plan if p["presale"])))


# ------------------------------------------------------------------ 页面动作
async def ev_ok(cap, body: str):
    """统一的页面执行入口（走 callFunctionOn，返回值语义干净）。"""
    return await zreact.call_js(cap, body, timeout=60)


async def ev_json(cap, body: str) -> dict:
    """跑页面 JS 并解析 JSON；空/异常都退化成 {}（不要把整条链打断）。

    ⚠ 实测：页面偶发返回空串或 EXC:...（标签被节流、弹窗还没挂载），
      直接 json.loads 会抛 JSONDecodeError 把整个批量跑挂掉。
    """
    raw = await ev_ok(cap, body)
    if not raw or not str(raw).strip().startswith("{"):
        return {}
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}


#: 第二行筛选器是「SKU(精准查询)」，它的值输入框在可见 input 里排第 7 个（下标 6）。
#
#  ⚠ 这里是**卡了很久**的地方，把结论记下来：
#    · 页面上有**两行**筛选器：第 1 行「SKC」+ 模糊查询；第 2 行「SKU(精准查询)」。
#    · 第 2 行默认就是**精准**匹配 ⇒ 必须填**完整 SKU 货号**（`BHY-102204-6SetC-S`），
#      填款号（`BHY-102204-6SetC`）精准匹配不到任何 SKU，于是返回全量列表 ——
#      这就是"查询点了却不过滤"的真正原因（之前一直以为是下拉切不动）。
#    · 「查询」按钮必须**直调它的 React onClick**（合成事件/CDP 真实鼠标都无效）。
JS_FILL_KEYWORD = """
var kw = %s;
var ins = Array.prototype.slice.call(document.querySelectorAll('input'))
  .filter(function (i) { return i.getClientRects().length; });
if (ins.length < 7) return 'not-enough-inputs:' + ins.length;
return window.__zr.write(ins[6], kw);
"""

JS_ROWS_STATE = """
var trs = document.querySelectorAll('tbody tr');
var codes = [];
Array.prototype.forEach.call(trs, function (t) {
  var s = t.innerText || '';
  var i = s.indexOf('货号：');
  if (i >= 0) {
    var tail = s.slice(i + 3);
    var nl = tail.indexOf(String.fromCharCode(10));
    codes.push(nl > 0 ? tail.slice(0, nl) : tail.slice(0, 21));
  }
});
return JSON.stringify({rows: trs.length, codes: codes.slice(0, 6)});
"""


async def search_style(cap, store: str, code: str, sizes=None) -> bool:
    """按「完整 SKU 货号」精准筛出该款（返回是否筛成功）。

    ⚠ 搜索词里的尺码**必须是该款真实存在的尺码**：早先固定拿 `-S` 去搜，遇到
      `BHY-207002-3BHP`（尺码是 3XL/4XL，没有 S）就返回 0 行 → 后面全部打不开弹窗
      （2026-10-13 实测，稳定复现）。所以这里按候选尺码逐个试。
    """
    cand = [s for s in (sizes or []) if s]
    if not cand:
        cand = ["S", "M", "L", "XL", "XXL", "3XL", "4XL", "XS", "XXXL"]
    for size in cand:
        kw = "%s-%s" % (code, size)      # 用完整 SKU 货号做精准匹配
        await ev_ok(cap, JS_FILL_KEYWORD % json.dumps(kw))
        await ev_ok(cap, "return window.__zr.call(window.__zr.btn('查询'), 'onClick');")
        for _ in range(6):
            await asyncio.sleep(2)
            st = await ev_json(cap, JS_ROWS_STATE)
            if st.get("rows") and st.get("rows") < 12:
                break
        st = await ev_json(cap, JS_ROWS_STATE)
        codes = {c for c in (st.get("codes") or [])}
        ok = bool(codes) and all(c.upper() == code.upper() for c in codes)
        print("   筛选(%s): rows=%s codes=%s → %s"
              % (kw, st.get("rows"), sorted(codes)[:3], "成功" if ok else "没匹配"))
        if ok:
            return True
    return False


async def dialog_open(cap) -> bool:
    """弹窗（含「库存增减」表头的那张表）是否已挂载。"""
    n = await ev_ok(cap, """
var txt = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null;
  try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  if (!(r.width > 400 && r.height > 200)) return false;
  return txt(d).indexOf('库存增减') >= 0 && d.querySelectorAll('tbody tr').length > 0;
});
return String(boxes.length);
""")
    try:
        return int(n or 0) > 0
    except (TypeError, ValueError):
        return False


#: 找「修改库存」链接（优先自身带 React onClick 的），并回传坐标
JS_EDIT_LINK = """
var all = Array.prototype.slice.call(document.querySelectorAll('a,span,div,button'));
var links = all.filter(function (e) {
  return (e.innerText || '').trim() === '修改库存' && e.getClientRects().length > 0; });
if (!links.length) return JSON.stringify({n: 0});
var pick = null;
for (var i = 0; i < links.length; i++) {
  var p = window.__zr.findProps(links[i]);
  if (p && typeof p.onClick === 'function') { pick = links[i]; break; }
}
if (!pick) pick = links[links.length - 1];
try { pick.scrollIntoView({block: 'center'}); } catch (e) {}
var r = pick.getBoundingClientRect();
return JSON.stringify({n: links.length, tag: pick.tagName,
  xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]});
"""

JS_EDIT_REACT = """
var all = Array.prototype.slice.call(document.querySelectorAll('a,span,div,button'));
var links = all.filter(function (e) {
  return (e.innerText || '').trim() === '修改库存' && e.getClientRects().length > 0; });
var pick = null;
for (var i = 0; i < links.length; i++) {
  var p = window.__zr.findProps(links[i]);
  if (p && typeof p.onClick === 'function') { pick = links[i]; break; }
}
if (!pick) return 'no-link';
try { pick.scrollIntoView({block: 'center'}); } catch (e) {}
return window.__zr.call(pick, 'onClick');
"""


async def open_dialog(cap, code: str) -> str:
    """打开「修改库存」弹窗一次，返回结果描述。

    ⚠ 2026-10-13 实测（`BHY-207002-3BHP`）：同一个 `<A>修改库存`，
      **直调 React onClick 连点三次都不弹窗**，而**真实鼠标点同一坐标就弹了**。
      ⇒ 首选手法改为 CDP 真实鼠标序列（`Input.dispatchMouseEvent`），
        React 直调退居兜底；两种都留着，因为有的款反过来只有 React 有效。
      （另外：文本为「修改库存」的有 3 个元素——外层 DIV / `<A>` / 内层 SPAN，
       只有 `<A>` 自身带 onClick；它常在视口外，所以先 scrollIntoView。）
    """
    # ⚠ 关键：**搜索刚做完时页面还没稳定**，此时点链接常"点了不弹"。
    #   实测（2026-10-13）静置几秒后再点就正常 ⇒ 先等页面稳定。
    await asyncio.sleep(5)

    last = "n/a"
    for attempt in range(3):
        raw = await ev_ok(cap, JS_EDIT_LINK)
        try:
            d = json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            d = {}
        if not d.get("xy"):
            last = "no-edit-link:%s" % d.get("n")
            await asyncio.sleep(3)
            continue

        await click_real(cap, d["xy"])                   # ① 真实鼠标
        # 弹窗不是瞬间挂上的：2.5s 才出「库存增减」列、5s 才出全部列 ⇒ 轮询等
        if await wait_dialog(cap, 14):
            return "mouse@%s(attempt %d)" % (d["xy"], attempt + 1)

        await ev_ok(cap, JS_EDIT_REACT)                  # ② 兜底：React 直调
        if await wait_dialog(cap, 10):
            return "react(attempt %d)" % (attempt + 1)
        last = "clicked-but-no-modal"
        await asyncio.sleep(3)
    return last


async def wait_dialog(cap, seconds: int = 14) -> bool:
    """轮询等弹窗挂上（每秒看一次）。"""
    for _ in range(max(1, seconds)):
        if await dialog_open(cap):
            return True
        await asyncio.sleep(1)
    return False


DIALOG_INFO = r"""
/* 弹窗识别：不要只按 position/size 取「最后一个」——页面上还有消息中心那种大浮层。
   这里要求容器里**同时**有「修改库存」标题和 tbody 行，并取行数最少的那个（最贴近表格）。

   ⚠ 列对齐（2026-10-12 用列头钉死，别再猜 cell[4]）：
     表头 = ['', '基础信息', '仓内库存', '在途库存', '覆盖站点', '商品信息', '属性集',
             '发货模式', '发货仓', '库存维护方式', '当前库存', '库存增减', '修改后库存',
             '在途库存预售功能介绍', '在途库存补货日期', '在途预售库存']
     但行的 td 数比表头少（商品信息是 rowspan 合并块），所以按**锚点**定位：
       · 属性集  = 形如 `混合色-S` 的 td（以尺码结尾、长度短）
       · 当前库存 = 属性集之后**第一个纯数字** td
       · 修改后库存 = 当前库存往后第 3 个（中间夹了「库存增减」那个 td）
       · 数量输入框 = 该行最后一个占位含「请输入」的 input
*/
var txt = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var isNum = function (s) {
  if (!s || s.length > 6) return false;
  for (var i = 0; i < s.length; i++) {
    var c = s.charAt(i);
    if (c < '0' || c > '9') return false;
  }
  return true;
};
var all = Array.prototype.slice.call(document.querySelectorAll('div'));
var boxes = all.filter(function (d) {
  var st = null;
  try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  if (!(r.width > 400 && r.height > 200)) return false;
  return txt(d).indexOf('修改库存') >= 0 && d.querySelectorAll('tbody tr').length > 0;
});
if (!boxes.length) return JSON.stringify({ok: false, why: 'no-stock-modal'});
boxes.sort(function (a, b) {
  return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length;
});
var box = boxes[0];
var trs = Array.prototype.slice.call(box.querySelectorAll('tbody tr'));
var out = [];
trs.forEach(function (tr, idx) {
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(txt);
  var attrIdx = -1;
  for (var i = 0; i < cells.length; i++) {
    var m = cells[i].match(/(S|M|L|XL|XXL|XXXL|XS)$/);
    if (m && cells[i].length <= 16) { attrIdx = i; break; }
  }
  var size = attrIdx >= 0 ? cells[attrIdx].match(/(S|M|L|XL|XXL|XXXL|XS)$/)[1] : '';
  var curIdx = -1;
  for (var j = attrIdx + 1; j < cells.length; j++) {
    if (isNum(cells[j])) { curIdx = j; break; }
  }
  var ins = Array.prototype.slice.call(tr.querySelectorAll('input'));
  var qty = null;
  for (var k = ins.length - 1; k >= 0; k--) {
    if ((ins[k].placeholder || '').indexOf('请输入') >= 0) { qty = ins[k]; break; }
  }
  out.push({i: idx, size: size, attr: attrIdx >= 0 ? cells[attrIdx] : '',
            curStock: curIdx >= 0 ? cells[curIdx] : null,
            afterStock: curIdx >= 0 ? cells[curIdx + 2] : null,
            #: ⚠ 「修改后库存」在没填数时显示的是 `-`；它和「当前库存」之间只隔了
            #:   「库存增减」那一格，但 td 数会因 rowspan 变化 ⇒ 顺便把整行尾巴带回来诊断。
            tail: cells.slice(Math.max(0, curIdx), curIdx + 4),
            qtyPh: qty ? qty.placeholder : null,
            qtyValue: qty ? qty.value : null,
            nInputs: ins.length, cells: cells.slice(0, 9)});
});
return JSON.stringify({ok: true,
  sku: (txt(box).match(/货号：\s*([A-Za-z0-9\-]+)/) || [])[1] || '',
  heads: Array.prototype.slice.call(box.querySelectorAll('th')).map(txt).slice(0, 16),
  rows: out});
"""

#: 弹窗根节点定位（供填数/确认复用）——同一个判据
JS_MODAL = """
var txt = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var all = Array.prototype.slice.call(document.querySelectorAll('div'));
var boxes = all.filter(function (d) {
  var st = null;
  try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  if (!(r.width > 400 && r.height > 200)) return false;
  return txt(d).indexOf('修改库存') >= 0 && d.querySelectorAll('tbody tr').length > 0;
});
if (!boxes.length) return null;
boxes.sort(function (a, b) {
  return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length;
});
return boxes[0];
"""


async def fill_add(cap, size: str, qty: int) -> str:
    """在某尺码行的「库存增减」输入框填 qty（「增减」保持默认「增加」）。

    定位与 `DIALOG_INFO` 用同一套锚点：属性集以尺码结尾的那一行 → 该行最后一个
    占位含「请输入」的 input。
    """
    body = """
var SIZE = %s, QTY = %s;
var txt = function (e) { return (e.innerText || '').replace(/[ \\t\\r\\n]+/g, ' ').trim(); };
var all = Array.prototype.slice.call(document.querySelectorAll('div'));
var boxes = all.filter(function (d) {
  var st = null;
  try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  if (!(r.width > 400 && r.height > 200)) return false;
  return txt(d).indexOf('修改库存') >= 0 && d.querySelectorAll('tbody tr').length > 0;
});
if (!boxes.length) return 'no-modal';
boxes.sort(function (a, b) {
  return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length;
});
var trs = Array.prototype.slice.call(boxes[0].querySelectorAll('tbody tr'));
for (var i = 0; i < trs.length; i++) {
  var cells = Array.prototype.slice.call(trs[i].querySelectorAll('td')).map(txt);
  var hit = false;
  for (var k = 0; k < cells.length; k++) {
    if (cells[k].length > SIZE.length && cells[k].length <= 16 &&
        cells[k].slice(-SIZE.length) === SIZE) { hit = true; break; }
  }
  if (!hit) continue;
  var ins = Array.prototype.slice.call(trs[i].querySelectorAll('input'));
  var qty = null;
  for (var j = ins.length - 1; j >= 0; j--) {
    if ((ins[j].placeholder || '').indexOf('请输入') >= 0) { qty = ins[j]; break; }
  }
  if (!qty) return 'no-qty-input:' + SIZE;
  return 'size=' + SIZE + ' -> ' + window.__zr.write(qty, String(QTY));
}
return 'size-row-not-found:' + SIZE;
""" % (json.dumps(size), json.dumps(str(qty)))
    return await ev_ok(cap, body)


async def read_dialog_state(cap) -> dict:
    body = """
var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
  var st = null;
  try { st = getComputedStyle(d); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = d.getBoundingClientRect();
  return r.width > 400 && r.height > 250;
});
var box = boxes[boxes.length - 1];
if (!box) return JSON.stringify({ok: false});
var txt = function (e) { return (e.innerText || '').replace(/\\s+/g, ' ').trim(); };
var trs = Array.prototype.slice.call(box.querySelectorAll('tbody tr'));
return JSON.stringify({
  ok: true,
  rows: trs.map(function (tr, i) {
    var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(txt);
    var ins = Array.prototype.slice.call(tr.querySelectorAll('input'));
    return {i: i, attr: cells[1] || '', cells: cells.slice(1, 9),
            vals: ins.map(function (x) { return x.value; })};
  })
});
"""
    return await ev_json(cap, body)


async def click_real(cap, xy) -> None:
    """真实鼠标点坐标（有些控件对合成事件无反应，如页大小下拉、确认按钮）。"""
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1},
                       timeout=20)
        await asyncio.sleep(0.2)
    await asyncio.sleep(1.2)


async def click_dialog_confirm(cap) -> str:
    """点「修改库存」弹窗的「确认」。

    ⚠ 会有**二次确认**弹窗（用户 2026-10-12 截图）：
      「建议SKU下每个发货仓的库存高于10件，否则可能导致以下风险…」+「返回修改 / 确认」，
      必须再点那个「确认」才真正提交。只点第一次的话**什么都不发生**（不报错、弹窗不关、
      服务端库存不变）—— 我为此误报过一次"成功"。
    """
    body = """
var txt = function (e) { return (e.innerText || '').replace(/[ \\t\\r\\n]+/g, ' ').trim(); };
function stockModal() {
  var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
    var st = null;
    try { st = getComputedStyle(d); } catch (e) { return false; }
    if (st.position !== 'fixed' && st.position !== 'absolute') return false;
    var r = d.getBoundingClientRect();
    if (!(r.width > 400 && r.height > 200)) return false;
    return txt(d).indexOf('库存增减') >= 0 && d.querySelectorAll('tbody tr').length > 0;
  });
  if (!boxes.length) return null;
  boxes.sort(function (a, b) {
    return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length; });
  return boxes[0];
}
function confirmBtns() {
  var bs = Array.prototype.slice.call(document.querySelectorAll('button'))
    .filter(function (b) { return txt(b) === '确认' && b.getClientRects().length > 0; });
  return bs;
}
var box = stockModal();
if (!box) return 'no-stock-modal';
var btns = Array.prototype.slice.call(box.querySelectorAll('button'))
  .filter(function (b) { return txt(b) === '确认'; });
if (!btns.length) return 'no-confirm';
var first = window.__zr.call(btns[btns.length - 1], 'onClick');
return 'step1: ' + first;
"""
    return await ev_ok(cap, body)


async def click_second_confirm(cap) -> str:
    """点二次确认弹窗（「建议SKU下每个发货仓的库存高于10件…」）里的「确认」。

    ⚠ 别假设它的文本/类名（我第一版按「建议SKU/返回修改」匹配，实测**匹配不到**）。
      更稳的做法：**页面上同时存在两个「确认」按钮**时，取**不在库存表弹窗里的那一个**
      （即新弹出来的风险确认框），并把它的坐标一并带回，方便调用方改用真实鼠标点。
    """
    body = """
var txt = function (e) { return (e.innerText || '').replace(/[ \\t\\r\\n]+/g, ' ').trim(); };
function stockModal() {
  var boxes = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (d) {
    var st = null;
    try { st = getComputedStyle(d); } catch (e) { return false; }
    if (st.position !== 'fixed' && st.position !== 'absolute') return false;
    var r = d.getBoundingClientRect();
    if (!(r.width > 400 && r.height > 200)) return false;
    return txt(d).indexOf('库存增减') >= 0 && d.querySelectorAll('tbody tr').length > 0;
  });
  if (!boxes.length) return null;
  boxes.sort(function (a, b) {
    return a.querySelectorAll('tbody tr').length - b.querySelectorAll('tbody tr').length; });
  return boxes[0];
}
var box = stockModal();
var all = Array.prototype.slice.call(document.querySelectorAll('button'))
  .filter(function (b) { return txt(b) === '确认' && b.getClientRects().length > 0; });
var outside = all.filter(function (b) { return !box || !box.contains(b); });
var warn = outside.length ? outside : all;
if (!warn.length) return 'no-confirm-anywhere';
warn.sort(function (a, b) {
  return (a.getBoundingClientRect().width * a.getBoundingClientRect().height) -
         (b.getBoundingClientRect().width * b.getBoundingClientRect().height); });
var pick = warn[0];
var r = pick.getBoundingClientRect();
var how = window.__zr.call(pick, 'onClick');
return JSON.stringify({how: how, n: all.length, outside: outside.length,
  xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)],
  cls: String(pick.className).slice(0, 40)});
"""
    return await ev_ok(cap, body)


# ------------------------------------------------------------------ 主流程
async def run(store: str, code: str, plan: list, apply: bool, attach: bool = False,
        precheck_only: bool = False) -> int:
    port = probe_port(WINDOWS[store])
    if not port:
        print("!! 探不到 %s 窗口的 CDP 端口" % store)
        return 3
    # ⚠ 必须用**前台**页：后台标签会被节流，Runtime 调用挂住不返回。
    #   · 默认新开一个前台页（人工筛选就在这一页做）
    #   · --attach：复用已打开的页（筛选已经做好、不想被重开清掉时用）
    if attach:
        tl = cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
        cands = [x for x in tl if x.get("type") == "page"
                 and "goods/list" in (x.get("url") or "")]
        if not cands:
            print("!! --attach 但没找到已打开的商品列表页")
            return 3
        tgt = cands[0]
        tid = tgt.get("id")
        print("复用已打开页 target:", str(tid)[:16])
    else:
        tid = await cdp_net.new_tab(port, URL_GOODS, foreground=True)
        tgt = None
        for _ in range(30):
            await asyncio.sleep(1)
            tgt = next((x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                        if (x.get("id") or x.get("targetId")) == tid), None)
            if tgt:
                break
        if not tgt:
            print("!! 拿不到新页 target")
            return 3
        print("用前台新页 target:", str(tid)[:16])
    ws = tgt["webSocketDebuggerUrl"]
    async with cdp_net.NetCapture(ws) as cap:
        for i in range(20):
            await asyncio.sleep(3)
            n = await cap.send("Runtime.evaluate",
                               {"expression": "document.querySelectorAll('tbody tr').length",
                                "returnByValue": True}, timeout=30)
            if (n.get("result") or {}).get("value"):
                print("列表已加载（%s 行）" % (n["result"]["value"]))
                break
        else:
            print("!! 页面 60s 内没加载出列表（登录态/网络？）")
            return 5
        try:
            await zreact.inject(cap)
        except Exception as e:  # noqa: BLE001
            print("!! 注入辅助失败:", str(e)[:150])
            return 5
        rows_seen = []
        try:
            r = await cap.send("Runtime.evaluate",
                               {"expression": "document.querySelectorAll('tbody tr').length",
                                "returnByValue": True}, timeout=30)
            rows_seen = list(range(int((r.get("result") or {}).get("value") or 0)))
        except Exception:  # noqa: BLE001
            pass
        print("① 先精准筛选该款（填完整 SKU 货号，精准查询才匹配得到）")
        # 用该款**计划内**的尺码去精准搜索（有些款没有 S/M，见 search_style 注释）
        plan_sizes = [p["size"] for p in plan] or None
        ok_search = await search_style(cap, store, code, plan_sizes)
        print("   筛选:", "成功" if ok_search else "未确认，继续尝试直接定位")
        # ② 打开「修改库存」——弹窗**偶发打不开**（页面状态漂移）⇒ 最多重试 3 轮：
        #    每轮之间重载商品列表页、重新精准筛选，再点一次。
        info = {}
        sku = ""
        for round_no in range(3):
            print("② 打开「修改库存」(%d/3):" % (round_no + 1),
                  await open_dialog(cap, code))
            # 弹窗是异步挂载的：读一次就判会误报"没打开"，轮询等最多 ~15s
            for _ in range(6):
                await asyncio.sleep(2.5)
                info = await ev_json(cap, DIALOG_INFO)
                if info.get("ok") and any(r.get("nInputs") for r in info.get("rows", [])):
                    break
            sku = (info.get("sku") or "").upper()
            if info.get("ok") and sku == code.upper():
                break
            # ⚠ 不要 Page.reload：实测重载后 SPA 久久不挂载（inputs=0），反而更糟。
            #   真正常见的失败形态是"链接点了但弹窗没出来"⇒ 关掉可能的残留弹窗、再点一次。
            print("   没打开（拿到 %r），关残留弹窗后重点一次" % (info.get("sku"),))
            try:
                await ev_ok(cap, """
var bs = Array.prototype.slice.call(document.querySelectorAll('button'))
  .filter(function (b) { return (b.innerText || '').trim() === '取消' && b.getClientRects().length; });
if (bs.length) { window.__zr.call(bs[bs.length - 1], 'onClick'); return 'closed'; }
return 'no-cancel';
""")
                await asyncio.sleep(3)
            except Exception as e:  # noqa: BLE001
                print("   关闭残留弹窗失败:", str(e)[:80])
        dlg_ok = bool(info.get("ok") and any(r.get("nInputs") for r in info.get("rows", []))
                      and sku == code.upper())
        if dlg_ok:
            print("   弹窗 SKU:", info.get("sku"), "（与目标一致 ✅）")
            print("   列头:", info.get("heads"))
            for r in info.get("rows", []):
                print("     尺码=%-5s 属性集=%-10s 当前库存=%-5s 修改后=%-5s 输入框占位=%s"
                      % (r.get("size"), r.get("attr"), r.get("curStock"),
                         r.get("afterStock"), r.get("qtyPh")))
        if not dlg_ok:
            print("\n!! 弹窗不是目标款（拿到 %r，期望 %r）或没打开。"
                  % (info.get("sku"), code))
            print("   请人工在该页筛一次（条件 SKU(精准查询) + 完整货号 %s-S）后加 --attach 重跑。"
                  % code)
            return 4

        if precheck_only:
            # 预检模式：只要弹窗能开、且款号对得上，就算这一款"可加"，立刻收工
            print("\n[预检] 弹窗可开且款号一致 ✅")
            return 0
        if not apply:
            print("\n(dry-run：未填数、未提交。加 --apply 才动手)")
            return 0

        # ---- 提交前先读一遍弹窗：把「计划」和「弹窗实际」对齐（保险之一）
        allowed = {p["size"]: p for p in plan}
        live = {r["size"]: r for r in info.get("rows", []) if r.get("size")}
        #: ⚠ 核对基准必须用**弹窗里的当前库存**，不能用面板导出的 TEMU 可售 ——
        #:   导出可能是几十分钟前的（当天已加过库存），拿它当基线会把成功判成失败
        #:   （2026-10-12 批量实测：`BHY-207202-10SetA` 面板 30、弹窗 9/10 ⇒ 全部误判"没生效"）。
        base = {}
        for size, r in live.items():
            try:
                base[size] = int(str(r.get("curStock")).strip())
            except (TypeError, ValueError):
                base[size] = None
        print("\n③ 逐尺码填「增加」数量（只填计划内尺码）")
        for size, p in allowed.items():
            if size not in live:
                print("   %-6s 跳过：弹窗里没有该尺码行" % size)
                continue
            print("   %-6s +%-4s（弹窗当前库存 %s）→ %s"
                  % (size, p["add"], live[size].get("curStock"),
                     await fill_add(cap, size, p["add"])))
            await asyncio.sleep(0.5)

        st = await ev_json(cap, DIALOG_INFO)
        print("\n④ 提交前回读（核对「库存增减」是否等于计划，以及「修改后库存」）:")
        bad = []
        for r in st.get("rows", []):
            size = r.get("size")
            want = allowed.get(size, {}).get("add")
            got = r.get("qtyValue")
            try:
                got_n = int(str(got).strip()) if got not in (None, "") else None
            except ValueError:
                got_n = None
            flag = "OK" if (want is None or got_n == want) else "**不一致**"
            if want is not None and got_n != want:
                bad.append((size, want, got))
            print("     %-4s 当前库存=%-5s 增减填=%-5s 计划=%-5s 修改后=%-6s 尾格=%s %s"
                  % (size, r.get("curStock"), got, want if want is not None else "—",
                     r.get("afterStock"), r.get("tail"), flag))
        if bad:
            print("\n!! 填数与计划不一致，已中止提交（不会点确认）：%s" % bad)
            return 6
        # ⑤ 提交：点「确认」→（可能弹出"库存低于10件"风险框）→ 再点它的「确认」
        #    ⚠ 第一次确认有时点了没反应（批量实测偶发）⇒ 轮询确认状态并在必要时重试。
        for attempt in range(3):
            print("\n⑤ 点第 %d 次「确认」:" % (attempt + 1),
                  await click_dialog_confirm(cap))
            await asyncio.sleep(3)
            r2 = await click_second_confirm(cap)
            print("   二次确认:", r2)
            if r2 != "no-confirm-anywhere":
                try:
                    d2 = json.loads(r2 or "{}")
                    if d2.get("xy"):
                        print("   真实鼠标补点", d2["xy"])
                        await click_real(cap, d2["xy"])
                except Exception:  # noqa: BLE001
                    pass
                break
            # 没有二次确认框：可能是"点了没反应"，检查库存弹窗是否还在（在就重试）
            await asyncio.sleep(4)
            cur = await ev_json(cap, DIALOG_INFO)
            if not cur.get("ok"):
                print("   库存弹窗已关闭（无需二次确认）")
                break
        await asyncio.sleep(7)
        # ⑥ **以服务端为准**判定成功：重开弹窗读该尺码的当前库存
        after = await ev_json(cap, DIALOG_INFO)
        if after.get("ok"):
            print("\n⑥ 提交后弹窗仍在，回读（表单未提交）:")
            for r in after.get("rows", []):
                print("     %-4s 当前库存=%-5s 修改后=%s"
                      % (r.get("size"), r.get("curStock"), r.get("afterStock")))
            print("\n!! 弹窗没关 ⇒ 很可能没提交成功。请确认是否还有二次确认未点。")
        else:
            # 弹窗关了：重开一次看服务端值是否真的变了（这才是成功判据）
            print("\n⑥ 弹窗已关闭，重开核对服务端值……")
            await asyncio.sleep(2)
            await open_dialog(cap, code)
            await asyncio.sleep(5)
            chk = await ev_json(cap, DIALOG_INFO)
            want = {p["size"]: p for p in plan}
            allok = True
            for r in chk.get("rows", []):
                sz = r.get("size")
                if sz not in want:
                    continue
                got = r.get("curStock")
                try:
                    gotn = int(str(got).strip())
                except ValueError:
                    gotn = None
                # 期望 = **弹窗基线 + 加量**（面板导出可能过时，不能当基线）
                b0 = base.get(sz)
                exp = (b0 + want[sz]["add"]) if b0 is not None else None
                flag = "✅" if (exp is not None and gotn == exp) else "**没生效**"
                if exp is None:
                    flag = "?（弹窗没读到基线）"
                elif gotn != exp:
                    allok = False
                print("     %-4s 服务端当前库存=%-5s 期望=%-5s %s"
                      % (sz, got, exp, flag))
            print("\n结论:", "✅ 提交成功（服务端值已变）" if allok
                  else "❌ 提交未生效（服务端值没变）")
    return 0


def codes_of(store: str) -> list:
    """该店当前**有加量**的款号（只取面板判为「可加库存」的行）。"""
    goods = json.loads(GOODS_JSON.read_text(encoding="utf-8"))
    s = (goods.get("stores") or {}).get(store) or {}
    out = []
    for r in s.get("lowStock", {}).get("rows", []):
        if (r.get("verdict") or "") != "可加库存":
            continue
        if (r.get("suggestAdd") or 0) <= 0:
            continue
        if r["code"] not in out:
            out.append(r["code"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 加库存（+ 到货≤7天开预售），仅加州仓")
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--code", default=None, help="款号（不带店铺前缀也可）")
    ap.add_argument("--all", action="store_true",
                    help="该店**所有**「可加库存」的款（批量模式；与 --code 二选一）")
    ap.add_argument("--apply", action="store_true", help="真的填数并提交")
    ap.add_argument("--attach", action="store_true",
                    help="复用已打开的商品列表页（保留了手工筛选结果）")
    ap.add_argument("--stop-after-fails", type=int, default=3,
                    help="批量模式：连续失败这么多款就停（默认 3）")
    ap.add_argument("--allow-partial", action="store_true",
                    help="预检没 100% 通过时也继续（默认拒绝提交，未过的款标 SKIPPED）")
    ap.add_argument("--precheck", action="store_true",
                    help="只做预检（逐款确认弹窗可开），不填数不提交")
    a = ap.parse_args()
    store = a.store.upper()
    cfg = load_cfg()

    if a.all:
        todo = codes_of(store)
        print("=== %s 批量加库存：%d 个款 ===" % (store, len(todo)))
        if not todo:
            print("（没有「可加库存」的款，收工）")
            return 0
        # ---- 门槛：提交前先预检每个款「修改库存弹窗能不能开」
        #   用户 2026-10-13 要求：开不了弹窗的款要**显式标 SKIPPED 并汇总**，
        #   不允许"静默漏加"；**只有预检 100% 通过才允许真提交**。
        skipped = []
        if a.apply:
            print("\n--- 预检阶段：逐款确认弹窗可开（不填数、不提交）---")
            for i, code in enumerate(todo, 1):
                plan = build_plan(store, code, cfg["加库存"])
                if not plan:
                    skipped.append((code, "无可加尺码"))
                    print("[%d/%d] %s SKIPPED（无可加尺码）" % (i, len(todo), code))
                    continue
                rc_pre = asyncio.run(run(store, code, plan, False, a.attach,
                                         precheck_only=True))
                if rc_pre != 0:
                    skipped.append((code, "弹窗打不开 rc=%s" % rc_pre))
                    print("[%d/%d] %s SKIPPED（弹窗打不开）" % (i, len(todo), code))
                else:
                    print("[%d/%d] %s 预检 OK" % (i, len(todo), code))
            ok_n = len(todo) - len(skipped)
            print("\n--- 预检结果：%d/%d 可加，%d 个 SKIPPED ---"
                  % (ok_n, len(todo), len(skipped)))
            for code, why in skipped:
                print("   SKIPPED %-26s %s" % (code, why))
            if skipped:
                print("\n!! 预检未 100% 通过 ⇒ **拒绝自动提交**"
                      "（避免无人值守时静默漏加）。")
                print("   处理办法：① 人工在页面上筛一次这些款再看；"
                      "② 或在 --apply 时加 --allow-partial 明确接受跳过。")
                if not a.allow_partial:
                    outdir = OUT_DIR
                    outdir.mkdir(parents=True, exist_ok=True)
                    p = outdir / ("%s_SKIPPED_%s.json"
                                  % (store, datetime.now().strftime("%Y%m%d-%H%M%S")))
                    p.write_text(json.dumps({"store": store, "skipped": skipped},
                                            ensure_ascii=False, indent=1),
                                 encoding="utf-8")
                    print("   SKIPPED 清单已存:", p)
                    return 7
            todo = [c for c in todo if c not in {s[0] for s in skipped}]
        results, fails_in_row, rc = [], 0, 0
        for idx, code in enumerate(todo, 1):
            plan = build_plan(store, code, cfg["加库存"])
            head = "[%d/%d] %s" % (idx, len(todo), code)
            if not plan:
                print("%s 跳过（无可加尺码）" % head)
                continue
            print("\n%s 计划：%s" % (head, "、".join("%s+%d" % (p["size"], p["add"])
                                                  for p in plan)))
            if not a.apply:
                results.append({"code": code, "plan": plan, "applied": False})
                continue
            rc = asyncio.run(run(store, code, plan, True, a.attach))
            results.append({"code": code, "plan": plan, "applied": True, "rc": rc})
            if rc == 0:
                fails_in_row = 0
            else:
                fails_in_row += 1
                if fails_in_row >= a.stop_after_fails:
                    print("\n!! 连续 %d 个款失败，停止批量（避免把错误状态放大）"
                          % fails_in_row)
                    break
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / ("%s_ALL_%s%s.json"
                         % (store, datetime.now().strftime("%Y%m%d-%H%M%S"),
                            "" if a.apply else "_DRYRUN"))
        out.write_text(json.dumps({"store": store, "applied": bool(a.apply),
                                   "口径": cfg["加库存"], "results": results},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        ok = sum(1 for r in results if r.get("applied") and r.get("rc") == 0)
        bad = sum(1 for r in results if r.get("applied") and r.get("rc") not in (0, None))
        print("\n=== %s 批量完成：成功 %d 款，失败 %d 款，计划共 %d 款 ===\n清单: %s"
              % (store, ok, bad, len(results), out))
        return 1 if bad else 0

    if not a.code:
        print("!! 要么给 --code <款号>，要么给 --all（批量该店所有可加库存的款）")
        return 2
    code = a.code.strip()
    # ⚠ **不要**把款号转全大写：SKU 货号里带小写（`BHY-102204-6SetC-S`），
    #   精准查询区分大小写，转大写会查不到（2026-10-12 实测 0 行）。
    if code.upper().startswith(store + "-"):
        code = code[len(store) + 1:]          # 去掉已有前缀（保留大小写）
    code = store + "-" + code
    plan = build_plan(store, code, cfg["加库存"])
    print_plan(plan, code)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / ("%s_%s%s.json" % (store, datetime.now().strftime("%Y%m%d-%H%M%S"),
                                       "" if a.apply else "_DRYRUN"))
    out.write_text(json.dumps({"store": store, "code": code, "applied": bool(a.apply),
                               "口径": cfg["加库存"], "plan": plan},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("计划已存:", out)
    if not plan:
        return 0
    return asyncio.run(run(store, code, plan, a.apply, a.attach))


if __name__ == "__main__":
    raise SystemExit(main())
