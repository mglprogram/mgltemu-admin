# -*- coding: utf-8 -*-
"""在「设置库存」页给商品添加 恒信仓 发货仓，并把该仓每个尺码的当前库存填 0。

链路（2026-11-17 实测）：
    商品列表-在售中 → 修改库存 → 「站点与发货仓关系设置」→ 会**新开**
    `/goods/stock-edit?productId=<SPU>` → 「发货仓」多选勾上 恒信仓（BHY=`恒信加州3`
    / BEL=`恒信加州3仓`）→ 每个尺码的「当前库存」填 0 → 提交

用户口径（2026-11-17）：
    · 选完仓库后每个尺码**直接填 0**；
    · 填完**直接提交**；
    · **禁止一直重复打开设置库存页** —— 全程复用同一个标签页，一个 SPU 只导航一次，
      提交后靠页面跳转/提示判成功，不再重开核对。

用法：
    python scripts/goods/add_hx_warehouse.py --store BHY --spu 48740150939            # 只看
    python scripts/goods/add_hx_warehouse.py --store BHY --spu 48740150939 --apply    # 真提交
    python scripts/goods/add_hx_warehouse.py --store BHY --spu-file _logs/spus.txt --apply
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

import cdp_net  # noqa: E402\nimport zreact  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]

#: 各店「恒信仓」在 TEMU 下拉里的真值（README 549~550 行）
HX_NAME = {"BEL": "恒信加州3仓", "BHY": "恒信加州3"}
WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF", "KLS": "TM-KinglamanSport"}
URL = "https://agentseller.temu.com/goods/stock-edit?productId=%s"
SEL = "#siteWarehouseRouteMap\\.site_100\\.warehouseId"

JS_HEAD_XY = r"""
var el = document.querySelector(%s);
if (!el) return 'null';
try { el.scrollIntoView({block: 'center'}); } catch (e) {}
var r = el.getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);
""" % json.dumps(SEL + ' [data-testid="beast-core-select-header"]')

JS_OPTS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
Array.prototype.forEach.call(document.querySelectorAll('[class*="cIL_item"]'), function (e) {
  var s = t(e);
  if (!s || s.length > 30 || !e.getClientRects().length) return;
  var r = e.getBoundingClientRect();
  if (r.height < 8) return;
  out.push({txt: s, active: /active/.test(String(e.className)),
            xy: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]});
});
return JSON.stringify(out);
"""

JS_TAGS = r"""
var el = document.querySelector(%s);
if (!el) return 'null';
var tags = Array.prototype.slice.call(el.querySelectorAll('[data-testid="beast-core-tagGroup-tag"]'))
  .map(function (e) { return (e.innerText || '').trim(); });
return JSON.stringify(tags);
""" % json.dumps(SEL)

JS_BTN = r"""
var t = function (e) { return (e.innerText || '').trim(); };
var bs = Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (b) {
  return t(b) === %s && b.getClientRects().length > 0 && !b.disabled; });
if (!bs.length) return 'null';
var r = bs[bs.length - 1].getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);
"""

