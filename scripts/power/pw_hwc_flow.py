# -*- coding: utf-8 -*-
"""pw_hwc_flow.py — 帕沃尔「店铺 → 全部订单」页(hwc_shop)主流程驱动。

用户口径（2026-11-06，重要纠正）：
    操作**只在这一个页面**：
    https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1&uselastfilter=1
    · 搜索框 textarea[name=deliver_no]，支持多行（一行一个参考单号）
    · 「下单代发」= input[value=下单代发] onclick=edit_order('xiadan_df')
    · 批量改仓渠道 = 底部 select[name=select_types]=pl_edit_ck_chqd
      + select_ck(22=加州3仓 / 10=kansas仓) + select_zdkd(803=temu) + select_chqd(现取)

真机坑（实测）：
  1. 「查询」不能连点 → 页面 is_dianji 锁 + 原生 alert 会阻塞 JS。先 window.alert/confirm 打桩。
  2. 输入框写值要 focus()+select() 再 insertText，写完回读（本脚本会硬校验行数）。
  3. 勾选要 checked 属性 + property 双写（xiadan_df 读 attr('checked')）。
  4. 页面保留大量注释掉的旧列，解析前必须剥注释，否则「状态列」错位。
  5. xiadan_df 的返回 msg 不可信 → 真实结果只认「状态」列 + 运单号。

子命令：
    query    按单号清单精确查询 → 落 hwc_<tag>.json
    setwh    勾选当前列表 → 设仓库渠道 → 提交 → 回读
    xiadan   勾选当前列表 → 点「下单代发」→ 等全部完成 → 回读状态
    check    只看当前列表状态（不点任何动作）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, HERE)
from cdp_transport import CdpPage, http_targets  # noqa: E402

import pw_hwc_parse as P  # noqa: E402

PORT = 9333
URL = "https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1&uselastfilter=1"
OUT = os.path.join(HERE, "out", "2026-11-06")
PEEK = os.path.join(HERE, "out", "_peek")
CK = {"加州3仓": "22", "kansas仓": "10"}
ZDKD_TEMU = "803"
#: 「请选择操作」下拉 → 批量改仓渠道。★ 2026-11-07 用户贴图：
#:   下拉里是「**修改仓库渠道**」（不是「批量修改渠道」）；提交前出现
#:   仓库 / 指定快递 / 出货渠道 三个下拉：仓库真值 `加州3仓` / `kansas仓`。
#: ★ 2026-11-11 用户贴图（SHEIN 店口径）：
#:   「这一 步针对 shein 店铺在帕沃尔发货要改成 **Shein** 和 **Shein官方面单**」。
#:   ⇒ 指定快递 / 出货渠道**按平台取**（下拉真值读页面，不写死）：
#:     TEMU 店 → 指定快递 `temu`、出货渠道 `temu官方面单`
#:     SHEIN 店 → 指定快递 `Shein`、出货渠道 `Shein官方面单`
#:   ⚠ 出货渠道还有 `CBS-SHEIN自提`，绝不能靠「含 SHEIN」去命中它。
SET_WH_ACTION = "pl_edit_ck_chqd"

#: 平台 → (指定快递下拉文字, 出货渠道下拉文字)
PLATFORM_ROUTE = {
    "temu": ("temu", "temu官方面单"),
    "shein": ("Shein", "Shein官方面单"),
}


def platform_of_store(store: str) -> str:
    """店铺 → 平台（`temu` / `shein`）。SHEIN 店＝BS / BD / BAN / BEGL。"""
    s = str(store or "").strip().upper()
    if s in ("BS", "BD", "BAN", "BANGL", "BEGL", "BSGL"):
        return "shein"
    return "temu"
#: 店铺 ID（帕沃尔里的「店铺ID」）。★ 2026-11-07 真机：这里曾写死 BHY 的
#: `634418228526171`，于是 **BEL 的 P2 单在 BHY 店铺下查永远是 0 行**
#: （查询「成功」但列表 0 条，看着像页面坏了）。改为按店传：BEL=…77419 / BHY=…26171。
SHOP_IDS = {"BEL": "634418227477419", "BHY": "634418228526171"}
SHOP_ID = SHOP_IDS["BEL"]


# ---------------------------------------------------------------- 页面基础
def page() -> CdpPage:
    """取帕沃尔页面（**有头**，2026-11-09 用户令：帕沃尔仓的发货改有头）。

    由 `lib/pw_browser.ensure()` 起实例（独立 profile）并自动登录；
    模式默认有头，`PW_HEADED=0` 才无头。
    """
    import pw_browser  # noqa: PLC0415

    pg, _ = pw_browser.ensure(PORT)
    if not pg:
        raise SystemExit("帕沃尔浏览器起不来（端口 %d）" % PORT)
    if not pw_browser.ensure_logged_in(pg):
        raise SystemExit("帕沃尔实例登录失效，且自动登录失败")
    return pg


def arm(pg: CdpPage) -> None:
    """打桩弹窗 + 关掉 layer 遮罩。

    真机坑：`xiadan_df` 跑完会 `layer.alert(waybill_str)` 弹一个遮罩层，
    它会盖住整页（`document.elementFromPoint(按钮中心)` 返回 DIV 而不是按钮），
    之后点「查询」全部落空 —— 必须关掉才能继续（实测踩过）。

    ★ 2026-11-11 09:3x 修（现实证，关键）：原来这里是
      `document.querySelectorAll('.layui-layer').forEach(e => e.remove())`
      —— 帕沃尔的 layer 弹层**就是这个 class**（`layer.confirm` 询问是否确认下单、
      `layer.alert` 回显结果都在里面）。无条件 remove 等于把「确认」按钮一起删掉，
      于是点「下单代发」永远没反应、状态永远停在「未下单」（00:00 那轮"点了没回执"
      就是这么来的）。现在只关 **iframe 型**弹层（那是登录/编辑页，不是确认框），
      页面级 layer 交给 `click_xiadan` 自己点「确认」。
    """
    pg.evaluate("() => {"
                " window.alert = () => {}; window.confirm = () => true;"
                " try { if (typeof is_dianji !== 'undefined') is_dianji = false; } catch(e) {}"
                " document.querySelectorAll('.layui-layer-iframe').forEach(e => e.remove());"
                " return 1; }")


def ensure_page(pg: CdpPage) -> None:
    """确保停在「店铺 → 全部订单」列表页。

    ★ 2026-11-11：还要识别 `pb=` —— 有人（或上一轮点击）把页签点到
      「待确认订单 pb=1」后，同样一次整批查询只会查出待确认那几行，
      表现成「查不到本批单」。除 pb=1 外都视作要重新打开列表页。
    """
    url = pg.url or ""
    if ("hwc_shop.php" not in url or "shop_order" not in url
            or ("pb=" in url and "pb=1" in url)):
        pg.goto(URL)
        time.sleep(6)
    arm(pg)


#: 每页条数。★ 2026-11-11 真机：列表默认每页 50 行，整批 62 单查询只回 50 行，
#:   剩下 12 单被**分页截断**，看起来像「帕沃尔查不到」。查询前必须先调到 300
#:   （页面控件是 `input#pageSize`，回车走 `listTable.changePageSize(event)`）。
PAGE_SIZE = 300

#: 整批查询最多试几次（2026-11-12：页面被别的店冲掉时不再一次就整批放弃）
QUERY_ATTEMPTS = 3

#: 下单代发后「状态还是未下单」的延迟复核秒数（回执成功但列表更新慢）
LATE_RECHECK_S = 120


def set_page_size(pg: CdpPage, size: int = PAGE_SIZE) -> int:
    """把列表「每页 N 条」调到 300，返回实际生效值（调不到不抛，只提示）。

    不调这一步的后果：一次查 60+ 单只回来前 50 行，缺的那些会被误记成
    「帕沃尔查不到该单」，进而漏下单。
    """
    try:
        cur = pg.evaluate(r"""(n) => {
          const el = document.getElementById('pageSize');
          if (!el) return -1;
          if (String(el.value) !== String(n)) {
            const setv = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setv.call(el, String(n));
            try { listTable.changePageSize({keyCode: 13, which: 13}); } catch(e) {}
          }
          return String(document.getElementById('pageSize').value);
        }""", int(size))
        return int(cur) if str(cur).lstrip("-").isdigit() else -1
    except Exception:  # noqa: BLE001
        return -1


JS_STATE = r"""() => {
  const out = [];
  document.querySelectorAll('tr[class^=tr_html_]').forEach(t => {
    const id = (t.className.match(/tr_html_(\d+)/) || [])[1];
    if (!id) return;
    const tds = [...t.children].filter(e => e.tagName === 'TD');
    const wbCell = t.querySelector('[class^=waybill_]');
    const idx = wbCell ? tds.indexOf(wbCell) : -1;
    const info = (tds[4] ? tds[4].innerText : '').replace(/\s+/g, ' ');
    // 非贪婪 + 用下一个标签做边界：仓库/渠道为空时贪婪版会把「备注」吃进渠道（实测踩过）
    const m = info.match(/发货仓库\s*:\s*(.*?)\s*出货渠道\s*:\s*(.*?)\s*备注/) || [];
    const skus = [...t.querySelectorAll('p')].map(p => (p.innerText || '').trim())
      .filter(x => x.indexOf('平台SKU') === 0)
      .map(x => (x.split(':')[1] || '').trim());
    out.push({
      id,
      po: ((t.querySelector('[class^=deliver_no_]') || {}).innerText || '').trim(),
      status: idx > 0 ? (tds[idx - 1].innerText || '').trim() : '',
      err: wbCell ? (wbCell.innerText || '').trim() : '',
      wh: m[1] || '', qd: m[2] || '',
      sku: skus.join(' / '),
      cb: (t.querySelector('input[name="checkboxes[]"]') || {}).checked,
    });
  });
  return JSON.stringify(out);
}"""


def read_state(pg: CdpPage) -> list:
    return json.loads(pg.evaluate(JS_STATE) or "[]")


WB_RE = P.WB_RE


def wb_of(s: str) -> str:
    m = WB_RE.search(s or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------- 搜索
def do_query(pg: CdpPage, pos: list, shop_id: str = SHOP_ID, wait: float = 25.0) -> list:
    """按参考单号查询列表状态 —— **一单一查**（2026-11-11 修复）。

    ★ 为什么不再一次贴多行：2026-11-11 04:00 的日更任务里 BEL/BHY 的
      `hwc_order`（帕沃尔下单代发）都死在 `[FATAL] 查询没刷新出结果`。
      现场取证（真页、只查询不动单）：
        · 单号 1 行 → 查询正常返回（listCallback record_count=1）；
        · 同样的单号写 2/3/5/62 行（换行或逗号分隔）→ record_count 恒为 0。
      即帕沃尔「参考单号」搜索已退化成**只认一个参考单号**，
      多行/逗号都被当成一整串精确匹配 ⇒ 永远 0 行。这不是偶发慢加载，
      重试也没用（11-10 那次同样是 hwc_order 挂在同一处）。
      故这里改成：多单时逐单查询再合并，缺单只告警不整步崩。
    """
    # ★★ 2026-11-11 09:2x 用户纠（推翻上面「逐单」的临时做法）：
    #   「在下单代发那一步，复制所有帕沃尔仓的订单去搜索框输入搜索，而不是一个一个搜」
    #   ⇒ 恢复成**一次把整批参考单号贴进搜索框**（换行分隔、单次查询），不再逐单。
    #   写法也改了：不再用 CDP Input.insertText（有头窗口抢焦点时文本会丢，
    #   现场就是这样把单号弄丢、再被 0 行误判成「站点只认单号」），
    #   改为原生 setter 写 value + 回读校验；回读不符会整齐地重写一次。
    # ★ 第二处（同日取证）：**店铺ID 过滤也不可靠**。BEL 导出的
    #   `PO-211-19207776891514009` 在帕沃尔那一行写的是「店铺ID:634418228526171
    #   店铺名称:BHY」—— 帕沃尔侧店铺归属与 TEMU 店名对不上，按 shop_id 筛就是 0 行。
    #   参考单号全局唯一，故逐单查询时带店筛不到就退化全店查（结果仍被
    #   `pos_now ⊆ want` 卡住，不会串到别人的单）。
    pos = [str(x).strip() for x in (pos or []) if str(x).strip()]
    ensure_page(pg)
    # 整批一次查：先带店筛（shop_id 默认空＝全部店铺），快慢由 sites 决定
    out = _do_query_batch(pg, pos, shop_id, wait)
    want = set(pos)
    missing = [p for p in pos if p not in {r["po"] for r in out}]
    if missing and shop_id:
        # 店铺归属不可信：带店筛漏了单 → 再整批全店查一次
        print("[WARN] 店铺 %s 下漏了 %d 单 → 整批全店重查" % (shop_id, len(missing)))
        try:
            r2 = _do_query_batch(pg, missing, "", wait)
            seen = {r["po"] for r in out}
            out = out + [r for r in r2 if r["po"] not in seen]
        except SystemExit:
            pass
        missing = [p for p in pos if p not in {r["po"] for r in out}]
    if not out:
        raise SystemExit("[FATAL] 查询没刷新出结果（%d 单逐单查询全部 0 行；"
                         "shop_id=%s，确认帕沃尔「更新订单」是否已同步该店）"
                         % (len(pos), shop_id or "(全部店铺)"))
    if missing:
        print("[WARN] %d / %d 单在帕沃尔查不到（已逐单查 + 退化全店查）：%s"
              % (len(missing), len(pos), ", ".join(missing[:8])))
    return out


def _query_one_po(pg: CdpPage, po: str, shop_id: str, wait: float) -> list:
    """查一个参考单号；带店铺筛查不到就退化全店查。返回 [] = 帕沃尔确实没这单。

    ★ 2026-11-11 起 `do_query` / `order_pos` 都改走整批一次查询，本函数只留给
      需要单点复核的场合（`order_pos` 回读某一单时仍用它）。
    """
    try:
        return _do_query_batch(pg, [po], shop_id, wait, fast=True)
    except SystemExit:
        if not shop_id:
            return []
    print("[WARN] %s 在店铺 %s 下查不到 → 退化全店查询" % (po, shop_id))
    try:
        return _do_query_batch(pg, [po], "", wait, fast=True)
    except SystemExit:
        return []


class QueryStale(Exception):
    """本次查询没拿到本批单号（页面被别的店刷掉 / 筛选没生效）→ 可重试。"""


def _do_query_batch(pg: CdpPage, pos: list, shop_id: str = SHOP_ID, wait: float = 25.0,
                    fast: bool = False, attempts: int = 0) -> list:
    """**整批查询（带重试）**：查询没拿到本批就重新粘贴单号再查，重试用尽才 FATAL。

    ★ 2026-11-12 真机（当天帕沃尔 85 单全灭）：帕沃尔是单实例单页（`PORT=9333`），
      多个店并行时页面会被别的店冲掉——我们这侧 textarea 回读是对的，
      但点「查询」落下去时表单已被重置，`listCallback` 回来的是**整店默认列表**
      （record_count=61），判据 `pos_now ⊆ 本批` 不成立 ⇒ 旧版一次不中就 `[FATAL]` 整批不下单。
      现场取证就是 `{"rows":63,"cb":1,"cnt":"61"}` 这个签名在 BHY/BEL/BAN 同时出现。
      ⇒ 现在允许重试（默认 `QUERY_ATTEMPTS` 次，每次重贴单号 + 重新点查询）。
      根治仍是串行（`run_pw_fulfill._pw_lock` 的 `PW` 锁），重试只是兜底。
    """
    n = int(attempts or os.environ.get("PW_QUERY_ATTEMPTS") or QUERY_ATTEMPTS)
    n = max(1, n)
    last = ""
    for i in range(n):
        try:
            return _do_query_batch_once(pg, pos, shop_id, wait, fast)
        except QueryStale as ex:
            last = str(ex)
            print("[WARN] 第 %d/%d 次查询没拿到本批（%s）→ 重贴单号再查"
                  % (i + 1, n, last.replace("\n", " ")[:160]), flush=True)
            if i + 1 < n:
                time.sleep(2.0)
                arm(pg)
    raise SystemExit(last or "[FATAL] 查询没刷新出结果")


def _do_query_batch_once(pg: CdpPage, pos: list, shop_id: str = SHOP_ID, wait: float = 25.0,
                         fast: bool = False) -> list:
    """真实页面操作：清筛选 → textarea 写入**整批**参考单号（换行分隔）→ 点「查询」→ 等列表刷新。

    ★ 2026-11-11（用户口径）：下单代发要「复制所有帕沃尔仓的订单到搜索框一次搜」，
      不逐单。写值方式定死为「原生 setter 写 value + 回读校验 + 不符重写」：
      有头窗口被抢焦点时 `Input.insertText` 会丢字（现实证过，text 变空 ⇒ 查询 0 行
      ⇒ 被误判成「站点只认单号」），而 setter 不依赖焦点。
    `fast=True`（单点复核时用）：把「等物理点击落地」的 4s 压到 1.2s。
    """
    ensure_page(pg)
    set_page_size(pg)
    pg.evaluate(r"""(sid) => {
      const ta = document.querySelector('textarea[name=deliver_no]');
      const f = ta.closest('form');
      f.elements['deliver_no'].value = '';
      f.elements['shop_id'].value = sid;
      f.elements['status'].value = '';
      f.elements['ck_id'].value = '';
      f.elements['country'].value = '';
      f.elements['shop_types'].value = '';
      f.elements['start_date'].value = '2026-09-01';
      f.elements['end_date'].value = '2026-12-31';
      ta.focus(); ta.select();
      return 1;
    }""", shop_id)
    text = "\n".join(pos)
    got = ""
    for _try in range(3):
        got = pg.evaluate(r"""(t) => {
          const ta = document.querySelector('textarea[name=deliver_no]');
          const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
          set.call(ta, '');
          set.call(ta, t);
          ta.dispatchEvent(new Event('input', {bubbles: true}));
          ta.dispatchEvent(new Event('change', {bubbles: true}));
          return ta.value;
        }""", text) or ""
        n_got = len([x for x in got.splitlines() if x.strip()])
        if n_got == len(pos):
            break
        time.sleep(0.5)
    if n_got != len(pos):
        raise QueryStale("[FATAL] textarea 回读不符：期望 %d 行，页面 %d 行" % (len(pos), n_got))

    # 清掉「查询」的连点锁（is_dianji 在上一批查询里可能还挂着）
    pg.evaluate("() => { try { if (typeof is_dianji !== 'undefined') is_dianji = false; }"
                " catch(e) {} return 1; }")
    # ★ 用 hook 判「这次查询真的回来了」，不靠 DOM 指纹：
    #   列表可能和上一次完全一样（同筛选再查一次），指纹不变会被误判成"没刷新"（实测踩过）。
    pg.evaluate(r"""() => {
      window.__qdone = 0; window.__qres = null;
      if (typeof listTable !== 'undefined' && !listTable.__hooked) {
        const orig = listTable.listCallback;
        listTable.listCallback = function(res, txt) {
          window.__qdone = 1;
          window.__qres = (res && res.filter) ? res.filter.record_count : null;
          return orig.call(listTable, res, txt);
        };
        listTable.__hooked = 1;
      }
      return 1;
    }""")
    b = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('button,input[type=button]')]
        .find(e => /^\s*查询\s*$/.test((e.innerText || e.value || '').trim()));
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                             inView: r.top >= 0 && r.bottom <= innerHeight});
    }""") or "{}")
    if not b.get("x"):
        raise QueryStale("[FATAL] 找不到「查询」按钮")
    # 弹层会吃掉点击（elementFromPoint 变成遮罩），先清
    arm(pg)
    # ★ 2026-11-11：查询前把旧行清掉。不清的话回读期间会读到**上一批残留的行**
    #   （实测「本批命中 50 行 + 混了非本批单号」），既误判又拖慢。
    pg.evaluate("() => { document.querySelectorAll('tr[class^=tr_html_]').forEach(e => e.remove());"
                " return 1; }")
    pg.dispatch_mouse(b["x"], b["y"])

    # 物理点击偶尔会落空（焦点/滚动竞态）→ 4 秒内没回调就退化成调同一个函数。
    # 「查询」按钮的 form action 就是 `javascript:searchOrder(1)`，所以两者等价。
    time.sleep(1.2 if fast else 4)
    if not pg.evaluate("() => window.__qdone"):
        pg.evaluate("() => { try { searchOrder(1); return 1; } catch(e) { return 0; } }")

    want = set(pos)
    got_rows = []
    partial = []
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(0.5)
        pg.evaluate("() => { try { if (typeof is_dianji !== 'undefined') is_dianji = false; }"
                    " catch(e) {} return 1; }")
        if not pg.evaluate("() => window.__qdone"):
            continue
        time.sleep(0.5)                     # 让 innerHTML 换完再读
        rows = read_state(pg)
        pos_now = {x["po"] for x in rows}
        # 整批查：分页可能只返回前 N 行 ⇒ 只要求「返回的全都属于本批」，
        # 不要求一页装下全部（原来那版要求 ⊆ 且非空，多单时永远判不通过）。
        # ★ 2026-11-11 09:4x 修：本批里**帕沃尔本来就没有**的单（未同步 / 单号不在帕沃尔）
        #   不参与这条判定，否则整批必然判"不是本批单号"（24 单里有 2 单查不到就是这么炸的）。
        if rows and pos_now <= want:
            got_rows = rows
            break
        if rows and pos_now & want:
            partial = rows
    arm(pg)
    if not got_rows:
        if partial:
            # 有交集但不是全属于本批：带 WARN 放行，由调用方按 po 逐个核状态
            print("[WARN] 列表里混了非本批单号，按行逐个核对：本批命中 %d 行"
                  % len([x for x in partial if x["po"] in want]))
            got_rows = partial
        else:
            q = pg.evaluate("() => JSON.stringify({rows: document.querySelectorAll('tr[class^=tr_html_]').length, "
                            "cb: window.__qdone, cnt: window.__qres})") or "{}"
            raise QueryStale("[FATAL] 查询没刷新出结果（listCallback 未回调或列表不是本批单号）"
                             "｜页面实况 %s" % q)
    if len(got_rows) < len(want):
        print("[WARN] 查到 %d 行 / 期望 %d 单（其余可能分页或帕沃尔未同步）"
              % (len(got_rows), len(want)))
    return got_rows


