# -*- coding: utf-8 -*-
"""lib/temu_sales_driver.py — TEMU 订单导出 driver（适配新导出界面 export-order.html）。

流程（2026-09-02 实测，界面已改版）：
    1. 连紫鸟窗口 CDP（cdp_transport 按 target 直连，不 spawn、不做 browser attach）
    2. 打开导出页 https://agentseller-us.temu.com/mmsos/export-order.html
    3. 设「订单日期」= 指定日（点开日历 → 点目标月面板的目标日两次=开始+结束 → 确定）
    4. 选「订单类型」= 全部（下拉第一个可见项）、《订单状态》= 全部
    5. 点「确认导出」→ 下方「导出记录」生成任务 → 轮询 状态=已完成 → 点该行「下载」
    6. 下载的是 CSV（UTF-8 BOM, 约39列）→ openpyxl 转为 xlsx 落 out_dir

关键点（实测）：
    - 日期输入框 input.IPT_input_123.RPR_input_123 为 readonly React 受控：set value 注入
      会被重渲染清空、.fill()/.type() 因 readonly 超时、「昨天」快捷预设给出错误范围 ——
      只能用「点开日历 → 点目标月面板的目标日格子两次」的路子。
    - 日历为双/多月视图（左=上月、右=当月，可再含次月）：须点「目标月面板」里那一天，
      勿取相邻月（曾误点成 10/12 月）。
    - 订单类型/订单状态下拉为自定义控件，选项在 [class*=dropdown] [class*=item]，
      「第一个可见项=全部」；用 JS el.click() 点（常规 click 会因 actionability 超时/点空）。
    - 点「确认导出」后：导出记录 Task 生成，状态由生成 → 已完成；需等「已完成」再点「下载」。
"""
from __future__ import annotations

import os
import json
import re
import shutil
import time
import csv
from datetime import datetime, timezone, timedelta

_EXPORT_URL = "https://agentseller-us.temu.com/mmsos/export-order.html"
# 日期范围输入框
_DATE_INPUT_SELS = ["input.IPT_input_123.RPR_input_123", "div.RPR_inputWrapper_123 input",
                    "input[data-testid='beast-core-rangePicker-htmlInput']"]
_DATE_INPUT_WRAP = ["div.RPR_inputWrapper_123", "div.IPT_inputWrapper_123.RPR_inputWrapper_123",
                    "div.IPT_inputWrapper_123"]
# 日历日期格子（双/多月视图）
_DATE_CELL_SELS = ["div.RPR_cell_123", "td.RPR_tdDay_123"]
_CONFIRM_BTN = ["确定", "确认"]                     # 日期选择器确认
_EXPORT_CONFIRM_BTN = "确认导出"                     # 导出页确认导出按钮
_DROPDOWN_ITEM = "li.cIL_item_123"                    # 下拉选项（首个可见项=「全部/全选」；旧 [class*=dropdown] [class*=item] 已随改版失效）
# 订单类型/订单状态当前选中值 chip 文本（用于点开对应下拉）
_ORDER_TYPE_CHIP = "卖家履约订单"                    # 订单类型下拉当前值
_ORDER_STATUS_CHIP = "待发货"                        # 订单状态下拉当前值


def _cnt(pg, sels):
    """返回候选选择器中第一个 count>0 的 locator；都无则 None。"""
    for s in sels:
        try:
            l = pg.locator(s)
            if l.count() > 0:
                return l
        except Exception:
            continue
    return None


def _js_click(loc) -> bool:
    try:
        loc.evaluate("el => el.click()")
        return True
    except Exception:
        return False


def _click_text(pg, texts, timeout=5000, js=False) -> bool:
    for t in texts:
        try:
            l = pg.get_by_text(t, exact=True)
            if l.count():
                for i in range(min(l.count(), 6)):
                    try:
                        if js:
                            l.nth(i).evaluate("el => el.click()")
                            return True
                        l.nth(i).click(timeout=timeout)
                        return True
                    except Exception:
                        continue
        except Exception:
            continue
    return False


def _close_modal(pg) -> None:
    """关闭可能的残留弹窗（推广弹窗/上次卡住的 Modal），防止遮挡。"""
    for _ in range(3):
        closed = False
        for sel in ["svg[data-testid=beast-core-icon-close]", "[data-testid=beast-core-icon-close]",
                    "div[class*=MDL_] svg", "[class*=icon-close]"]:
            try:
                l = pg.locator(sel)
                if l.count():
                    l.first.click(timeout=1200)
                    closed = True
                    break
            except Exception:
                pass
        try:
            c = pg.get_by_text("取消", exact=True)
            for i in range(min(c.count(), 6)):
                try:
                    if c.nth(i).is_visible():
                        c.nth(i).click(timeout=1200)
                        closed = True
                except Exception:
                    break
        except Exception:
            pass
        try:
            pg.keyboard.press("Escape")
        except Exception:
            pass
        pg.wait_for_timeout(600)
        if not closed:
            break
    pg.wait_for_timeout(400)


def _url_parts(u: str):
    from urllib.parse import urlparse
    return urlparse(str(u or ""))


def _url_is_export_page(u: str) -> bool:
    """是否已在导出页：host + path 判（禁止整串 `in`——URL query 的 redirectUrl 里也含 export-order.html）。"""
    try:
        p = _url_parts(u)
        return p.netloc == "agentseller-us.temu.com" and "/mmsos/export-order" in (p.path or "").lower()
    except Exception:
        return False


def _url_is_auth_page(u: str) -> bool:
    """是否停在登录/认证页（seller 登录页 或 卖家中心 auth/login 路径）。"""
    try:
        p = _url_parts(u)
        host = (p.netloc or "").lower()
        path = (p.path or "").lower()
        if "kuajingmaihuo.com" in host:
            return True
        if host == "agentseller-us.temu.com" and any(k in path for k in ("auth", "login", "authentication")):
            return True
        return False
    except Exception:
        return False