JS_POPUP = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var d = Array.prototype.slice.call(document.querySelectorAll('div')).filter(function (x) {
  var st = null; try { st = getComputedStyle(x); } catch (e) { return false; }
  if (st.position !== 'fixed' && st.position !== 'absolute') return false;
  var r = x.getBoundingClientRect();
  if (r.width < 200 || r.height < 60) return false;
  var s = t(x);
  return s.indexOf('待办任务') >= 0 || s.indexOf('更新待办') >= 0;
});
if (!d.length) return 'none';
d.sort(function (a, b) { return a.getBoundingClientRect().width - b.getBoundingClientRect().width; });
return t(d[0]).slice(0, 120);
"""

JS_FILL_QTY = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var want = %s;
var qtyRaw = %s;                   /* {"colors":{颜色:{S:..}},"sizes":{S:..}} 或旧的扁平 {S:..} */
var byColor = (qtyRaw && qtyRaw.colors) ? qtyRaw.colors : {};
var bySize = (qtyRaw && qtyRaw.sizes) ? qtyRaw.sizes : (qtyRaw || {});
var colorKeys = Object.keys(byColor);
var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
var SIZE_RE = /^(XXS|XS|S|M|L|XL|XXL|XXXL|[2-6]XL)$/;
var pickSize = function (cells) {
  for (var k = 0; k < cells.length; k++) {
    var parts = String(cells[k]).split(/[,，\/\s]+/);
    for (var j = 0; j < parts.length; j++) {
      if (SIZE_RE.test(parts[j])) return parts[j];
    }
  }
  return '';
};
var pickColor = function (cells) {
  for (var k = 0; k < cells.length; k++) {
    if (colorKeys.indexOf(cells[k]) >= 0) return cells[k];
  }
  return '';
};
/* 尺码/颜色在 td 上可能是 rowspan 合并的：按 DOM 顺序继承上一行的值 */
var rows = [];
var curSize = '', curColor = '';
Array.prototype.forEach.call(document.querySelectorAll('tbody tr'), function (tr) {
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var s = pickSize(cells);
  if (s) curSize = s;
  var c = pickColor(cells);
  if (c) curColor = c;
  var isHx = Array.prototype.some.call(tr.querySelectorAll('td'), function (td) {
    return t(td).indexOf(want) >= 0; });
  if (isHx) rows.push({tr: tr, size: curSize, color: curColor});
});
var cnt = {};
rows.forEach(function (o) {
  var k = o.color + '|' + o.size;
  cnt[k] = (cnt[k] || 0) + 1;
});
var done = [];
rows.forEach(function (o) {
  var m = byColor[o.color] || bySize;
  var base = (m && m[o.size] !== undefined) ? m[o.size] : 0;
  /* 同一（颜色,尺码）出现多行时均分，避免总量翻倍 */
  var v = String(Math.floor(base / Math.max(1, cnt[o.color + '|' + o.size] || 1)));
  Array.prototype.forEach.call(o.tr.querySelectorAll('input'), function (i) {
    setter.call(i, v);
    i.dispatchEvent(new Event('input', {bubbles: true}));
    i.dispatchEvent(new Event('change', {bubbles: true}));
    i.dispatchEvent(new Event('blur', {bubbles: true}));
    done.push((o.color ? o.color + '/' : '') + o.size + '=' + i.value);
  });
});
return JSON.stringify(done);
"""

JS_FILL_ZERO = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var want = %s;
var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr')).filter(function (tr) {
  return Array.prototype.some.call(tr.querySelectorAll('td'), function (td) { return t(td) === want; });
});
var done = [];
trs.forEach(function (tr) {
  Array.prototype.forEach.call(tr.querySelectorAll('input'), function (i) {
    setter.call(i, '0');
    i.dispatchEvent(new Event('input', {bubbles: true}));
    i.dispatchEvent(new Event('change', {bubbles: true}));
    i.dispatchEvent(new Event('blur', {bubbles: true}));
    done.push(i.value);
  });
});
return JSON.stringify(done);
"""


JS_READ_HX = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var want = %s;
var out = [];
Array.prototype.forEach.call(document.querySelectorAll('tbody tr'), function (tr) {
  if (!Array.prototype.some.call(tr.querySelectorAll('td'), function (td) { return t(td) === want; })) return;
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var size = '';
  for (var k = 0; k < cells.length; k++) {
    if (/^(XXS|XS|S|M|L|XL|XXL|XXXL|[2-6]XL)$/.test(cells[k])) { size = cells[k]; break; }
  }
  var ins = Array.prototype.slice.call(tr.querySelectorAll('input'));
  out.push(size + '=' + (ins.length ? ins[0].value : ''));
});
return JSON.stringify(out);
"""


def _xy(raw):
    try:
        return json.loads(raw or "null")
    except Exception:  # noqa: BLE001
        return None


async def _eval(sess, js, timeout=60):
    body = "(function () {\n%s\n})()" % js
    r = await sess.send("Runtime.evaluate",
                        {"expression": body, "returnByValue": True, "awaitPromise": True},
                        timeout=timeout)
    if (r or {}).get("exceptionDetails"):
        raise RuntimeError("JS 出错: %s" % str(r["exceptionDetails"])[:200])
    return (r or {}).get("result", {}).get("value")


async def _click(sess, xy):
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await sess.send("Input.dispatchMouseEvent",
                        {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1},
                        timeout=20)
        await asyncio.sleep(0.12)