# ---------------------------------------------------------------- 勾选 / 下拉
def set_checks(pg: CdpPage, ids: list) -> int:
    """勾选指定 id。

    用户口径（2026-11-06）：「你要全选序号，再去提交」—— 走页面自己的全选框
    `listTable.selectAll(this,"checkboxes")`，它只写 `checked` property；
    而 `edit_order('xiadan_df')` 读的是 `attr('checked')` ⇒ 必须再补一层
    setAttribute('checked','checked')，否则下单代发会认为"没选中"。
    """
    # ① 全选：点表头那个 checkbox（等同人工点「序号」全选）
    pg.evaluate(r"""() => {
      const hdr = document.querySelector('th input[type=checkbox]');
      hdr.scrollIntoView({block: 'center'});
      if (!hdr.checked) { hdr.click(); }
      if (typeof listTable !== 'undefined') { listTable.selectAll(hdr, 'checkboxes'); }
      return 1;
    }""")
    time.sleep(0.3)
    # ② 收窄到目标 id + 双写 attr（xiadan_df 依赖 attribute）
    js = r"""(ids) => {
  const want = new Set(ids.map(String));
  let n = 0;
  document.querySelectorAll('input.checkbox').forEach(cb => {
    const hit = want.has(String(cb.value));
    cb.checked = hit;
    if (hit) { cb.setAttribute('checked', 'checked'); n++; }
    else { cb.removeAttribute('checked'); }
  });
  return n;
}"""
    return int(pg.evaluate(js, [str(i) for i in ids]) or 0)