def _page_rank(u: str) -> int:
    """页面优先级（越小越优先）：导出页 → 订单页 → 其它 /mmsos 页 → 卖家中心其它页 → 其它 temu 页 → 认证/登录页（垫底）。

    为什么要按优先级而不是"取第一个"（2026-09-16 实测，三家全中）：授权跳转后窗口里会留下一个
    **空白且 0 个 input 的 `seller.kuajingmaihuo.com/settle/seller-login` 死 tab**，且它排在 tab 列表最前；
    旧实现取第一个含 temu/kuajingmaihuo 的页 ⇒ 取到该死 tab ⇒ 导出直接误报「停在登录/认证页，需先授权」。
    认证页仍保留为**最后兜底**：窗口里确实只剩认证页时，仍要按"需先授权"如实报错（不能把真需授权也吞掉）。
    """
    try:
        p = _url_parts(u)
        host = (p.netloc or "").lower()
        path = (p.path or "").lower()
        if _url_is_auth_page(u):
            return 5
        if host == "agentseller-us.temu.com":
            if "/mmsos/export-order" in path:
                return 0
            if "/mmsos/orders" in path:
                return 1
            if "/mmsos/" in path:
                return 2
            return 3
        if "temu.com" in host or "kuajingmaihuo.com" in host:
            return 4
        return 9
    except Exception:
        return 9


def _pick_page(b):
    """从窗口所有 tab 里挑一个要操作的 page（按 _page_rank 取最优，而不是取第一个）。"""
    best = None
    best_rank = 99
    for ctx in b.contexts:
        for page in ctx.pages:
            u = ""
            try:
                u = page.url or ""
            except Exception:
                continue
            if not ("temu.com" in u or "kuajingmaihuo.com" in u):
                continue
            r = _page_rank(u)
            if r < best_rank:
                best, best_rank = page, r
    return best


def _open_own_tab(b, url: str, port: int):
    """在窗口里**新开一个自己的后台标签页**并导航到 url（绝不动现有页）。

    2026-10-27 用户口径：「执行定时任务时打开新标签，不允许把我打开的标签
    覆盖，尤其是 BEL」；2026-10-28 追加：「自己新开页，跑完即关」。

    为什么不用 `ctx.new_page()`：那会把紫鸟窗口提到最前（2026-09-14 用户实测
    「窗口会置顶」）。这里走原生 CDP `Target.createTarget(background=true)`。

    返回 `(page, targetId)`；**调用方必须在 finally 里 `cdp_transport.close_target(port, tid)`**，
    否则开出来的页就留在用户窗口里了（旧实现就是这样漏的：`b.close()` 只是 detach）。

    ★ 2026-10-28 修两处：
      ① 认页只按「快照之外 + URL 命中」的对象身份，**不再按「窗口里任意导出页」兜底** ——
         那个兜底会在新页尚未出现时命中**用户自己开的导出页**，后面全在用户页上操作；
      ② 端口由调用方传入（旧代码从 Playwright 私有属性反推端口，拿不到就静默降级）。
    """
    import json
    import urllib.request

    try:
        before = list(b.contexts[0].pages)
    except Exception:  # noqa: BLE001
        before = []
    tid = ""
    try:
        ver = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/json/version" % int(port), timeout=5).read())
        from cdp_transport import Cdp  # noqa: PLC0415
        # ★ 2026-11-06 修（BHY/BEL 导出两天都卡在「日期选择器未生效」的真因）：
        #   `background: True` 开出来的页 `document.visibilityState === 'hidden'`，
        #   页面里的控件**不绑定事件**（实测点日期框右侧图标，格子数恒为 5 = 只有分页器；
        #   同一 URL 在前台页上点，立刻出 87 个日历格子）。
        #   ⇒ 必须 `background: False`。跑完由 finally 里的 `close_target` 关掉，
        #     不会把页留在用户窗口里。
        r = Cdp(ver["webSocketDebuggerUrl"]).send(
            "Target.createTarget", {"url": url, "background": False})
        tid = ((r.get("result") or {}).get("targetId")) or ""
    except Exception:  # noqa: BLE001
        tid = ""
    if not tid:
        raise RuntimeError("开不出自己的后台标签页（createTarget 失败）：%s" % url[:100])

    deadline = time.time() + 20.0
    while time.time() < deadline:
        for ctx in b.contexts:
            for page in ctx.pages:
                if any(page is x for x in before):
                    continue                     # 运行前就有 = 用户的页，绝不碰
                try:
                    if _url_is_export_page(page.url or "") or (page.url or "") in ("", "about:blank"):
                        return page, tid         # 刚开出来、可能还在导航，交给调用方等
                except Exception:  # noqa: BLE001
                    continue
        time.sleep(0.5)
    # 认不到自己的页 → 把已经建出来的 target 尽力关掉，绝不留垃圾
    try:
        from cdp_transport import close_target  # noqa: PLC0415
        close_target(port, tid)
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError("自己的后台标签页没出现（targetId=%s，已尝试关闭）" % tid)


def _connect(port: int):
    """连紫鸟窗口 CDP。

    ★ 2026-11-06 修（BHY 导出实测连不上）：原实现用
      `playwright.chromium.connect_over_cdp(...)`，它做的是 **browser-level attach**：
      会先把窗口里**所有** target 都接一遍。BHY 窗口当时有 5 个页、其中 3 个是
      `agentseller.temu.com/auth/authentication` 残留 ⇒ attach 被拖到超时：
        `BrowserType.connect_over_cdp: Timeout 15000ms exceeded`
      端口是对的、订单页也活着，纯粹是连接方式的问题。
      ⇒ 改用本仓已验证的 `cdp_transport`（**按 target 直连**，不做 browser attach），
        它的 `CdpBrowser/CdpContext` 是 Playwright 的鸭子类型壳，
        下游 `_pick_page` / `locator` / `get_by_text` 全部照旧可用。
    """
    from cdp_transport import CdpBrowser  # noqa: PLC0415

    b = CdpBrowser(int(port))
    pg = _pick_page(b)
    if pg is None:
        for ctx in b.contexts:
            for page in ctx.pages:
                pg = page
                break
            if pg:
                break
    if pg is None:
        raise RuntimeError("窗口 %d 里没有可用页面" % port)
    return None, b, pg


def _month_header_text(day) -> str:
    """目标月月头文本（如 2026年 9月）。"""
    return "%d年 %d月" % (day.year, day.month)