async def _wait_tags(sess, seconds: int = 40, log=None):
    """等「发货仓」多选渲染出来；遇到待办任务弹窗就刷新（用户 2026-11-17：碰到弹窗直接刷新）。"""
    reloaded = 0
    for _ in range(seconds):
        try:
            tags = await _eval(sess, JS_TAGS)
        except Exception:  # noqa: BLE001
            tags = "null"          # 导航中上下文销毁，继续等
        if tags and tags != "null":
            return json.loads(tags)
        pop = await _eval(sess, JS_POPUP)
        if pop and pop != "none" and reloaded < 3:
            reloaded += 1
            if log:
                log("  弹窗（%s）→ 刷新" % pop[:30])
            await sess.send("Page.reload", {"ignoreCache": False}, timeout=30)
            await asyncio.sleep(4)
            continue
        await asyncio.sleep(1)
    return None


JS_TAG_REMOVE = r"""
var t = function (e) { return (e.innerText || '').trim(); };
var drops = %s;
var findProps = function (el) {
  if (!el) return null;
  var ks = Object.keys(el).filter(function (k) { return k.indexOf('__reactProps') === 0; });
  for (var i = 0; i < ks.length; i++) {
    var p = el[ks[i]];
    if (p && typeof p === 'object') return p;
  }
  return null;
};
var removed = [];
Array.prototype.forEach.call(
  document.querySelectorAll('[data-testid="beast-core-tagGroup-tag"]'), function (tag) {
    var s = t(tag);
    if (!drops.some(function (d) { return s.indexOf(d) >= 0; })) return;
    var el = tag, props = null, hit = null;
    for (var i = 0; i < 4 && el; i++) {
      props = findProps(el);
      if (props && typeof props.onClose === 'function') { hit = 'onClose'; break; }
      if (props && typeof props.onRemove === 'function') { hit = 'onRemove'; break; }
      el = el.parentElement;
    }
    if (!hit) return;
    try {
      props[hit]({target: el, currentTarget: el, bubbles: true,
                  stopPropagation: function () {}, preventDefault: function () {},
                  nativeEvent: {}});
      removed.push(s + ':' + hit);
    } catch (e) {
      removed.push(s + ':threw');
    }
});
return JSON.stringify(removed);
"""


async def _drop_warehouses(sess, drops, log=print) -> list:
    """把发货仓多选里命中 drops 的仓取消勾选（kansas / 新泽西）。

    ⚠ 2026-11-17 实测：标签上的 × 用鼠标点、用 React onClose 直调**都不生效**
      （after_drop 原样不动）。可靠做法＝重新打开「发货仓」下拉，把已勾中的目标项
      **再点一次取消**（和勾恒信仓同一套机制）。
    """
    removed: list[str] = []
    xy = _xy(await _eval(sess, JS_HEAD_XY))
    if not xy:
        return removed
    opts = []
    for _ in range(3):                    # 下拉偶尔没弹开
        await _click(sess, xy)
        await asyncio.sleep(1.5)
        opts = json.loads(await _eval(sess, JS_OPTS) or "[]")
        if opts:
            break
        await asyncio.sleep(1.0)
    for _ in range(6):
        target = next((o for o in opts
                       if o.get("active") and any(d in o["txt"] for d in drops)), None)
        if not target:
            break
        await _click(sess, target["xy"])
        removed.append(target["txt"])
        await asyncio.sleep(1.0)
        opts = json.loads(await _eval(sess, JS_OPTS) or "[]")
    if opts:                              # 收起下拉
        await _click(sess, xy)
        await asyncio.sleep(0.8)
        if json.loads(await _eval(sess, JS_OPTS) or "[]"):
            await _click(sess, xy)
            await asyncio.sleep(0.6)
    if removed:
        log("  取消发货仓：%s" % removed)
    return removed