def goto_batch_zone(pg: CdpPage) -> None:
    pg.evaluate("""() => {
      const s = document.querySelector('[name=select_types]');
      s.scrollIntoView({block: 'center'});
      return 1;
    }""")


def set_selects(pg: CdpPage, wh: str, platform: str = "temu") -> dict:
    """底部批量栏：修改仓库渠道 → 仓库 → 指定快递 → 出货渠道（**按平台**）。

    ★ 2026-11-11 用户贴图：SHEIN 店在帕沃尔发货要选 **Shein** + **Shein官方面单**；
      TEMU 店仍是 `temu` + `temu官方面单`。两者都**按下拉文字取 value**，
      不写死 id（帕沃尔会改枚举，写死过一次就整批 no-option）。
    """
    ck = CK[wh]
    zdkd_text, chqd_text = PLATFORM_ROUTE.get(platform, PLATFORM_ROUTE["temu"])
    r = json.loads(pg.evaluate(r"""(a) => {
      const s = document.querySelector('[name=select_types]');
      const c = document.querySelector('[name=select_ck]');
      const z = document.querySelector('[name=select_zdkd]');
      const q = document.querySelector('[name=select_chqd]');
      if (!s || !c || !z || !q) return JSON.stringify({err: 'missing select'});
      // ★ 用户贴图：下拉文字是「修改仓库渠道」；按文字兜底找 value，防止站点改枚举
      const hit = [...s.options].find(o => (o.text || '').replace(/\s+/g, '') === '修改仓库渠道');
      s.value = hit ? hit.value : a.act;
      // ★ 指定快递按文字取（temu / Shein 都取自下拉真值，不写死 803）
      const zt = (a.zdkd_text || '').replace(/\s+/g, '').toLowerCase();
      const zo = [...z.options].find(o => (o.text || '').replace(/\s+/g, '').toLowerCase() === zt);
      if (zo) z.value = zo.value;
      c.value = a.ck;
      c.style.display = ''; z.style.display = ''; q.style.display = '';
      // 选完快递要触发一次切换，出货渠道才会按快递重新拉
      z.dispatchEvent(new Event('change', {bubbles: true}));
      return JSON.stringify({st: s.value, st_text: (s.options[s.selectedIndex] || {}).text,
                             ck: c.value, zdkd: z.value,
                             zdkd_text: (z.options[z.selectedIndex] || {}).text});
    }""", {"act": SET_WH_ACTION, "ck": ck, "zdkd": ZDKD_TEMU,
            "zdkd_text": zdkd_text}) or "{}")
    r["chqd"] = ""
    r["platform"] = platform
    # ★ 出货渠道：**先精确匹配平台那个**（`Shein官方面单` / `temu官方面单`），
    #   命中不了再退化成「含 官方面单 且不含 自提」的候选。
    want_txt = chqd_text.replace(" ", "").lower()
    for _ in range(24):
        pg.evaluate("() => { try { get_paisongx(); } catch(e) { return 0; } return 1; }")
        time.sleep(0.7)
        opts = json.loads(pg.evaluate(
            "() => JSON.stringify([...document.querySelector('[name=select_chqd]').options]"
            ".map(o => ({v: o.value, t: o.text.trim()})))") or "[]")
        hit = [o for o in opts if o["v"] and o["t"].replace(" ", "").lower() == want_txt]
        if not hit:
            hit = [o for o in opts
                   if o["v"] and "官方面单" in o["t"] and "自提" not in o["t"]]
        if hit:
            r["chqd"] = hit[0]["v"]
            r["chqd_name"] = hit[0]["t"]
            break
    # ★ 必须把值写回下拉框本身：get_paisongx() 只负责填充 options，
    #   不写 value 时表单里 select_chqd=""，服务端会回「请选择出货渠道」（实测踩过）。
    #
    # ★★ 2026-11-11 真机（BD 24 单全灭）根因就在这一行之后的**竞态**：
    #   `get_paisongx()` 是 `$.post` 异步，回调里 `$('[name=select_chqd]').html(str_html)`
    #   会**整体重建 options**（新 option 没有 selected）⇒ 我们刚写进去的 value 被清成 ""。
    #   于是提交时 select_chqd="" ⇒ 服务端只回一句 layer.alert（被我们桩掉）⇒ 页面毫无变化。
    #   现在：写值后**回读校验**，不对就重写，最多 3 次；仍不对就把 `chqd` 清空，
    #   让调用方走 `http_set_wh()` 兜底（不带着空渠道去提交）。
    if r.get("chqd"):
        for _ in range(3):
            r["set"] = pg.evaluate(
                "(v) => { const q = document.querySelector('[name=select_chqd]');"
                " q.value = v; q.style.display = '';"
                " q.dispatchEvent(new Event('change', {bubbles: true}));"
                " return q.value; }", r["chqd"])
            if str(r.get("set") or "") == str(r["chqd"]):
                break
            time.sleep(0.8)
        if str(r.get("set") or "") != str(r["chqd"]):
            r["why"] = "出货渠道值被页面重建清空（%r→%r）" % (r["chqd"], r.get("set"))
            r["chqd"] = ""
    return r