def _date_day_cells(pg):
    """日历里的「日本月格子」坐标（**不返回 locator**，直接给 `[{day,x,y}]`）。

    ★ 2026-11-06：`div.RPR_cell_123` / `td.RPR_tdDay_123` 改版后**已失效**
      （实测日历格子的 class 是空的 `<div>28</div>`）；
      而 `cdp_transport` 的 `_Locator` 是 Playwright 的最小壳、**没有 `.filter()`**
      ⇒ 用 `locator(...).filter(has_text=...)` 会抛
      `'_Locator' object has no attribute 'filter'`。
      故直接回坐标数组，调用方按 `day` 选一个再 `dispatch_mouse`。

    ★ 2026-11-07 排除溢出格：日历每面板首尾会溢出相邻月的灰色格子，
      class `RPR_outOfMonth_123`（文字颜色 rgba(0,0,0,0.24)）。
      不排它会点到**别的月**的同数字 —— 实测 Start=10-30 被点成 11 月面板
      溢出的 12-06，区间变成 10-30 ~ 12-06。
      ⚠ 2026-11-06 的「class 是空 div」说法与新观察矛盾：格子本体
        `<div class="RPR_cell_123">` + 父级 `<td class="RPR_tdDay_123">`，
        以本轮实测为准。
    """
    try:
        raw = pg.evaluate(r"""() => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
          const isOut = e => {
            for (let n = e; n && n !== document.body; n = n.parentElement) {
              const c = String(n.className || '');
              if (c.includes('RPR_outOfMonth')) return true;
            }
            return false;
          };
          return JSON.stringify([...document.querySelectorAll('div,td,span')]
            .filter(vis)
            .filter(e => e.children.length === 0 && /^\d{1,2}$/.test(txt(e))
                      && +txt(e) >= 1 && +txt(e) <= 31)
            .filter(e => !isOut(e))
            .map(e => { const r = e.getBoundingClientRect();
              return {day: +txt(e), x: Math.round(r.x + r.width/2),
                      y: Math.round(r.y + r.height/2)}; }));
        }""")
        return json.loads(str(raw))
    except Exception:  # noqa: BLE001
        return []


def _open_date_picker(pg) -> bool:
    """打开「订单日期」日历面板。

    ★ 2026-11-06 修（BHY/BEL 两店实测都弹不出，报「日期选择器未生效（找不到日历格子）」）：
      两个独立原因叠在一起：
        ① 日期输入框是 `readOnly=True` 的**展示框**，点它本身不会开日历；
           日历图标在输入框**右端内侧**（实测 `right - 12`，垂直居中）。
        ② 页面刚加载完时点击**无效**（控件还没绑定事件）；
           实测同一页 `reload` 后等 12s → 第一次点就出 87 个格子；
           不等就点，连点 3 次也是 0 个格子。
      ⇒ 现在：等页面静止 → 点右端图标 → **读回格子数确认弹出**，不成就换坐标/重试。
    """
    # ① 等页面静止（DOM 行数 + 日期框坐标都不再变），最多 12s
    prev = None
    for _ in range(12):
        try:
            sig = pg.evaluate(r"""() => {
              const e = [...document.querySelectorAll('input')]
                .find(x => /开始时间/.test(x.placeholder||''));
              if (!e) return 'no';
              const r = e.getBoundingClientRect();
              return [Math.round(r.x), Math.round(r.y), Math.round(r.width),
                      document.querySelectorAll('table tbody tr').length].join(',');
            }""")
        except Exception:  # noqa: BLE001
            sig = None
        if sig and sig == prev and sig != "no":
            break
        prev = sig
        pg.wait_for_timeout(1000)

    box = pg.evaluate(r"""() => {
      const e = [...document.querySelectorAll('input')]
        .find(x => /开始时间/.test(x.placeholder||''));
      if (!e) return '{}';
      e.scrollIntoView({block: 'center'});
      const r = e.getBoundingClientRect();
      return JSON.stringify({iconX: Math.round(r.right - 12),
                             iconY: Math.round(r.y + r.height / 2),
                             cx: Math.round(r.x + r.width / 2),
                             cy: Math.round(r.y + r.height / 2)});
    }""")
    try:
        b = json.loads(str(box))
    except Exception:  # noqa: BLE001
        return False
    if not b:
        return False
    # ② 点右端图标；不成再试中心、再试一次图标
    for x, y in ((b["iconX"], b["iconY"]), (b["cx"], b["cy"]), (b["iconX"], b["iconY"])):
        pg.dispatch_mouse(x, y)
        pg.wait_for_timeout(1500)
        if _count_date_cells(pg) > 10:
            return True
    return False


def _count_date_cells(pg) -> int:
    """当前页面上可见的「1~31 的纯数字格子」数量（日历是否弹出）。"""
    try:
        n = pg.evaluate(r"""() => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
          return [...document.querySelectorAll('*')].filter(vis)
            .filter(e => /^\d{1,2}$/.test(txt(e)) && e.children.length===0
                      && +txt(e)>=1 && +txt(e)<=31).length;
        }""")
        return int(n or 0)
    except Exception:  # noqa: BLE001
        return 0


def _calendar_panels(pg):
    """日历各面板：`[{month, x0, x1, y0, y1}]`，按 x 排序（左→右）。

    ★ 2026-11-07 实测：月头 `RPR_dateText_123` 只有「10月」这种月份数字（无年份），
      所以**不能只按数字挑格子**——左右面板各有一个「30」，取 x 最大会点错月。
      这里给出「面板的横向范围」，`_pick_day_cell` 用它在**目标月所在面板**里找格子。
    """
    try:
        raw = pg.evaluate(r"""() => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0; };
          const heads = [...document.querySelectorAll('[class*=RPR_panelHeader_123]')]
            .filter(vis)
            .map(e => {
              const r = e.getBoundingClientRect();
              const t = [...e.querySelectorAll('[class*=RPR_dateText_123]')]
                .map(x => (x.innerText || '').replace(/\s+/g, ''))[0] || '';
              return {month: t, x0: Math.round(r.x), x1: Math.round(r.right),
                      y: Math.round(r.y)};
            })
            .sort((a, b) => a.x0 - b.x0);
          // 面板高度：从月头到日历底部（用最后一个格子的 y 兜）
          const cells = [...document.querySelectorAll('div,td,span')].filter(vis)
            .filter(e => e.children.length === 0
                      && /^\d{1,2}$/.test((e.innerText||'').replace(/\s+/g,''))
                      && +((e.innerText||'').replace(/\s+/g,'')) >= 1
                      && +((e.innerText||'').replace(/\s+/g,'')) <= 31)
            .map(e => e.getBoundingClientRect())
            .filter(r => r.y > (heads[0] ? heads[0].y : 0));
          const bottom = cells.length ? Math.max(...cells.map(r => r.bottom)) : 0;
          return JSON.stringify(heads.map(h => ({month: h.month,
            x0: h.x0, x1: h.x1, y0: h.y - 40, y1: Math.round(bottom)})));
        }""")
        return json.loads(str(raw)) or []
    except Exception:  # noqa: BLE001
        return []