async def _ensure_ca(sess, log=print) -> str:
    """没有加州仓就补一个（用户 2026-11-17：原本只有 kansas/新泽西 的款，删完要加加州3/三仓）。

    返回 'had' / 仓名 / 'no-option'。
    """
    tags = json.loads(await _eval(sess, JS_TAGS) or "[]")
    if any("加州" in x and "恒信" not in x for x in tags):
        return "had"
    xy = _xy(await _eval(sess, JS_HEAD_XY))
    if not xy:
        return "no-head"
    opts = []
    for _ in range(3):
        await _click(sess, xy)
        await asyncio.sleep(1.5)
        opts = json.loads(await _eval(sess, JS_OPTS) or "[]")
        if opts:
            break
        await asyncio.sleep(1.0)
    target = next((o for o in opts
                   if "加州" in o["txt"] and "恒信" not in o["txt"] and "乐航" not in o["txt"]),
                  None)
    if not target:
        if opts:
            await _click(sess, xy)
        return "no-option"
    await _click(sess, target["xy"])
    await asyncio.sleep(1.0)
    await _click(sess, xy)                    # 收起下拉
    await asyncio.sleep(0.6)
    log("  补加州仓：%s" % target["txt"])
    return target["txt"]


async def add_one(sess, store: str, spu: str, apply: bool, log=print,
                  qty_map: dict | None = None, force: bool = False,
                  combo_mode: str = "ask", drops: list | None = None,
                  ensure_ca: bool = False, zeros: list | None = None) -> dict:
    """在**已 attach 的那个标签页**里处理一个 SPU（不新开页、不重复打开）。

    force=True：即使该款已有恒信仓，也照样填量并提交（改量用）。
    zeros：要一并清零的发货仓名列表（用户 2026-11-19：恒信加完库存后把 加州3仓 清零）。
    """
    out = {"spu": spu, "store": store, "ok": False}
    hx = HX_NAME.get(store)          # 未登记的店：从下拉里自动认「含 恒信 的那个」

    await sess.send("Page.navigate", {"url": URL % spu}, timeout=30)
    tags = await _wait_tags(sess, 40, log=log)
    if tags is None:
        out["why"] = "页面没加载出发货仓"
        return out
    out["before"] = tags
    has_hx = [t for t in tags if "恒信" in t]
    if has_hx:
        hx = has_hx[0]
    if has_hx and not force:
        out.update(ok=True, skipped=True)
        log("  %s 已有 %s，跳过" % (spu, has_hx))
        return out

    if not has_hx:
        xy = _xy(await _eval(sess, JS_HEAD_XY))
        if not xy:
            out["why"] = "找不到发货仓下拉"
            return out
        opts = []
        for _try in range(3):           # 下拉偶尔没弹开（options 为空）→ 重点一次
            await _click(sess, xy)
            await asyncio.sleep(1.5)
            opts = json.loads(await _eval(sess, JS_OPTS) or "[]")
            if opts:
                break
            await asyncio.sleep(1.0)
        target = next((o for o in opts if o["txt"] == hx), None) if hx else None
        if not target:
            target = next((o for o in opts if "恒信" in o["txt"]), None)
        if not target:
            out["why"] = "下拉里没有恒信仓：%s" % [o["txt"] for o in opts]
            return out
        hx = target["txt"]
        out["hx"] = hx
        await _click(sess, target["xy"])
        await asyncio.sleep(1.0)
        tags2 = json.loads(await _eval(sess, JS_TAGS) or "[]")
        out["picked"] = tags2
        if hx not in tags2:
            out["why"] = "勾选没生效：%s" % tags2
            return out

    if drops:
        out["dropped"] = await _drop_warehouses(sess, drops, log=log)
        tags_now = json.loads(await _eval(sess, JS_TAGS) or "[]")
        out["after_drop"] = tags_now
        if hx not in tags_now:
            out["why"] = "删仓库后恒信仓不见了：%s" % tags_now
            return out
        if ensure_ca:
            out["ca"] = await _ensure_ca(sess, log=log)
            out["after_ca"] = json.loads(await _eval(sess, JS_TAGS) or "[]")

    qm = qty_map or {}
    filled = []
    for _ in range(20):                     # 勾完仓库后表格重渲染要几百 ms~几秒
        filled = json.loads(await _eval(
            sess, JS_FILL_QTY % (json.dumps(hx, ensure_ascii=False),
                                 json.dumps(qm, ensure_ascii=False))) or "[]")
        if filled and not any(f.startswith("=") for f in filled):   # 尺码没认出来就重试
            break
        await asyncio.sleep(0.8)
    out["filled"] = filled
    log("  %s 已勾 %s，填数 %s" % (spu, hx, filled))
    ca = out.get("ca")
    if ensure_ca and ca and ca not in ("had", "no-option", "no-head"):
        # 新补的加州仓也必须填数，否则提交被「请填写当前库存」拦住 → 先填 0
        for _ in range(10):
            got = json.loads(await _eval(
                sess, JS_FILL_QTY % (json.dumps(ca, ensure_ascii=False),
                                     json.dumps({"sizes": {}, "colors": {}}))) or "[]")
            if got:
                break
            await asyncio.sleep(0.8)
        out["ca_filled"] = got
        log("  %s 加州仓先填 0：%s" % (spu, got))
    if not filled:
        out["why"] = "没找到恒信仓的库存输入框"
        return out
    # 其他发货仓清零（2026-11-19 用户口径：恒信仓加上库存后，加州3仓清 0）
    if zeros:
        out["zeroed"] = {}
        for zname in zeros:
            zgot = []
            for _ in range(8):
                zgot = json.loads(await _eval(
                    sess, JS_FILL_ZERO % json.dumps(zname, ensure_ascii=False)) or "[]")
                if zgot:
                    break
                await asyncio.sleep(0.8)
            out["zeroed"][zname] = zgot
            log("  %s %s 清零：%s" % (spu, zname, zgot))
    await asyncio.sleep(0.8)
    if not apply:
        out.update(ok=True, dry=True)
        log("  %s 勾选+填 0 完成（未提交）" % spu)
        return out

    btn = _xy(await _eval(sess, JS_BTN % json.dumps("提交")))
    if not btn:
        out["why"] = "找不到提交按钮"
        return out
    await _click(sess, btn)
    await asyncio.sleep(2.5)
    for _ in range(3):
        got = _xy(await _eval(sess, JS_BTN % json.dumps("确认", ensure_ascii=False))) \
            or _xy(await _eval(sess, JS_BTN % json.dumps("确定", ensure_ascii=False)))
        if not got:
            break
        log("  确认框 → 点确认")
        await _click(sess, got)
        await asyncio.sleep(2)
    await asyncio.sleep(2)

    # 组合品弹窗：「绑定并切换合作仓发货 / 取消」（--combo bind 时自动点前者）
    combo = _xy(await _eval(
        sess, JS_BTN % json.dumps("绑定并切换合作仓发货", ensure_ascii=False)))
    if combo:
        body0 = await _eval(
            sess, "return (document.body.innerText||'').replace(/[ \\t]+/g,' ');") or ""
        out["combo_modal"] = body0[-160:]
        if combo_mode == "bind":
            log("  组合品弹窗 → 点「绑定并切换合作仓发货」")
            await _click(sess, combo)
            await asyncio.sleep(2.5)
            for _ in range(2):
                got = _xy(await _eval(
                    sess, JS_BTN % json.dumps("确认", ensure_ascii=False))) \
                    or _xy(await _eval(
                        sess, JS_BTN % json.dumps("确定", ensure_ascii=False)))
                if not got:
                    break
                await _click(sess, got)
                await asyncio.sleep(2)
            await asyncio.sleep(2)
        elif combo_mode != "probe":
            out["why"] = "组合品弹窗（未处理）：%s" % out["combo_modal"][-80:]
            log("  %s 组合品弹窗，按 --combo 设置跳过" % spu)
            return out

    url_now = await _eval(sess, "return location.href;")
    body = await _eval(sess, "return (document.body.innerText||'').replace(/[ \\t]+/g,' ');") or ""
    out["url"] = url_now
    if "stock-edit" not in (url_now or ""):
        out.update(ok=True)
        log("  %s 提交成功（页面已返回商品列表）" % spu)
        # 回读一次同页数值（同一个标签页再导航一次，不是新开页）
        await sess.send("Page.navigate", {"url": URL % spu}, timeout=30)
        if await _wait_tags(sess, 30):
            out["values"] = json.loads(
                await _eval(sess, JS_READ_HX % json.dumps(hx, ensure_ascii=False)) or "[]")
            log("  %s 回读恒信仓：%s" % (spu, out["values"]))
        return out
    if "请填写" in body or "请选择" in body:
        out["why"] = "提交被拦：%s" % body[-120:]
    else:
        tags3 = json.loads(await _eval(sess, JS_TAGS) or "[]")
        out["after"] = tags3
        out["ok"] = hx in tags3
        if not out["ok"]:
            out["why"] = "提交后没看到 %s：%s" % (hx, tags3)
    log("  %s 提交结果：%s" % (spu, json.dumps(out, ensure_ascii=False)[:200]))
    return out