def http_set_wh(ids: list, wh: str, platform: str = "temu", log=print) -> dict:
    """HTTP 直提「修改仓库渠道」——等价页面点「提交」（`hwc_shop.php?act=operate`）。

    ★ 2026-11-11 真机（BD 24 单）：页面「提交」点了但回读全是 `wh=- qd=-`，
      同一组参数（select_types=pl_edit_ck_chqd / select_ck / select_zdkd=804 /
      select_chqd=553）走 HTTP 一次就成功。⇒ 页面点击不落地时的**兜底**，两处都调它。
    提交前先用 `get_shop_paisongx` 取该 (快递, 仓) 下的渠道 id（不写死 553/530）。
    """
    import pw_session as SS  # noqa: PLC0415

    zdkd = {"temu": "803", "shein": "804"}.get(platform, "803")
    ck = CK[wh]
    pw = SS.PW()
    pw.login()
    ids_s = "".join("%si" % str(i) for i in ids)
    r = pw.post("deliver.php", {"act": "get_shop_paisongx", "ids": ids_s, "zdkd": zdkd,
                                "select_ck": ck, "select_types": "pl_edit_ck_chqd"},
                referer="https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1")
    try:
        d = json.loads(r) if isinstance(r, str) else r
    except Exception:  # noqa: BLE001
        d = {}
    want = PLATFORM_ROUTE.get(platform, PLATFORM_ROUTE["temu"])[1].replace(" ", "").lower()
    cands = [o for o in (d.get("html") or [])
             if str(o.get("pname") or "").replace(" ", "").lower() == want]
    if not cands:
        cands = [o for o in (d.get("html") or [])
                 if "官方面单" in str(o.get("pname") or "")]
    if not cands:
        return {"ok": 0, "why": "取不到出货渠道：%s" % json.dumps(d, ensure_ascii=False)[:200]}
    chqd = str(cands[0]["id"])
    data = [("act", "batch"), ("pb", ""), ("pb_str", ""), ("ids", ""),
            ("select_types", "pl_edit_ck_chqd"), ("select_ck", ck),
            ("select_zdkd", zdkd), ("select_chqd", chqd)]
    data += [("checkboxes[]", str(i)) for i in ids]
    resp = pw.post("hwc_shop.php?act=operate", data,
                   referer="https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1")
    txt = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)
    log("   [HTTP] 设仓渠道 %s/%s（%d 单，渠道 id=%s）→ 回执 %s"
        % (wh, cands[0].get("pname"), len(ids), chqd,
           re.sub(r"\s+", " ", txt)[:120]))
    return {"ok": len(ids), "chqd": chqd, "chqd_name": cands[0].get("pname"),
            "zdkd": zdkd, "wh": wh}