def _read_date_input(pg) -> str:
    """读「订单日期」输入框当前值（`2026-10-30 ~ 2026-11-06`）。"""
    try:
        raw = pg.evaluate(r"""() => {
          const e = [...document.querySelectorAll('input')]
            .find(x => /开始时间/.test(x.placeholder||''));
          return e ? (e.value || '') : '';
        }""")
        return str(raw or "")
    except Exception:  # noqa: BLE001
        return ""


def _pick_day_cell(cells: list, day, log=print, panels=None):
    """在日历格子里找 `day` 那天的坐标。

    ★ 2026-11-07 修：日历是双面板（左=上月 / 右=本月），两个面板**各有自己的
      「30」**（左=10月30、右=11月30）。旧逻辑「取 x 最大」在 10 月 30 号上会
      点到 11 月面板的 30，区间就变成 11-06~11-30。
      现在：先用 `panels` 按月份锁定**目标月所在面板的横向范围**，只在该范围里找；
      同一范围里若仍有多个候选（罕见），取 y 最靠上的（本月第一周优先）。
    """
    same = [c for c in cells if c["day"] == day.day]
    if not same:
        return None
    box = None
    for p in (panels or []):
        if str(p.get("month") or "").replace(" ", "") == "%d月" % day.month:
            box = p
            break
    if box:
        inner = [c for c in same
                 if box["x0"] - 4 <= c["x"] <= box["x1"] + 4
                 and box["y0"] <= c["y"] <= box["y1"] + 4]
        if inner:
            same = inner
    pick = max(same, key=lambda c: (-c["y"], c["x"]))
    log("[日期] 点 %d-%02d-%02d @(%d,%d)（候选 %d 个%s）"
        % (day.year, day.month, day.day, pick["x"], pick["y"], len(same),
           "，已按面板锁定" if box else "，⚠ 没匹配到面板"))
    return pick


def _calendar_month_headers(pg):
    """读日历各面板的月头（按 x 排序，返回月份数字列表，如 [10, 11]）。

    ★ 2026-11-07 实测 DOM：月头是 `RPR_panelHeader_123` 里的 `RPR_dateText_123`，
      文本只有「10月」「11月」（**没有年份**）；年份信息拿不到，跨年（12月→1月）
      由调用方用「连续翻页不会回到起点」的假设兜住。
    """
    try:
        raw = pg.evaluate(r"""() => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0; };
          return JSON.stringify([...document.querySelectorAll('[class*=RPR_dateText_123]')]
            .filter(vis)
            .map(e => { const r = e.getBoundingClientRect();
              return {t: (e.innerText||'').replace(/\s+/g,''), x: Math.round(r.x)}; })
            .sort((a,b) => a.x - b.x)
            .map(e => e.t));
        }""")
        out = []
        for t in (json.loads(str(raw)) or []):
            m = re.match(r"^(\d{1,2})月$", t)
            if m:
                out.append(int(m.group(1)))
        return out
    except Exception:  # noqa: BLE001
        return []


def _click_month_nav(pg, forward: bool) -> bool:
    """点日历头部的上/下月 SVG 箭头一次（forward=True 往未来方向）。

    ★ 2026-11-07 实测 DOM：没有「‹›」文字按钮；翻月箭头是每个
      `RPR_panelHeader_123` 两端的 **svg**（左面板的 svg 在月头左端＝上月，
      右面板的 svg 在月头右端＝下月），坐标 x≈553 / x≈1053。SVG 上的
      click 事件绑在 svg 元素本身，直接 dispatch 坐标点击。
    """
    try:
        raw = pg.evaluate(r"""(fwd) => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const heads = [...document.querySelectorAll('[class*=RPR_panelHeader_123]')]
            .filter(vis)
            .map(e => { const r = e.getBoundingClientRect();
              return {x: Math.round(r.x), r: Math.round(r.right),
                      y: Math.round(r.y + r.height/2)}; })
            .sort((a,b) => a.x - b.x);
          if (!heads.length) return 'no-header';
          // 每个面板头的 svg 都在 header 内；取目标面板最外端的那个
          const h = fwd ? heads[heads.length - 1] : heads[0];
          const svgs = [...document.querySelectorAll('svg')].filter(vis)
            .map(e => { const r = e.getBoundingClientRect();
              return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                      left: Math.round(r.x)}; });
          const inHead = svgs.filter(s => Math.abs(s.y - h.y) < 8
                                       && (fwd ? s.x > h.r - 60 : s.x < h.x + 60));
          const el = inHead[fwd ? (inHead.length - 1) : 0];
          if (!el) return 'no-svg';
          return JSON.stringify(el);
        }""", forward)
        if not str(raw).startswith("{"):
            print("[日期] 翻月箭头没找到：%s" % raw)
            return False
        import json as _json
        p = _json.loads(str(raw))
        pg.dispatch_mouse(p["x"], p["y"])
        return True
    except Exception as e:  # noqa: BLE001
        print("[日期] 点翻月箭头失败：%s" % str(e)[:120])
        return False