def build_qty_map(spu: str, store: str, goods_path: str, stock_path: str,
                  divisor: int, code_map: dict | None = None) -> dict:
    """按 SPU 算「恒信仓该填多少」：领星 LM739 该 SKU 的可用库存 // divisor。

    · 领星 productName 形如 `102104-5SetA-S`（无店前缀）；
    · TEMU 货号形如 `BHY-102104-5SetA-S` ⇒ 去掉第一段前缀再匹配；
    · 同码多色时按尺码合计（页面按尺码着色，做不到拆到色）。
    """
    try:
        goods = json.loads(Path(goods_path).read_text(encoding="utf-8"))
        items = ((goods.get("stores") or {}).get(store) or {}).get("items") or []
        stock = json.loads(Path(stock_path).read_text(encoding="utf-8"))
        stock_items = stock.get("items") or []

        def qty_of(code: str) -> dict:
            key = code.split("-", 1)[1] if "-" in code else code
            avail: dict[str, float] = {}
            for it in stock_items:
                pn = it.get("productName") or ""
                if not pn.startswith(key + "-"):
                    continue
                size = pn.rsplit("-", 1)[-1]
                avail[size] = avail.get(size, 0.0) + float(it.get("avail") or 0)
            return {s: int(v // divisor) for s, v in avail.items()}

        forced = str((code_map or {}).get(str(spu)) or "")
        # 用 --code-map 给了货号（KLS 这类不在 goods.json 里的店）就直接按该货号算，
        # 不能往下走「多颜色」分支——那里要靠 goods.json 的 skus，拿不到就会返回空
        if forced:
            return {"sizes": qty_of(forced), "colors": {}}
        codes = [
            (x.get("code"), "") for x in items if str(x.get("spuId")) == str(spu) and x.get("code")]
        if not codes:
            return {}
        if len(codes) == 1:
            return {"sizes": qty_of(codes[0][0]), "colors": {}}

        # 同一 SPU 多个货号（不同颜色）：按颜色分别算，页面按颜色拆行
        by_color: dict[str, dict] = {}
        merged: dict[str, float] = {}
        for x in items:
            if str(x.get("spuId")) != str(spu) or not x.get("code"):
                continue
            for s in x.get("skus") or []:
                color = str(s.get("color") or "")
                if not color:
                    continue
                by_color[color] = qty_of(x["code"])
        for m in by_color.values():
            for s, v in m.items():
                merged[s] = max(merged.get(s, 0), v)
        return {"colors": by_color, "sizes": {s: int(v) for s, v in merged.items()}}
    except Exception as e:  # noqa: BLE001
        print("  [warn] 算库存失败 %s: %s" % (spu, e))
        return {}


async def main_async(a) -> int:
    port = a.port or _probe_port(WINDOWS[a.store])
    if not port:
        print("窗口没开：%s（可用 --port 指定 CDP 端口）" % WINDOWS[a.store])
        return 2
    spus = []
    if a.spu:
        spus.append(a.spu)
    if a.spu_file:
        spus += [x.strip() for x in Path(a.spu_file).read_text(encoding="utf-8").split()
                 if x.strip()]
    if not spus:
        print("没给 SPU")
        return 2

    code_map = {}
    if a.code_map:
        code_map = json.loads(Path(a.code_map).read_text(encoding="utf-8"))

    # ★ 全程一个标签页（用户 2026-11-17：禁止反复打开设置库存页）
    async def _attach(url: str):
        t = await cdp_net.new_tab(port, url, foreground=True)
        inf = next(x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                   if x.get("id") == t)
        return t, await cdp_net.NetCapture(inf["webSocketDebuggerUrl"]).__aenter__()

    tid, sess = await _attach(URL % spus[0])
    bad = 0
    try:
        for i, spu in enumerate(spus):
            print("[%d/%d] SPU %s" % (i + 1, len(spus), spu))
            qm = {}
            if a.divisor and a.divisor > 1:
                qm = build_qty_map(spu, a.store, a.goods, a.stock_json, a.divisor, code_map)
                print("   恒信仓填量（领星可用//%d）: %s" % (a.divisor, qm))
            try:
                if sess is None:                       # 上一轮 CDP 断了 → 重开一页接着跑
                    tid, sess = await _attach(URL % spu)
                r = await add_one(sess, a.store, spu, a.apply, qty_map=qm, force=a.force,
                                  combo_mode=a.combo,
                                  drops=[x.strip() for x in a.drop.split(",") if x.strip()],
                                  ensure_ca=a.ensure_ca,
                                  zeros=[x.strip() for x in a.zero.split(",") if x.strip()])
            except Exception as e:                     # noqa: BLE001
                # 2026-11-17：长跑会被 TimeoutError / ConnectionClosed 打断，别整批挂掉
                print("  [err] %s 异常：%s" % (spu, str(e)[:160]))
                r = {"spu": spu, "store": a.store, "ok": False,
                     "why": "异常:%s" % str(e)[:120]}
                try:
                    await sess.send("Target.closeTarget", {"targetId": tid}, timeout=5)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await sess.__aexit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await cdp_net.close_tab(port, tid)
                except Exception:  # noqa: BLE001
                    pass
                sess = None
            print(json.dumps(r, ensure_ascii=False))
            if not r.get("ok"):
                bad += 1
    finally:
        if a.debug and sess is not None:
            try:
                shot = await sess.send("Page.captureScreenshot", {"format": "png"}, timeout=30)
                Path("D:/AI-workplace/TEMU/_logs/hx_last.png").write_bytes(
                    base64.b64decode(shot.get("data") or ""))
            except Exception:  # noqa: BLE001
                pass
        try:
            if sess is not None:
                await sess.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        await cdp_net.close_tab(port, tid)
    print("完成 %d 个，失败 %d" % (len(spus), bad))
    return 0 if bad == 0 else 1


def _probe_port(window: str) -> int:
    from ziniao_port import probe_port
    return probe_port(window)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--spu", default="")
    ap.add_argument("--spu-file", default="")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--force", action="store_true", help="已有恒信仓也照样改量并提交")
    ap.add_argument("--drop", default="", help="要删掉的发货仓（逗号分隔，如 kansas,新泽西）")
    ap.add_argument("--ensure-ca", action="store_true",
                    help="删完后没有加州仓就补一个（加州三仓/加州3仓）")
    ap.add_argument("--combo", default="ask", choices=["ask", "bind", "probe"],
                    help="组合品弹窗：ask=跳过 / bind=自动绑定并切换合作仓 / probe=只记录")
    ap.add_argument("--divisor", type=int, default=0,
                    help="恒信仓填 领星可用//divisor；0/1 = 全填 0")
    ap.add_argument("--goods", default=str(_ROOT / "temu-admin" / "public" / "data" / "goods.json"))
    ap.add_argument("--code-map", default="", help="SPU→货号 JSON（goods.json 里没有的店用）")
    ap.add_argument("--no-auth-guard", action="store_true", help="跳过登录态兜底授权")
    ap.add_argument("--zero", default="",
                    help="一并清零的发货仓（逗号分隔，如 加州3仓）；恒信加完库存后把这些仓填 0")
    ap.add_argument("--stock-json",
                    default=str(_ROOT / "temu-admin" / "public" / "data" / "linxing-stock.json"))
    a = ap.parse_args()
    # 登录态兜底（2026-11-19 加）：商品列表/设置库存都在 agentseller.temu.com ⇒ cc 域。
    # 必须放在 asyncio.run **之前**：探针内部自己 asyncio.run，在事件循环里调用会直接失败。
    if not a.no_auth_guard:
        try:
            import auth_guard  # noqa: PLC0415
            auth_guard.guard_store(a.store, domain="cc", log=print)
        except Exception as e:  # noqa: BLE001
            print("[auth] 登录态兜底跳过：%s" % str(e)[:120])
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())