def click_submit(pg: CdpPage) -> None:
    b = json.loads(pg.evaluate(r"""() => {
      const f = document.forms['listForm'];
      const el = [...f.querySelectorAll('input[type=submit],button')]
        .find(e => /^\s*提交\s*$/.test((e.value || e.innerText || '').trim()));
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }""") or "{}")
    if not b.get("x"):
        raise SystemExit("[FATAL] 找不到「提交」按钮")
    pg.dispatch_mouse(b["x"], b["y"])


# ---------------------------------------------------------------- 下单代发
def click_xiadan(pg: CdpPage, warehouse: str = "") -> dict:
    """点「下单代发」按钮（edit_order('xiadan_df')），并完成随后的确认框（选仓 → 确认）。

    ★ 2026-11-11 09:3x 第三次修（用户真机口径）：
      「更新完订单 → 批量搜索 → 选仓 → 下单代发」。真页链路是
      `edit_order('xiadan_df')` → 可能弹 **layer.confirm 询问框**（选发货仓库）
      → 点「确认」才真正提交。所以这里：调用后**等确认框出现、把发货仓库选好、
      点确认**；确认框里的仓库下拉读不到时如实报出来，不再假装成功。
      物理点击只作兜底。
    """
    if not pg.evaluate("() => { try { edit_order('xiadan_df'); return 1; }"
                       " catch(e) { return 0; } }"):
        b = json.loads(pg.evaluate(r"""() => {
          const el = [...document.querySelectorAll('input,a,button')]
            .find(e => /xiadan_df/.test(e.getAttribute('onclick') || ''));
          if (!el) return '{}';
          el.scrollIntoView({block: 'center'});
          const r = el.getBoundingClientRect();
          return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
        }""") or "{}")
        if not b.get("x"):
            raise SystemExit("[FATAL] 找不到「下单代发」按钮（edit_order 也不可用）")
        pg.dispatch_mouse(b["x"], b["y"])
    conf = confirm_xiadan_dialog(pg, warehouse=warehouse)
    return {"confirm": conf}


def xiadan_layer_present(pg: CdpPage) -> bool:
    return bool(pg.evaluate(
        "() => !!document.querySelector('.layui-layer:not(.layui-layer-iframe)')"))


def confirm_xiadan_dialog(pg: CdpPage, warehouse: str = "", timeout: float = 15.0) -> dict:
    """处理「下单代发」的 layer.confirm：选发货仓库 → 点「确认」。

    返回 {"seen":bool,"warehouse":"","picked":"","buttons":[..],"note":""}；
    没出现弹层 → seen=False（交给后续回读状态去判定）。
    """
    t0 = time.time()
    info = {}
    while time.time() - t0 < timeout:
        if xiadan_layer_present(pg):
            break
        time.sleep(0.5)
    else:
        return {"seen": False}
    time.sleep(0.8)
    info = json.loads(pg.evaluate(r"""() => {
      const ly = [...document.querySelectorAll('.layui-layer')]
        .filter(e => !e.classList.contains('layui-layer-iframe')).pop();
      if (!ly) return '{"seen":false}';
      const cs = [...ly.querySelectorAll('select')].map(s => ({
        name: s.name || s.id || '', cls: s.className,
        opts: [...s.options].map(o => [(o.value||''), (o.text||'').trim()])}));
      const btns = [...ly.querySelectorAll('a,button,input[type=button],.layui-layer-btn0,.layui-layer-btn1')]
        .map(b => (b.innerText || b.value || '').trim()).filter(Boolean);
      return JSON.stringify({seen: true, text: (ly.innerText||'').replace(/\s+/g,' ').slice(0,200),
                             selects: cs, buttons: btns});
    }""") or "{}")
    picked = ""
    # ① 选发货仓库（有下拉就选；用户口径「选仓下单代发」）
    if warehouse:
        picked = pg.evaluate(r"""(wh) => {
          const ly = [...document.querySelectorAll('.layui-layer')]
            .filter(e => !e.classList.contains('layui-layer-iframe')).pop();
          if (!ly) return '';
          for (const s of ly.querySelectorAll('select')) {
            for (const o of s.options) {
              if ((o.text||'').trim() === wh) {
                s.value = o.value;
                s.dispatchEvent(new Event('change', {bubbles: true}));
                return (o.text||'').trim() + '=' + o.value;
              }
            }
          }
          return '';
        }""", warehouse) or ""
        time.sleep(0.4)
    # ② 点「确认」（layer 的标准按钮是 .layui-layer-btn0）
    clicked = pg.evaluate(r"""() => {
      const ly = [...document.querySelectorAll('.layui-layer')]
        .filter(e => !e.classList.contains('layui-layer-iframe')).pop();
      if (!ly) return '';
      const b = ly.querySelector('.layui-layer-btn0')
             || [...ly.querySelectorAll('a,button,input[type=button]')]
                  .find(e => /确认|确定|是/.test((e.innerText||e.value||'')));
      if (!b) return '';
      b.click();
      return (b.innerText || b.value || '').trim();
    }""") or ""
    time.sleep(0.5)
    info["picked"] = picked
    info["clicked"] = clicked
    # 关掉残留弹层（只关 iframe 型，避免下一轮点不到按钮）
    arm(pg)
    return info


def wait_xiadan(pg: CdpPage, n_expect: int, timeout: float = 1800.0,
                log=print) -> dict:
    """等下完。

    判据（实测）：xiadan_df 只写「下单错误提醒」列（class=waybill_<id>），
    「状态」列**不会**自动变 —— 必须重新查询才刷新（这和坑 6 一致：
    接口 msg 不可信，但错误提醒列是逐单真实结果）。
    完成信号：waybill_ing 归 0 且已回条数 = 选中条数；兜底看 waybill_sm==waybill_count。
    """
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        time.sleep(3)
        st = json.loads(pg.evaluate(r"""() => {
          const g = n => { try { return eval(n); } catch(e) { return null; } };
          const rows = [...document.querySelectorAll('tr[class^=tr_html_]')];
          const filled = rows.filter(t => {
            const c = t.querySelector('[class^=waybill_]');
            return c && (c.innerText || '').trim();
          }).length;
          const ing = rows.filter(t => {
            const c = t.querySelector('[class^=waybill_]');
            return c && /获取中/.test(c.innerText || '');
          }).length;
          const load = document.querySelector('.load_start');
          return JSON.stringify({ing: g('waybill_ing'), sm: g('waybill_sm'),
                                 cnt: g('waybill_count'), rows: rows.length,
                                 filled, busy: ing,
                                 bar: load ? getComputedStyle(load).display : 'none'});
        }""") or "{}")
        if st != last:
            log("   [等] t=%.0fs ing=%s 已回=%s/%s 行=%s 有结果=%s 获取中=%s"
                % (time.time() - t0, st.get("ing"), st.get("sm"), st.get("cnt"),
                   st.get("rows"), st.get("filled"), st.get("busy")))
            last = st
        done = (st.get("ing") in (0, None)) and st.get("busy") == 0 and st.get("filled")
        if done:
            return st
    return {"timeout": True}