def _ensure_months_visible(pg, start_day, end_day, log=print) -> bool:
    """让 Start 与 End 所在月**同时**出现在日历面板里（跨月时翻页对齐）。

    rangePicker 在 Start/End 落在不同面板时判定会错位（实测 10-30~11-06 被
    展开成 10-29~12-06）。所以先看月头（只有「10月」这种月份数字，没有年份），
    若 End 的月还没出现就点「下月」，若 Start 的月已经翻出视野就点「上月」；
    最多翻 3 次。
    """
    sm, em = start_day.month, end_day.month
    # 月份数字只差 1 视为相邻（跨年 12→1 特判：差 11 也算相邻）
    adjacent = (em - sm) % 12 in (0, 1)

    for _ in range(3):
        ms = _calendar_month_headers(pg)
        have_s = sm in ms
        have_e = em in ms
        log("[日期] 面板月=%s（Start %d月：%s / End %d月：%s）"
            % (ms, sm, "有" if have_s else "无", em, "有" if have_e else "无"))
        if have_s and have_e:
            return True
        if not have_e:
            _click_month_nav(pg, forward=True)
        elif not have_s:
            _click_month_nav(pg, forward=False)
        pg.wait_for_timeout(1200)

    ms = _calendar_month_headers(pg)
    ok = sm in ms and em in ms
    if not ok:
        log("[日期] ⚠ 翻页后仍未对齐两月（%s），跨月点击可能错位" % ms)
    return ok


def _set_export_date(pg, day) -> bool:
    """设「订单日期」区间 = Start=当天往前推 7 天，End=当天。

    ★ 2026-11-07 用户口径：「以后这样设定 start=当天往前推 7 天，end=当天」。
      之前是单日（连点同一格两次 ⇒ Start=End=当天）；现改为 8 天窗口：
        ① 第一次点 Start 格（day-7）
        ② 第二次点 End 格（day）—— rangePicker 靠「第一次点击=起点、第二次=终点」区分
      两个日子可能落在相邻月面板（如 10-31 → 11-07），`_pick_day_cell` 按
      「该数字在同面板的最右出现」各自定位，跨月也能点到正确格子。

    兼容：`day` 传来的仍是「数据日期」（缺省=昨天 PT）；Start 用它往前推 7 天。
    """
    inp = _cnt(pg, _DATE_INPUT_SELS)
    if inp is None:
        return False
    # ★ 2026-11-06：日历靠「点输入框右端图标」打开（旧的点中心/点 wrapper 无效）
    if not _open_date_picker(pg):
        print("[日期] 点日历图标后仍未弹出（格子数=%d）" % _count_date_cells(pg))
        return False

    # ★ 2026-11-06：格子改成「坐标数组」，不再用 locator（_Locator 没有 filter/nth 语义）。
    start_day = day - timedelta(days=7)
    # ★ 跨月场景：先翻页让 Start/End 两月**同屏**，再点格子
    #   （2026-11-07 实测：10-30 与 11-06 分属左右面板时，区间被展开成 10-29~12-06）
    _ensure_months_visible(pg, start_day, day)
    cells = _date_day_cells(pg)
    if not cells:
        print("[日期] 日历里没读到「日」格子（%d 个）" % len(cells))
        return False
    panels = _calendar_panels(pg)
    p_start = _pick_day_cell(cells, start_day, log=print, panels=panels)
    p_end = _pick_day_cell(cells, day, log=print, panels=panels)
    if not p_start or not p_end:
        print("[日期] 日历里找不到 %s 或 %s（跨月格子缺失）"
              % (start_day, day))
        return False

    # ① 点 Start（day-7）→ ② 点 End（day）。两次点击之间等一拍，
    #    让 rangePicker 把第一次点击识别成「起点」再收「终点」。
    #    ⚠ 必须先点**较早**的日子（Start），再点较晚的（End）；
    #      反了平台会把两次点击当成两次单日选择。
    for p, tag in ((p_start, "Start"), (p_end, "End")):
        pg.dispatch_mouse(p["x"], p["y"])
        pg.wait_for_timeout(800)
        print("[日期]   %s 已点 (%d,%d)" % (tag, p["x"], p["y"]))

    # ★ 2026-11-07 回读闸门：点完必须确认输入框真是「Start ~ End」。
    #   实测踩过两次错位（10-29~12-06、11-06~11-30）都是坐标点到邻月格子。
    got = _read_date_input(pg)
    want = "%s ~ %s" % (start_day.strftime("%Y-%m-%d"), day.strftime("%Y-%m-%d"))
    print("[日期] 回读区间：%r（期望 %r）" % (got, want))
    if got.strip() != want:
        print("[日期] [FAIL] 区间不对，拒绝导出（日历格子点错月/溢出格）")
        return False

    # ★ 2026-11-07 实锤：这个日历**没有「确定」按钮**（实测 query 到 0 个）。
    #   区间在第二次点击时就已经写进输入框了，只剩「怎么收起面板」：
    #   ⚠ 点日历外的空白处会**清空已选值**（实测变成 ''）—— 那条路不能走。
    #   ⚠ 按 Escape 也会让输入框显示为空（实测），但**平台内部的值还在**：
    #     收起后重新打开面板，输入框会恢复成刚才选的区间（下面就是靠这个复核）。
    try:
        pg.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass
    pg.wait_for_timeout(800)
    shown = _read_date_input(pg)
    if shown.strip() == want:
        print("[日期] ✅ 区间已确认：%s" % shown)
        return True
    # 收起后显示为空 ⇒ 重新打开面板回读真实值
    print("[日期] 收起后输入框显示 %r，重开面板复核…" % shown)
    if not _open_date_picker(pg):
        print("[日期] [FAIL] 重开面板失败，无法确认区间")
        return False
    again = _read_date_input(pg)
    print("[日期] 重开后输入框：%r" % again)
    try:
        pg.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass
    pg.wait_for_timeout(500)
    if again.strip() != want:
        print("[日期] [FAIL] 区间不对（%r / 期望 %r），拒绝导出" % (again, want))
        return False
    print("[日期] ✅ 区间已确认：%s" % again)
    return True


