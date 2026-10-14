# -*- coding: utf-8 -*-
"""lib/temu_sales_driver.py — TEMU 订单导出 driver（适配新导出界面 export-order.html）。

流程（2026-09-02 实测，界面已改版）：
    1. 连紫鸟窗口 CDP（playwright connect_over_cdp，不 spawn）
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


def _connect(port: int):
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=15000)
    pg = _pick_page(b)
    if pg is None:
        for ctx in b.contexts:
            for page in ctx.pages:
                pg = page
                break
            if pg:
                break
    return p, b, pg


def _month_header_text(day) -> str:
    """目标月月头文本（如 2026年 9月）。"""
    return "%d年 %d月" % (day.year, day.month)


def _set_export_date(pg, day) -> bool:
    """设「订单日期」= day（单日）。点开日历 → 点目标月面板的目标日格子两次 → 确定。

    double 到同一天以构成 Start=End=目标日。取「目标月面板」里那一天，勿取相邻月。
    """
    inp = _cnt(pg, _DATE_INPUT_SELS)
    if inp is None:
        return False
    try:
        w = _cnt(pg, _DATE_INPUT_WRAP)
        (w if w is not None else inp).first.click(timeout=6000)
    except Exception:
        try:
            inp.first.click(timeout=6000)
        except Exception:
            return False
    pg.wait_for_timeout(1300)

    day_cells = _cnt(pg, _DATE_CELL_SELS)
    if day_cells is None:
        return False

    # 目标月头 bounding box（用于圈定该月面板）
    target_month_box = None
    for t in ["%d年 %d月" % (day.year, day.month), "%d年%d月" % (day.year, day.month)]:
        try:
            hl = pg.get_by_text(t)
            for i in range(min(hl.count(), 6)):
                try:
                    if hl.nth(i).is_visible():
                        target_month_box = hl.nth(i).bounding_box()
                        break
                except Exception:
                    continue
            if target_month_box:
                break
        except Exception:
            continue

    def _in_panel(box) -> bool:
        if box is None or target_month_box is None:
            return True  # 找不到月头就退回：点 day 对应格子（当前月的第一个）
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        return (target_month_box["x"] - 20 <= cx <= target_month_box["x"] + 320
                and target_month_box["y"] - 30 <= cy <= target_month_box["y"] + 260)

    # 收集匹配目标日 + 落在目标月面板的格子
    n = day_cells.count()
    matches = []
    for i in range(n):
        try:
            txt = day_cells.nth(i).inner_text().strip()
            if txt == str(day.day):
                box = day_cells.nth(i).bounding_box()
                if box and _in_panel(box):
                    matches.append(i)
        except Exception:
            continue
    # 优先取目标月面板内的；若无（月头框探不到），回退取「当月」那个（跳过相邻月：取中线处）
    if not matches:
        for i in range(n):
            try:
                if day_cells.nth(i).inner_text().strip() == str(day.day):
                    matches.append(i)
            except Exception:
                continue
        if len(matches) > 2:
            matches = [matches[len(matches) // 2]]  # 保守取中间（当前月）那条

    if not matches:
        return False
    target = matches[0] if len(matches) == 1 else matches[1] if len(matches) > 1 else matches[0]
    if len(matches) >= 2:
        target = matches[0]
    for _ in range(2):
        try:
            day_cells.nth(target).click(timeout=5000)
        except Exception:
            try:
                day_cells.nth(target).evaluate("el => el.click()")
            except Exception:
                return False
        pg.wait_for_timeout(500)

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
    from playwright.sync_api import sync_playwright  # noqa: F401 (ensure import path)
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
    try:
        _close_modal(pg)
        # 打开导出页。必须用 host+path 判（`_url_is_export_page`）：授权残留的死 tab
        # （`seller.kuajingmaihuo.com/settle/seller-login?redirectUrl=...%2Fmmsos%2Fexport-order.html`）
        # 的 URL query 里就带着 `export-order.html`，旧的「整串包含」写法会被它骗过 →
        # 既判定为"已在导出页"（于是不导航），又命中登录页特征 → 直接误报"需先授权"（2026-09-16 三家实测）。
        if not _url_is_export_page(pg.url):
            pg.goto(_EXPORT_URL, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(3000)
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
        try:
            b.close()
        except Exception:
            pass
        p.stop()


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    day_s = sys.argv[2] if len(sys.argv) > 2 else None
    out = sys.argv[3] if len(sys.argv) > 3 else None
    day = datetime.strptime(day_s, "%Y-%m-%d").date() if day_s else (datetime.now(timezone.utc) - timedelta(days=1)).date()
    print(export_daily_sales(port, day, out))
