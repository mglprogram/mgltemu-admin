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
    """日历里的「日」格子坐标（**不返回 locator**，直接给 `[{day,x,y}]`）。

    ★ 2026-11-06：`div.RPR_cell_123` / `td.RPR_tdDay_123` 改版后**已失效**
      （实测日历格子的 class 是空的 `<div>28</div>`）；
      而 `cdp_transport` 的 `_Locator` 是 Playwright 的最小壳、**没有 `.filter()`**
      ⇒ 用 `locator(...).filter(has_text=...)` 会抛
      `'_Locator' object has no attribute 'filter'`。
      故直接回坐标数组，调用方按 `day` 选一个再 `dispatch_mouse`。
    """
    try:
        raw = pg.evaluate(r"""() => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
          return JSON.stringify([...document.querySelectorAll('div,td,span')]
            .filter(vis)
            .filter(e => e.children.length === 0 && /^\d{1,2}$/.test(txt(e))
                      && +txt(e) >= 1 && +txt(e) <= 31)
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


def _set_export_date(pg, day) -> bool:
    """设「订单日期」= day（单日）。点开日历 → 点目标月面板的目标日格子两次 → 确定。

    double 到同一天以构成 Start=End=目标日。取「目标月面板」里那一天，勿取相邻月。
    """
    inp = _cnt(pg, _DATE_INPUT_SELS)
    if inp is None:
        return False
    # ★ 2026-11-06：日历靠「点输入框右端图标」打开（旧的点中心/点 wrapper 无效）
    if not _open_date_picker(pg):
        print("[日期] 点日历图标后仍未弹出（格子数=%d）" % _count_date_cells(pg))
        return False

    # ★ 2026-11-06：格子改成「坐标数组」，不再用 locator（_Locator 没有 filter/nth 语义）。
    cells = _date_day_cells(pg)
    if not cells:
        print("[日期] 日历里没读到「日」格子（%d 个）" % len(cells))
        return False

    # 目标月面板：日历是「左=上月 / 右=本月或下月」，点 **最靠右** 那组里的目标日，
    # 即"最后一组"—— 目标月通常是右面板。用 x 坐标聚类分组：同一天的多个格子
    # （上月/本月/下月各有一个相同的数字）里取 x 最大者。
    same = [c for c in cells if c["day"] == day.day]
    if not same:
        print("[日期] 日历里没有 %d 号" % day.day)
        return False
    # 同一天在相邻月面板也会出现；取 x 最大的那个（最右面板＝目标月）
    pick = max(same, key=lambda c: c["x"])
    print("[日期] 点 %d 号 @(%d,%d)（同数字 %d 个，取最右）"
          % (day.day, pick["x"], pick["y"], len(same)))
    for _ in range(2):          # 点两次 ⇒ Start=End=目标日
        pg.dispatch_mouse(pick["x"], pick["y"])
        pg.wait_for_timeout(600)

    _click_text(pg, _CONFIRM_BTN, timeout=5000, js=True)
    pg.wait_for_timeout(1000)
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


def _record_table(pg):
    return pg.locator("tbody tr")


def _wait_record_done(pg, before: int, timeout: int = 90) -> int:
    """轮询「导出记录」，等新增任务 状态=已完成；返回该行索引；超时返回 -1。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pg.wait_for_timeout(1500)
        rows = _record_table(pg)
        n = rows.count()
        # 导出记录列表为跨天累积历史（时间倒序，最新在顶部 idx0）。本次新增记录在顶部
        # （索引 0..n-before-1）。只认新增行里顶部那条「完成」（勿 reversed 扫全表，
        # 否则会取到最旧的已完成记录=旧日期数据）。行数未增时也查顶部 idx0。
        if n > before:
            new_span = n - before
            for idx in range(new_span):
                try:
                    cells = rows.nth(idx).locator("td")
                    if "完成" in (cells.nth(4).inner_text() if cells.count() > 4 else ""):
                        return idx
                except Exception:
                    continue
        else:
            # 行数未增（可能新记录覆盖旧记录）：看顶部 idx0 是否为「完成」
            try:
                cells = rows.nth(0).locator("td")
                if "完成" in (cells.nth(4).inner_text() if cells.count() > 4 else ""):
                    return 0
            except Exception:
                pass
    return -1


def _download_record(pg, row_idx: int, csv_path: str) -> str:
    """点该记录行「下载」→ expect_download 拦截保存到 csv_path。返回 OK/FAIL。"""
    rows = _record_table(pg)
    target = rows.nth(row_idx)
    dlbtn = target.get_by_text("下载", exact=True)
    for i in range(min(dlbtn.count(), 3)):
        try:
            if dlbtn.nth(i).is_visible():
                break
        except Exception:
            continue
    # 2026-11-06：不再需要 playwright（连接改走 cdp_transport）
    with pg.expect_download(timeout=120000) as dl_info:
        clicked = False
        for i in range(min(dlbtn.count(), 3)):
            try:
                if dlbtn.nth(i).is_visible():
                    dlbtn.nth(i).click(timeout=6000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            return "FAIL: 未找到下载按钮"
    dl = dl_info.value
    failure = dl.failure()
    if failure:
        return "FAIL: 下载失败 %s" % failure
    dl.save_as(csv_path)
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
        pg.wait_for_timeout(600)

        # 5) 确认导出
        before = _record_table(pg).count()
        if not _click_text(pg, [_EXPORT_CONFIRM_BTN], timeout=6000, js=True):
            return "FAIL: 未找到确认导出按钮"
        pg.wait_for_timeout(1000)

        # 6) 轮询导出记录 已完成
        row_idx = _wait_record_done(pg, before, timeout=90)
        if row_idx < 0:
            return "FAIL: 等待导出记录完成超时"

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