def _select_dropdown(pg, chip_text, item_text: str = None) -> bool:
    """点开当前值为 chip_text 的下拉（订单类型/订单状态），选「全部/全选」项。

    FIX 2026-09-08（此后 09-04~09-07 持续「订单状态=待发货」漏单的根因）：
    「订单状态」的当前值待发货是放在 **`input.IPT_input_123`（value=待发货）** 里，其外层
    `div.ST_selectValueSingle_123` / `div.ST_head_123` inner_text **为空** → 旧逻辑按外壳
    inner_text==chip_text 匹配，命中不了 → 下拉打不开 → 状态卡默认「待发货」。

    修复：优先点 **value==chip_text 的 `input.IPT_input_123`**（React 受控输入，点即开下拉）。
    订单类型（卖家履约订单）外壳 inner_text 有值仍走外壳；两种入口兼容。
    下拉选项为 `li.cIL_item_123`（旧 [class*=dropdown] [class*=item] 已随改版失效）。
    无 item_text 时优先选「全部/全选」，否则首项。
    """
    opened = False
    # FIX: 优先按 input value 定位下拉（订单状态值在 input value，不在外壳 text）
    inputs = pg.locator("input.IPT_input_123")
    for i in range(min(inputs.count(), 12)):
        try:
            if inputs.nth(i).input_value().strip() == chip_text and inputs.nth(i).is_visible():
                inputs.nth(i).click(timeout=6000)
                opened = True
                break
        except Exception:
            continue
    if not opened:
        for shell_sel in ["div.ST_selectValueSingle_123", "div.ST_head_123"]:
            shells = pg.locator(shell_sel)
            for i in range(min(shells.count(), 12)):
                try:
                    txt = shells.nth(i).inner_text().strip()
                    if txt == chip_text and shells.nth(i).is_visible():
                        shells.nth(i).click(timeout=6000)
                        opened = True
                        break
                except Exception:
                    continue
            if opened:
                break
    if not opened:
        # 兜底：get_by_text 精确（仅当外壳找不到；可能命中镜像 IPT_mirror_123）
        v = pg.get_by_text(chip_text, exact=True)
        for i in range(min(v.count(), 8)):
            try:
                if v.nth(i).is_visible() and "IPT_mirror" not in (v.nth(i).evaluate("e=>String(e.className)") or ""):
                    v.nth(i).click(timeout=6000)
                    opened = True
                    break
            except Exception:
                continue
    if not opened:
        return False
    pg.wait_for_timeout(1200)   # 留足下拉面板渲染时间
    items = pg.locator(_DROPDOWN_ITEM)
    # 选定目标项：有 item_text 精确匹配；无则优先「全部/全选」，否则兜底首项。
    if item_text:
        for j in range(min(items.count(), 12)):
            try:
                cand = items.nth(j)
                if cand.is_visible() and cand.inner_text().strip() == item_text:
                    cand.click(timeout=6000)
                    return True
            except Exception:
                continue
    else:
        for wants in ("全部", "全选", "全選"):
            for j in range(min(items.count(), 12)):
                try:
                    cand = items.nth(j)
                    if cand.is_visible() and cand.inner_text().strip() == wants:
                        cand.click(timeout=6000)
                        return True
                except Exception:
                    continue
    # 兜底：首项
    for j in range(min(items.count(), 12)):
        try:
            if items.nth(j).is_visible():
                items.nth(j).click(timeout=6000)
                return True
        except Exception:
            continue
    return False


def _click_cancelled_filter(pg) -> bool:
    """「已取消的商品」单选组选「过滤」（2026-11-07 用户口径）。

    页面结构（2026-11-07 实测）：label 包着 radio 圆点 + 文本「不过滤」/「过滤」，
    默认选中「不过滤」。点**文字所在的可点元素**即可切换；点击后回读
    label 的 data-checked / 圆点 class 确认选中态。
    """
    ok = pg.evaluate(r"""() => {
      const norm = s => (s || '').replace(/\s+/g, '');
      // 找「已取消的商品」所在容器，再在它后面找「过滤」那个 label
      const all = [...document.querySelectorAll('label,div,span')].filter(e => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      });
      const mark = all.find(e => e.children.length === 0
                                && norm(e.innerText) === '已取消的商品');
      if (!mark) return 'no-mark';
      let box = mark.closest('label') || mark.parentElement;
      for (let hop = 0; hop < 4 && box; hop++) {
        const labels = [...(box.parentElement || document).querySelectorAll('label')]
          .filter(l => /不过滤|过滤/.test(norm(l.innerText)));
        const want = labels.find(l => norm(l.innerText).endsWith('过滤')
                                     && !norm(l.innerText).includes('不过滤'))
                     || labels.find(l => norm(l.innerText) === '过滤');
        if (want) {
          const r = want.getBoundingClientRect();
          want.scrollIntoView({block: 'center'});
          want.click();
          return JSON.stringify({x: Math.round(r.x), y: Math.round(r.y),
                                 text: norm(want.innerText)});
        }
        box = box.parentElement;
      }
      return 'no-label';
    }""")
    print("[已取消] 点「过滤」：%s" % str(ok)[:120])
    return str(ok).startswith("{")


def _record_table(pg):
    return pg.locator("tbody tr")


def _wait_record_done(pg, before: int, timeout: int = 90, want_span: str = "") -> int:
    """轮询「导出记录」，等本次新增任务 状态=已完成；返回该行索引；超时返回 -1。

    ★ 2026-11-07 修（BEL 实跑踩到）：这个列表**分页**，每页固定 20 行。
      旧逻辑「行数增加 ⇒ 新增在顶部」在记录满一页后完全不成立
      （实测 before=32，点完变成 20 行 —— 32 是 locator count 的跨页总量，
      渲染出来只有 20 行，`n > before` 永远为假，于是一直等到超时）。
      现在改成：读**顶部第一行**的（下单时间 + 状态 + 日期区间），只要时间戳
      比点按钮前新就认定是本次任务；再等它变「已完成」。
      判据用时间戳而不是行数 —— 行数受分页影响，时间戳不受。

    `want_span`：期望的日期区间（如 `2026-10-30`）。给了就**必须**在记录行的
      区间里出现，否则返回 -1 —— 这是「日期选错不许当成功」的最后一道闸。
    """
    # 记下点按钮前的顶部第一行时间（"2026-11-07 15:52:05"）
    def top_sig():
        try:
            raw = pg.evaluate(r"""() => {
              const tr = document.querySelector('tbody tr');
              if (!tr) return '';
              const c = [...tr.children].map(e =>
                (e.innerText||'').replace(/\s+/g,' ').trim());
              return [c[1] || '', c[2] || '', c[4] || ''].join('|');
            }""")
            return str(raw or "")
        except Exception:  # noqa: BLE001
            return ""

    before_sig = top_sig() if before >= 0 else ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pg.wait_for_timeout(1500)
        sig = top_sig()
        if not sig:
            continue
        parts = (sig.split("|") + ["", "", ""])[:3]
        span_top, t_top, stat_top = parts
        t_before = (before_sig.split("|") + ["", ""])[1] if before_sig else ""
        # 本次任务 = 顶部时间变了（新的）或状态是进行态
        fresh = (not t_before) or (t_top != t_before) or ("完成" not in stat_top)
        if fresh and "完成" in stat_top:
            if want_span and want_span not in span_top:
                print("[导出] ⚠ 记录区间 %r 不含期望起点 %s" % (span_top, want_span))
                return -1
            return 0
    return -1