def fix_one_sku(pg: CdpPage, oid: str, new_sku: str) -> dict:
    """在订单编辑页把 ck_sku[] 覆盖成新 Fnsku，提交并回读。

    真机坑（用户反复强调）：sku 框必须 **JS focus() + select() 再 insertText**，
    单纯点击焦点不会落进去，页面还会假报"成功"但值没变 ⇒ 提交后必须回读。
    """
    pg.goto("https://www.powerwarehouse.net/hwc_shop.php?act=edit_order&id=%s" % oid)
    time.sleep(5)
    arm(pg)
    prep = json.loads(pg.evaluate(r"""() => {
      const e = document.querySelector('input[name="ck_sku[]"]');
      if (!e) return JSON.stringify({err: 'no sku input'});
      e.scrollIntoView({block: 'center'});
      e.focus(); e.select();
      return JSON.stringify({old: e.value, focused: document.activeElement === e,
                             rows: document.querySelectorAll('input[name="ck_sku[]"]').length});
    }""") or "{}")
    if prep.get("err"):
        return {"ok": False, "why": prep["err"]}
    cdp = pg.cdp_session()
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA",
                                        "windowsVirtualKeyCode": 65,
                                        "nativeVirtualKeyCode": 65, "modifiers": 2})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                        "windowsVirtualKeyCode": 65,
                                        "nativeVirtualKeyCode": 65, "modifiers": 2})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace",
                                        "code": "Backspace", "windowsVirtualKeyCode": 8,
                                        "nativeVirtualKeyCode": 8})
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace",
                                        "code": "Backspace", "windowsVirtualKeyCode": 8,
                                        "nativeVirtualKeyCode": 8})
    time.sleep(0.2)
    pg.evaluate("() => { const e = document.querySelector('input[name=\"ck_sku[]\"]');"
                " e.focus(); e.select(); return 1; }")
    cdp.send("Input.insertText", {"text": new_sku})
    time.sleep(0.6)
    filled = pg.evaluate("() => (document.querySelector('input[name=\"ck_sku[]\"]')||{}).value")
    if (filled or "").strip() != new_sku:
        return {"ok": False, "why": "写入失败：回读=%r 期望=%r" % (filled, new_sku),
                "old": prep.get("old")}
    open(os.path.join(PEEK, "fix_%s_filled.png" % oid), "wb").write(pg.screenshot())
    if not COMMIT:
        return {"ok": True, "dry": True, "old": prep.get("old"), "new": filled}

    # 提交（tijiao()）
    b = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('input[type=button],button,input[type=submit]')]
        .find(e => /^\s*提交\s*$/.test((e.value || e.innerText || '').trim()));
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                             onclick: el.getAttribute('onclick') || ''});
    }""") or "{}")
    if not b.get("x"):
        return {"ok": False, "why": "找不到提交按钮"}
    pg.dispatch_mouse(b["x"], b["y"])
    time.sleep(7)
    arm(pg)
    txt = pg.evaluate("() => (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,300)")
    open(os.path.join(PEEK, "fix_%s_after.png" % oid), "wb").write(pg.screenshot())
    # 回读：编辑页保留在原 id 时直接读 sku 框，否则回列表页查
    now = pg.evaluate("() => location.href")
    back = ""
    if "edit_order" in (now or ""):
        back = pg.evaluate("() => (document.querySelector('input[name=\"ck_sku[]\"]')||{}).value")
    return {"ok": (back or "").strip() == new_sku, "old": prep.get("old"),
            "new": back, "msg": txt[:120], "url": (now or "")[:80]}


COMMIT = False


def order_pos(pg: CdpPage, pos: list, shop_id: str = "", budget_s: float = 1800.0,
              log=print, wh_map: dict = None, platform: str = "temu") -> dict:
    """整批「下单代发」——正确链路（用户 2026-11-11 09:3x 贴图纠正，记死）：

        ① 更新订单（调用方负责，`pw_update_orders.py`）
        ② 全部订单页 → 搜索框**一次粘贴整批**参考单号 → 查询
        ③ 勾选（序号全选 / 收窄到本批）
        ④ **底部批量栏：修改仓库渠道 → 发货仓库(加州3仓|kansas仓) → 指定快递 → 出货渠道 → 提交**
           指定快递/出货渠道**按平台**：TEMU 店 `temu` + `temu官方面单`；
           SHEIN 店 `Shein` + `Shein官方面单`（用户 2026-11-11 贴图口径）。
        ⑤ 再点「下单代发」→ 确认 → **回读「状态」列 + 运单号**

    ★ 为什么 ④ 不能省：跳过 ④ 直接点下单代发，接口会回
      `{"error":1,"msg":"下单成功"}`（假的），而「状态」列一直是「未下单」、
      「下单错误提醒」列显示「下单成功」，全部订单 / 待确认 / deliver.php
      都查不到出库单 —— 这就是 11-11 00:00 那批"软失败"的真相。
      仓库渠道没设之前，帕沃尔根本不接受这单。
    ★ 判据不变：**只认「状态」列离开「未下单」+ 运单号**，接口 msg / 弹层都不算数。
    """
    pos = [str(x).strip() for x in (pos or []) if str(x).strip()]
    res = {"total": len(pos), "ok": [], "fail": [], "skip": [], "unfinished": []}
    t0 = time.time()
    left = list(dict.fromkeys(pos))
    for rnd in range(1, 3):
        if not left:
            break
        log("   [批次 %d] 整批查询 %d 单…" % (rnd, len(left)))
        try:
            rows = do_query(pg, left, shop_id=shop_id, wait=90)
        except SystemExit as ex:
            res["fail"] += [[p, str(ex)[:160]] for p in left]
            break
        want = set(left)
        # ★ 整批查询会带出同店别人的单（保底筛选失效时），这里先按本批单号收敛
        rows = [r for r in rows if r["po"] in want]
        todo = [r for r in rows if r["status"] == "未下单"]
        for r in rows:
            if r["status"] != "未下单":
                res["skip"].append([r["po"], "状态=%s" % (r["status"] or "?")])
        if not todo:
            # ★ 2026-11-13 修（BHY 55 单被误报「未完成 55」）：
            #   这里原来直接 break，**没把 Skip 的行从 left 里剔掉** ⇒ 走到函数末尾
            #   `if left:` 时整批又落进 `unfinished`，于是「全部已下单」被报成
            #   「0 单成功 / 未完成 55」，整链 soft_failed、失败 sheet 全是假失败。
            log("   本批无可下单行（都已有状态）")
            left = [p for p in left if p not in {s[0] for s in res["skip"]}]
            break
        ids = [r["id"] for r in todo]
        log("   待下单代发 %d 行 → 勾选" % len(ids))
        if not set_checks(pg, ids):
            res["fail"] += [[r["po"], "勾选 0 行（id=%s）" % r["id"]] for r in todo]
            break
        # ④ 先设仓库渠道（跳过这步 = 假下单）
        #    ★ 仓库**从拆分结果取**（`wh_map`：参考单号 → 加州3仓 / kansas仓），
        #      不从页面读 —— 未设渠道的行在页面上发货仓库本来就是空白，
        #      读页面等于把「本来就没设」当成「读不到」。
        wh_map = wh_map or {}
        for r in todo:
            r["wh"] = (wh_map.get(r["po"]) or r.get("wh") or "").strip()
        whs = sorted({r["wh"] for r in todo} - {""})
        wh = whs[0] if len(whs) == 1 else ""
        if len(whs) > 1:
            # 混仓：先下加州3仓那批，kansas 留下一轮
            wh = "加州3仓" if "加州3仓" in whs else whs[0]
            log("   ⚠ 本批混仓 %s，本轮只处理 %s" % ("/".join(whs), wh))
            keep = [r for r in todo if r["wh"] == wh]
            drop = [r for r in todo if r not in keep]
            if drop:
                log("   ⏭ 另 %d 单留到下一轮" % len(drop))
            todo, ids = keep, [r["id"] for r in keep]
            if not set_checks(pg, ids):
                res["fail"] += [[r["po"], "勾选 0 行（id=%s）" % r["id"]] for r in todo]
                break
        if not wh:
            res["fail"] += [[r["po"], "拆分结果里没有该单的仓库，拒绝盲下单（怕下错仓）"]
                            for r in todo]
            break
        sel = set_selects(pg, wh, platform=platform)
        log("   选仓渠道：%s" % json.dumps(sel, ensure_ascii=False)[:220])
        if not (sel.get("chqd") and sel.get("st")):
            res["fail"] += [[r["po"], "批量栏选仓/渠道设置失败：%s"
                             % json.dumps(sel, ensure_ascii=False)[:160]] for r in todo]
            break
        click_submit(pg)
        time.sleep(3)
        arm(pg)
        # ★ 判据只认回读：提交后重新整批查，行里必须出现「发货仓库 + 出货渠道」。
        #   （页面正文里「成功/失败」字样会命中历史行，不能当回执用——现实证误判过。）
        time.sleep(2)
        try:
            rows_chk = do_query(pg, [r["po"] for r in todo], shop_id=shop_id, wait=90)
            chk_by_po = {x["po"]: x for x in rows_chk}
        except SystemExit:
            chk_by_po = {}
        lacking = [r for r in todo
                   if not ((chk_by_po.get(r["po"]) or {}).get("wh")
                           and (chk_by_po.get(r["po"]) or {}).get("qd"))]
        for r in todo:
            x = chk_by_po.get(r["po"]) or {}
            if x:
                r.update({k: x.get(k) for k in ("id", "status", "wh", "qd", "err")})
        log("   渠道回读：%d/%d 行已带仓库+出货渠道"
            % (len(todo) - len(lacking), len(todo)))
        if lacking:
            # ★ 2026-11-11 真机兜底：页面「提交」点了但回读不到（BD 24 单全灭），
            #   同参数走 HTTP 一次就成功 ⇒ 这里自动补一刀，再回读一次，别直接判失败。
            log("   ⚠ 页面提交未生效 %d 单 → 走 HTTP 补提交" % len(lacking))
            try:
                hr = http_set_wh([r["id"] for r in lacking], wh, platform=platform, log=log)
            except Exception as ex:                             # noqa: BLE001
                hr = {"ok": 0, "why": str(ex)[:160]}
            if hr.get("ok"):
                time.sleep(3)
                try:
                    bk = do_query(pg, [r["po"] for r in lacking], shop_id=shop_id, wait=90)
                    bk_by = {x["po"]: x for x in bk}
                except SystemExit:
                    bk_by = {}
                still, fixed = [], []
                for r in lacking:
                    x = bk_by.get(r["po"]) or {}
                    if x.get("wh") and x.get("qd"):
                        r.update({k: x.get(k) for k in ("id", "status", "wh", "qd", "err")})
                        fixed.append(r)
                    else:
                        still.append(r)
                log("   [HTTP] 补提交后：成功 %d / 仍不行 %d" % (len(fixed), len(still)))
                if still:
                    res["fail"] += [[r["po"], "页面提交与 HTTP 补提交后仍读不到仓库/渠道"]
                                    for r in still]
                keep = set(id(r) for r in still)
                todo = [r for r in todo if id(r) not in keep]
            else:
                res["fail"] += [[r["po"], "修改仓库渠道提交后回读不到仓库/渠道（wh=- qd=-）；"
                                          "HTTP 兜底也失败：%s" % (hr.get("why") or "")[:80]]
                                for r in lacking]
                todo = [r for r in todo if r not in lacking]
            if not todo:
                break
        if not set_checks(pg, [r["id"] for r in todo]):
            res["fail"] += [[r["po"], "提交后勾选 0 行（id=%s）" % r["id"]] for r in todo]
            break
        # ⑤ 下单代发
        conf = click_xiadan(pg, warehouse=wh)
        log("   下单代发回执：%s" % json.dumps(conf.get("confirm") or {}, ensure_ascii=False)[:300])
        receipt = (conf.get("confirm") or {}).get("text") or ""
        want_ids = {r["id"] for r in todo}
        done = {}
        waited = 0
        while waited < min(600, max(60, 12 * len(want_ids))):
            time.sleep(5)
            waited += 5
            arm(pg)
            try:
                now = [x for x in read_state(pg) if x["id"] in want_ids]
            except Exception:                       # noqa: BLE001
                now = []
            for x in now:
                done[x["id"]] = x
            if len(done) >= len(want_ids) and all(
                    (x.get("status") or "") != "未下单" for x in now):
                break
        # 回读：状态列才是人眼看到的那一列
        try:
            arm(pg)
            after = do_query(pg, [r["po"] for r in todo], shop_id=shop_id, wait=90)
            after_by_po = {x["po"]: x for x in after}
        except SystemExit:
            after_by_po = {}
        for r in todo:
            x = after_by_po.get(r["po"]) or done.get(r["id"]) or {}
            st, cell = (x.get("status") or ""), (x.get("err") or "").strip()
            wb = wb_of(cell)
            if wb:
                res["ok"].append([r["po"], "运单号=%s" % wb])
            elif st and st != "未下单":
                res["ok"].append([r["po"], "状态=%s" % st])
            else:
                res["fail"].append([r["po"], "提交+下单代发后状态仍=未下单（回执：%s）"
                                    % ((cell or receipt or "无").replace("\n", " ")[:80])])
        # ★ 2026-11-12 真机：BS `GSU1AU28U001T97` 回执已「下单成功」，当场回读仍是「未下单」，
        #   十分钟后 `pw_now.json` 已经是「已下单」——列表更新比回执慢。旧逻辑只看一眼就判
        #   失败，把成功的单塞进失败表交人工（人工会去重复下单）。这里对**刚判失败**的单
        #   再等 `LATE_RECHECK_S` 复核一次，仍「未下单」才算失败。
        late = [c for c in res["fail"]
                if c[0] in {r["po"] for r in todo} and "未下单" in str(c[1])]
        if late:
            log("   延迟复核 %d 单（等 %ds 再看列表，回执成功但列表常滞后）"
                % (len(late), LATE_RECHECK_S))
            time.sleep(LATE_RECHECK_S)
            try:
                arm(pg)
                chk = {x["po"]: x for x in
                       do_query(pg, [c[0] for c in late], shop_id=shop_id, wait=90)}
            except SystemExit:
                chk = {}
            for c in late:
                x = chk.get(c[0]) or {}
                st, cell = (x.get("status") or ""), (x.get("err") or "").strip()
                wb = wb_of(cell)
                if wb or (st and st != "未下单"):
                    res["fail"].remove(c)
                    res["ok"].append([c[0], ("运单号=%s" % wb) if wb
                                      else "状态=%s（延迟复核）" % st])
                    log("   [延迟复核] %s → %s" % (c[0], st or wb))
        got = {r["po"] for r in todo}
        left = [p for p in left if p not in got and p not in {s[0] for s in res["skip"]}]
        if time.time() - t0 > budget_s:
            res["unfinished"] += [[p, "预算 %.0fs 用完" % budget_s] for p in left]
            left = []
            break
    if left:
        # ★ 2026-11-11：这里**不再**逐单补查（用户明确不要一个一个搜）。
        #   整批查不到的单，只做一次「整批重查」（页面上可能还在翻页/异步刷新），
        #   仍查不到就如实记为未查到，交人工。
        try:
            again = do_query(pg, left, shop_id="", wait=120)
            got2 = {r["po"] for r in again if r["po"] in set(left)}
        except SystemExit:
            got2 = set()
        for p in left:
            res["unfinished"].append([p, "整批查询两轮都未返回该单（不逐单搜，交人工）"]
                                     if p not in got2 else
                                     [p, "整批查询有行但未产生终态"])
    log("   [下单代发] total=%d ok=%d fail=%d skip=%d unfinished=%d"
        % (len(pos), len(res["ok"]), len(res["fail"]), len(res["skip"]),
           len(res["unfinished"])))
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["query", "setwh", "xiadan", "check", "fixsku"])
    ap.add_argument("--list", nargs="*", default=[])
    ap.add_argument("--wh", default="")
    ap.add_argument("--tag", default="q")
    ap.add_argument("--shop", default="",
                    help="店铺（BEL/BHY/BS/BD/BAN…）：决定指定快递与出货渠道（temu vs Shein）")
    ap.add_argument("--orders", nargs="*", default=[],
                    help="fixsku 用：id:Fnsku 或 po:Fnsku")
    ap.add_argument("--commit", action="store_true", help="fixsku 才会真提交")
    a = ap.parse_args()
    os.makedirs(PEEK, exist_ok=True)

    pos = []
    for f in a.list:
        with open(f, encoding="utf-8") as fh:
            pos += [x.strip() for x in fh if x.strip()]
    print("清单 %d 单" % len(pos))

    pg = page()
    ensure_page(pg)

    if a.cmd == "fixsku":
        global COMMIT
        COMMIT = bool(a.commit)
        # 支持两种写法：<订单id>:<Fnsku> / <参考单号>:<Fnsku>
        plan = []
        for spec in a.orders:
            left, _, fn = spec.rpartition(":")
            if not left or not fn:
                raise SystemExit("格式应为 id:Fnsku 或 参考单号:Fnsku，收到 %r" % spec)
            plan.append((left, fn))
        # 参考单号 → id（用订单列表页反查）
        need_po = [p for p, _ in plan if not p.isdigit()]
        po2id = {}
        if need_po:
            for x in do_query(pg, need_po):
                po2id[x["po"]] = x["id"]
        print("待改 SKU %d 单（commit=%s）" % (len(plan), COMMIT))
        from collections import defaultdict
        res = defaultdict(list)
        for left, fn in plan:
            oid = left if left.isdigit() else po2id.get(left, "")
            if not oid:
                res["找不到订单"].append(left)
                continue
            r = fix_one_sku(pg, oid, fn)
            print("  %-8s %-30s → %-16s %s" % (oid, r.get("old", ""), fn,
                                               "OK" if r.get("ok") else ("DRY" if r.get("dry")
                                                                         else r.get("why"))))
            res["OK" if r.get("ok") else (r.get("why") or "失败")].append("%s(%s)" % (left, oid))
        json.dump({k: v for k, v in res.items()},
                  open(os.path.join(OUT, "hwc_fixsku.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("汇总:", {k: len(v) for k, v in res.items()})
        return 0

    if a.cmd in ("query", "check"):
        rows = do_query(pg, pos) if a.cmd == "query" else read_state(pg)
        from collections import Counter
        print("列表 %d 行；状态 %s；仓库 %s" % (
            len(rows), dict(Counter(x["status"] for x in rows)),
            dict(Counter(x["wh"] or "(空)" for x in rows))))
        p = os.path.join(OUT, "hwc_%s.json" % a.tag)
        json.dump(rows, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        open(os.path.join(PEEK, "hwc_%s.png" % a.tag), "wb").write(pg.screenshot())
        print("落盘 %s" % p)
        return 0

    if a.cmd == "setwh":
        rows = read_state(pg)
        if not rows:
            raise SystemExit("[FATAL] 页面没有订单行，先跑 query")
        n = set_checks(pg, [x["id"] for x in rows])
        print("勾选 %d / %d 单" % (n, len(rows)))
        goto_batch_zone(pg)
        info = set_selects(pg, a.wh, platform=platform_of_store(a.shop))
        print("下拉: %s" % info)
        if not info.get("chqd"):
            raise SystemExit("[FATAL] 没现取到「%s」渠道 id"
                             % PLATFORM_ROUTE[platform_of_store(a.shop)][1])
        print("渠道 id=%s (%s)" % (info["chqd"], info["chqd_name"]))
        open(os.path.join(PEEK, "hwc_before_submit.png"), "wb").write(pg.screenshot())
        click_submit(pg)
        time.sleep(8)
        arm(pg)
        print("提交后 url: %s" % pg.url[:110])
        rows2 = do_query(pg, pos)
        bad = [x["po"] for x in rows2 if a.wh not in (x["wh"] or "")]
        qbad = [x["po"] for x in rows2 if "官方面单" not in (x["qd"] or "")]
        print("回读：仓库正确 %d/%d，渠道正确 %d/%d"
              % (len(rows2) - len(bad), len(rows2), len(rows2) - len(qbad), len(rows2)))
        json.dump(rows2, open(os.path.join(OUT, "hwc_after_setwh_%s.json" % a.wh), "w",
                              encoding="utf-8"), ensure_ascii=False, indent=1)
        open(os.path.join(PEEK, "hwc_after_setwh.png"), "wb").write(pg.screenshot())
        if bad:
            print("[WARN] 仓库没改上: %s" % ", ".join(bad[:10]))
            return 42
        return 0

    if a.cmd == "xiadan":
        rows = read_state(pg)
        if not rows:
            raise SystemExit("[FATAL] 页面没有订单行，先跑 query")
        empty = [x["po"] for x in rows if not (x["wh"] or "")]
        if empty:
            print("[WARN] 有 %d 单发货仓库为空（%s…），先确认是否要设仓"
                  % (len(empty), ", ".join(empty[:3])))
        n = set_checks(pg, [x["id"] for x in rows])
        print("勾选 %d / %d 单" % (n, len(rows)))
        info = click_xiadan(pg)
        print("按钮: %s" % info)
        st = wait_xiadan(pg, n)
        print("等待结果: %s" % st)
        open(os.path.join(PEEK, "hwc_xiadan_done.png"), "wb").write(pg.screenshot())
        rows2 = do_query(pg, pos, wait=40)
        from collections import Counter
        print("回读状态 %s" % dict(Counter(x["status"] for x in rows2)))
        json.dump(rows2, open(os.path.join(OUT, "hwc_after_xiadan.json"), "w",
                              encoding="utf-8"), ensure_ascii=False, indent=1)
        ok = [x for x in rows2 if x["status"] == "已下单" and wb_of(x["err"])]
        print("已下单且有运单号: %d / %d" % (len(ok), len(rows2)))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