def _download_record(pg, row_idx: int, csv_path: str) -> str:
    """点该记录行「下载」→ 落盘到 csv_path。返回 OK/FAIL。

    ★ 2026-11-07 修：不再用 `pg.expect_download` —— 连接走 `cdp_transport` 后
      没有这个接口（2026-11-06 起每条导出都卡在这里），而且紫鸟窗口里 CDP
      改下载目录**不生效**。改成与 `temu_download_export.py` 同一套：
      真实坐标点「下载」→ 扫紫鸟默认下载目录里 mtime 变新的那个文件 → 复制过来。
    """
    rows = _record_table(pg)
    box = rows.nth(row_idx).evaluate(r"""tr => {
      const tds = [...tr.children];
      const td = tds[5] || tds[tds.length - 1];
      const a = [...td.querySelectorAll('a,span,button,div')]
        .filter(e => e.children.length === 0
                  && (e.innerText||'').replace(/\s+/g,'') === '下载')
        .pop();
      if (!a) return '{}';
      a.scrollIntoView({block: 'center'});
      const r = a.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2),
                             y: Math.round(r.y + r.height/2)});
    }""")
    try:
        b = json.loads(str(box) or "{}")
    except Exception:  # noqa: BLE001
        b = {}
    if not b.get("x"):
        return "FAIL: 未找到下载按钮"

    # 下载目录快照（紫鸟默认下载目录）
    import glob
    win_dir = os.path.join(os.environ.get("APPDATA", ""), "ziniaobrowserdatas",
                           "ziniao browser")
    cand_dirs = [d for d in glob.glob(os.path.join(win_dir, "*")) if os.path.isdir(d)]
    before = {}
    for d in cand_dirs:
        for f in glob.glob(os.path.join(d, "*.csv")) + glob.glob(os.path.join(d, "*.xlsx")):
            try:
                before[f] = os.path.getmtime(f)
            except OSError:
                pass

    pg.dispatch_mouse(b["x"], b["y"])
    newest = ""
    for _ in range(45):                     # 最多等 90s
        pg.wait_for_timeout(2000)
        found = []
        for d in cand_dirs:
            for f in (glob.glob(os.path.join(d, "*.csv"))
                      + glob.glob(os.path.join(d, "*.xlsx"))):
                try:
                    mt = os.path.getmtime(f)
                except OSError:
                    continue
                if before.get(f) != mt and not os.path.basename(f).startswith("~$"):
                    found.append((mt, f))
        if found:
            newest = max(found)[1]
            break
    if not newest:
        return "FAIL: 下载目录没有新文件（紫鸟默认下载目录）"
    print("[下载] 新文件：%s" % os.path.basename(newest))
    try:
        if newest.lower().endswith(".csv"):
            shutil.copy2(newest, csv_path)
        else:
            shutil.copy2(newest, csv_path)
    except Exception as e:  # noqa: BLE001
        return "FAIL: 复制下载文件失败 %s" % str(e)[:120]
    return "OK"


def _csv_to_xlsx(csv_path: str, xlsx_path: str) -> tuple[bool, int]:
    """CSV（UTF-8 BOM）→ xlsx（openpyxl）。返回 (是否成功, 数据行数)。"""
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        if not rows:
            return False, 0
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        for r in rows:
            ws.append(r)
        wb.save(xlsx_path)
        return True, max(0, len(rows) - 1)
    except Exception:
        return False, 0


def export_daily_sales(port: int, day: datetime.date, out_dir: str,
                       base_name: str = None) -> str:
    """导出 TEMU 指定日期订单（新界面 export-order.html），落盘 out_dir/<base>.xlsx。

    返回: OK saved=<路径> (<字节>) / FAIL/ERR 说明。
    """
    out_dir = out_dir or os.path.join(os.getcwd(), "每日导出销量")
    os.makedirs(out_dir, exist_ok=True)
    fname = (base_name or ("销量-" + day.strftime("%Y-%m-%d"))) + ".xlsx"
    dst = os.path.join(out_dir, fname)
    tmp_csv = os.path.join(out_dir, fname.replace(".xlsx", "") + "_tmp.csv")

    p, b, pg = _connect(port)
    if not pg:
        return "FAIL: 未找到 TEMU 页面"
    own_tid = ""
    try:
        # ★ 2026-10-28 用户口径「自己新开页，跑完即关」：
        #   一律**自己开**导出页，绝不借用窗口里已有的导出页 ——
        #   `_pick_page` 挑的是"最像导出页"的那张，很可能正是用户开着的；
        #   旧代码还会在"已经有导出页"时直接用它，于是筛选/滚动都被改掉。
        #   自己那页由 finally 里的 close_target 关掉。
        pg, own_tid = _open_own_tab(b, _EXPORT_URL, port)
        try:
            pg.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:  # noqa: BLE001
            pass
        pg.wait_for_timeout(3000)
        # ⚠ 弹窗处理只在自己那页上做（原来在外面先对 `_pick_page` 拿到的页做，
        #   那可能正是用户的页 —— 按 Esc / 点取消都算动了别人的页面）。
        _close_modal(pg)
        if _url_is_auth_page(pg.url):
            return "FAIL: 停在登录/认证页，需先授权（run_temu_auth.py --store <店>）"
        if not _url_is_export_page(pg.url):
            # 导航 mmsos 被弹回卖家中心首页 = 店铺视图不对（全托管/半托管 local）；
            # 明确报出来，别让它到后面伪装成「日期选择器未生效」（2026-09-16 KinglamanSport 实测）。
            try:
                _path = (_url_parts(pg.url).path or "/")
            except Exception:
                _path = "/"
            if _path in ("", "/"):
                return ("FAIL: 导航导出页被弹回卖家中心首页（当前页=%s）：多为店铺视图不对"
                        "（需切到含「订单导出」的店铺视图，见 agents/每日导出销量-agent.md）" % pg.url[:110])

        # 3) 订单日期
        if not _set_export_date(pg, day):
            return "FAIL: 日期选择器未生效（找不到日历格子）"
        pg.wait_for_timeout(600)

        # 4) 订单类型=全部、订单状态=全部
        _select_dropdown(pg, _ORDER_TYPE_CHIP)
        pg.wait_for_timeout(400)
        try:
            pg.keyboard.press("Escape")
        except Exception:
            pass
        pg.wait_for_timeout(400)
        _select_dropdown(pg, _ORDER_STATUS_CHIP)
        pg.wait_for_timeout(400)
        try:
            pg.keyboard.press("Escape")
        except Exception:
            pass
        pg.wait_for_timeout(400)
        # ★ 2026-11-07 用户口径：「过滤已取消订单」——「已取消的商品」选「过滤」。
        #   这是单选组（不过滤 / 过滤），默认「不过滤」；选「过滤」后导出里
        #   就不再带「已取消」的单（BEL 今天 74 行里有 5 行是已取消）。
        #   ⚠ 它不是下拉，是 radio chip：直接按文本点「过滤」，点完不用收面板。
        try:
            _click_cancelled_filter(pg)
        except Exception as e:  # noqa: BLE001
            print("[已取消] 点「过滤」失败（不影响其余筛选）：%s" % str(e)[:120])
        pg.wait_for_timeout(600)

        # ★ 2026-11-07：不再在点「确认导出」前查输入框 ——
        #   收起日历后这个输入框**永远显示空**（实测），而平台内部的值是有的
        #   （导出记录里能证明）。改成点完导出后**按记录行的日期区间**核对，
        #   对不上就报错，不把错的当成功（见下面 `_wait_record_done`）。

        # 5) 确认导出
        # ★ 2026-11-07：改**真实坐标点击**——`get_by_text` 会命中隐藏的同名节点，
        #   点了没反应却返回 True（BEL 实测连续两次没有新记录）。
        before = -1
        box = pg.evaluate(r"""() => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0; };
          const el = [...document.querySelectorAll('button,div,span')].filter(vis)
            .find(e => e.children.length === 0
                    && (e.innerText||'').replace(/\s+/g,'') === '确认导出');
          if (!el) return '{}';
          const btn = el.closest('button') || el;
          btn.scrollIntoView({block: 'center'});
          const r = btn.getBoundingClientRect();
          return JSON.stringify({x: Math.round(r.x + r.width/2),
                                 y: Math.round(r.y + r.height/2),
                                 disabled: !!btn.disabled});
        }""")
        try:
            bx = json.loads(str(box) or "{}")
        except Exception:  # noqa: BLE001
            bx = {}
        if not bx.get("x"):
            return "FAIL: 未找到确认导出按钮"
        if bx.get("disabled"):
            return "FAIL: 确认导出按钮 disabled（筛选条件不完整）"
        pg.dispatch_mouse(bx["x"], bx["y"])
        print("[导出] 已点「确认导出」@(%d,%d)" % (bx["x"], bx["y"]))
        pg.wait_for_timeout(1500)

        # 6) 轮询导出记录 已完成（按顶部行时间戳判，不受分页影响）
        # ★ 2026-11-07 实锤：记录里的区间起点会比我们点的日子**早一天**
        #   （我们点 10-30，记录写 10-29 00:00:00 ~ 11-06 23:59:59 UTC+8）——
        #   平台内部按时区换算存，属正常。所以这里只核对**结束日**（＝我们点的那天），
        #   起点不参与判定，否则永远判成错。
        row_idx = _wait_record_done(pg, before, timeout=120,
                                    want_span=day.strftime("%Y-%m-%d"))
        if row_idx < 0:
            return "FAIL: 等待导出记录完成超时（或记录不含 %s）" % day.strftime("%Y-%m-%d")

        # 7) 下载 → CSV → xlsx
        res = _download_record(pg, row_idx, tmp_csv)
        if not res.startswith("OK"):
            return res
        ok, rows = _csv_to_xlsx(tmp_csv, dst)
        try:
            os.remove(tmp_csv)
        except Exception:
            pass
        if not ok:
            return "FAIL: CSV 转 xlsx 失败"
        sz = os.path.getsize(dst)
        return "OK saved=%s (%d bytes, %d rows)" % (dst, sz, rows)
    except Exception as e:
        return "ERR: %s" % str(e)[:200]
    finally:
        # 跑完即关：把自己开的那一页关掉（`b.close()` 只是 detach，关不了页）。
        if own_tid:
            try:
                from cdp_transport import close_target  # noqa: PLC0415
                if close_target(port, own_tid):
                    print("[页] 已关自己的导出页 %s" % own_tid[:12])
                else:
                    print("[页] ⚠ 导出页没关掉（%s），下次运行会由 sweep 清掉" % own_tid[:12])
            except Exception:  # noqa: BLE001
                pass
        try:
            b.close()
        except Exception:
            pass
        # ★ 2026-11-06：改用 cdp_transport 后不再有 playwright 实例，`p` 是 None。
        #   旧代码无条件 `p.stop()` 会 AttributeError（在 finally 里，会盖掉真实结果）。
        try:
            if p is not None:
                p.stop()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    day_s = sys.argv[2] if len(sys.argv) > 2 else None
    out = sys.argv[3] if len(sys.argv) > 3 else None
    day = datetime.strptime(day_s, "%Y-%m-%d").date() if day_s else (datetime.now(timezone.utc) - timedelta(days=1)).date()
    print(export_daily_sales(port, day, out))
