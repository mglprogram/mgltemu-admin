# -*- coding: utf-8 -*-
"""从「销量分析」页抓取每个 SPU / SKC 的销量，作为动态重点 SPU 名单 + 大屏销量列。

页面：https://agentseller.temu.com/main/data-center/goods-data
      （时间口径：今日 / 近7日 / 近30日 / 自定义）

流程（每店）：
  1. 找一个已登录的卖家页 tab，导航到销量分析页；若被重定向到认证页则走授权
  2. 点「近7日」→ 逐页抓（SKU 级）→ 校验翻页真的换了数据 → 取 skuSales >= 1 的行
  3. **再点「今日」抓一遍**（2026-10-14 加：大屏「今日销量」列要用），抓不到不影响第 2 步
  4. SKU货号 → SPU / SKC：查本地商品库 data/<store>/<store>_products.db
     （查不到的记入 unmatched，多为快照之后新建的款）

产物：data/focus/<store>_focus.json
  { store, window: "近7日", windows: {sales7, today, todayAt}, generatedAt,
    spus: [{spuId, sales7, todaySales, title}],      ← watch_focus.py 只用 spuId
    skcs: [{skcCode, sales7, todaySales}],           ← goods.json 按 SKC 回填
    unmatched: [...] }

只读：只点时间胶囊与分页，不点「立即加库存」「批量加库存」等写操作。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(r"D:\AI-workplace\TEMU")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))   # cdp_transport（临时标签页）
import flux_auth  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SALES_URL = "https://agentseller.temu.com/main/data-center/goods-data"
STORES = {
    "BEL": {"win": "TM-BEL-MGL", "db": ROOT / "data/bel/bel_products.db", "label": "BEL 店"},
    "BHY": {"win": "TM-BHY-YJF", "db": ROOT / "data/bhy/bhy_products.db", "label": "BHY 店"},
}
WINDOW = "近7日"
TODAY = "今日"


def log(m: str) -> None:
    print(m, flush=True)


def js(ws: str, expr: str):
    """求值。expr 必须是**裸箭头函数**（本模块的 js 会自动调用它）。"""
    return flux_auth.js(ws, expr)


def run_js_file(ws: str, path: Path, **vars) -> dict:
    """在同一条 CDP 连接上：先写入 window 变量，再执行脚本文件（原样，不额外包裹）。

    flux_auth.js() 会把参数包成 `(expr)()`，对「已调用的 IIFE」脚本文件会双重调用而失败，
    所以这里自己走一次 Runtime.evaluate。
    """
    from flux_auth import _run_ws  # noqa: PLC0415
    calls = []
    for k, v in vars.items():
        calls.append(("Runtime.evaluate", {
            "expression": "window.%s = %s" % (k, json.dumps(v, ensure_ascii=False)),
            "returnByValue": True}))
    src = path.read_text(encoding="utf-8").strip()
    calls.append(("Runtime.evaluate", {
        "expression": src, "returnByValue": True, "awaitPromise": True, "userGesture": True}))
    results = _run_ws(ws, calls, timeout=30)
    res = (results[-1].get("result") or {}).get("result") or {}
    v = res.get("value")
    return json.loads(v) if isinstance(v, str) else (v or {})


# ---------------------------------------------------------------- 页面准备
def _close_target(port: int, tid: str) -> None:
    """关掉自己开的临时标签页（异常路径也别留）。

    走 `cdp_transport.close_target`：它顺手把**登记表**里那条删掉（登记表用来扛强杀），
    幂等且永不抛。
    """
    if not tid:
        return
    try:
        import cdp_transport as cdp  # noqa: PLC0415
        cdp.close_target(port, tid)
    except Exception:  # noqa: BLE001
        pass


def prepare(store: str, port: int) -> tuple[str, str, str]:
    """**自己开一个后台标签页**抓数，不碰用户正在用的那个页。

    为什么改（2026-10-14）：用户窗口里那个 goods-data 标签页对 `Runtime.evaluate`
    完全不响应（20 秒超时、无错误）—— 原生对话框/冻结都可能这样，脚本会卡死。
    新开临时页既能绕开，也符合「不动用户页面」的一贯做法（预售读取层同款）。

    返回 (websocket 或 "", targetId, 说明)。掉登录会走一次授权。
    """
    import cdp_transport as cdp  # noqa: PLC0415

    tid = cdp.create_target(port, SALES_URL)
    if not tid:
        return "", "", "开不出临时标签页"
    deadline = time.time() + 50
    authed = False
    while time.time() < deadline:
        time.sleep(2)
        t = flux_auth.find_tab_by_id(port, tid)
        if not t:
            continue
        u = t.get("url") or ""
        if ("auth" in u or "login" in u) and not authed:
            log("  [auth] 临时页被重定向到认证页，走授权…")
            ok, msg = flux_auth.ensure_logged_in(port, store, timeout=240)
            log("  [auth] " + ("OK " if ok else "FAIL ") + msg)
            if not ok:
                return "", tid, msg
            authed = True
            try:
                flux_auth.navigate(t["webSocketDebuggerUrl"], SALES_URL)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(8)
            continue
        try:
            n = js(t["webSocketDebuggerUrl"],
                   "() => document.querySelectorAll('tbody tr').length")
        except Exception:  # noqa: BLE001
            n = 0
        if n:
            return t["webSocketDebuggerUrl"], tid, "临时页已就绪（%d 行）" % n
    return "", tid, "临时页打开了但一直没出数据行"


# ---------------------------------------------------------------- 抓取
def _table_sig(ws: str) -> tuple:
    """表格指纹：(行数, SKC销量合计, SKU销量合计, 总条数, 首行货号)。

    只读一次 DOM，用来判断「时间口径真的换过来了 / 这一页真的翻过去了」。
    """
    d = run_js_file(ws, HERE / "extract_sales.js")
    rows = d.get("rows") or []
    first = (rows[0].get("skuCode") if rows else "") or ""
    return (len(rows),
            round(sum(float(r.get("skcSales") or 0) for r in rows), 2),
            round(sum(float(r.get("skuSales") or 0) for r in rows), 2),
            str(d.get("total") or ""),
            first)


def _wait_settled(ws: str, before: tuple | None = None, timeout: float = 90,
                  quiet: int = 3, must_differ: bool = True) -> bool:
    """等页面重算完：指纹连续 quiet 次不变（且已与 before 不同，若给了 before）。

    ★ 2026-10-30 修（用户：「面板的今天销量是不是抓错了」）。原来只 `sleep(8)`：
      BHY 窗口 14:00 那轮点「今日」后 8 秒表格还没重算，抓到的**仍是「近7日」**的值
      ⇒ 大屏「今日销量」列 = 近7日（10-105-3BHL 显示 129，真实今日只有 22），
      同时那一遍只渲染出 11 行（实际 157 行）⇒ 大量款 sales7 被记成 0。
      现在改成"看指纹变没变 + 连续几次不变才算稳定"，慢页面会自己多等。
    """
    started = time.time()
    last, same, changed = None, 0, not must_differ
    while time.time() - started < timeout:
        time.sleep(2)
        try:
            sig = _table_sig(ws)
        except Exception:  # noqa: BLE001
            continue
        if sig[0] <= 0 or not sig[4]:
            same, last = 0, sig        # 空表/加载中，不算稳定
            continue
        if before is not None and sig[:4] != tuple(before)[:4]:
            changed = True
        if sig == last:
            same += 1
        else:
            same, last = 0, sig
        if changed and same >= quiet:
            return True
    log("    [warn] 页面 %.0f 秒内没稳定下来（口径可能没换过来）" % (time.time() - started))
    return False


def scrape_period(ws: str, period: str, max_pages: int = 40,
                  before: tuple | None = None) -> tuple[list, str]:
    """点时间胶囊 + 逐页抓，返回 (rows, 错误说明)。rows 里的 skuSales/skcSales 就是该口径的值。

    ⚠ 必须翻完：页面每页 30 条、总数上百（2026-10-14 实测只翻 7 页会漏，
    表现为「今日SKC销量」合并格 26、逐行只累到 9）。
    ⚠ 每次切口径/翻页都要**等到真的变了**再读（见 _wait_settled）。
    """
    r = run_js_file(ws, HERE / "click_period.js", PERIOD=period)
    log("  时间口径 " + json.dumps(r, ensure_ascii=False))
    if not r.get("ok"):
        return [], "找不到「%s」时间胶囊" % period
    # 胶囊本来就在这个口径上（action=already）⇒ 不必等"数字变了"，只要等它别再动
    _wait_settled(ws, before=before, must_differ=(r.get("action") != "already"))

    all_rows, seen = [], set()
    total, pages, zero = None, max_pages, 0
    for pg in range(1, max_pages + 1):
        if pg > 1:
            if pages and pg > pages:
                break
            prev = _table_sig(ws)
            rr = run_js_file(ws, HERE / "click_page.js", PAGE=str(pg))
            if not rr.get("ok"):
                msg = str(rr.get("msg") or "")
                if "no page" not in msg:      # 页码用完了不是错误，正常收工
                    log(f"  翻页到第{pg}页失败：{msg}")
                break
            # 翻页也要等首行货号变了，否则读到的是上一页的旧表
            _wait_settled(ws, before=prev, timeout=45, quiet=2)
        data = run_js_file(ws, HERE / "extract_sales.js")
        if total is None and data.get("total"):
            total = int(data["total"])
            pages = min(max_pages, (total + 29) // 30 + 2)     # 每页 30 条，+2 页容错
            log(f"  页面共 {total} 条 → 约 {pages} 页")
        rows = [x for x in data.get("rows", [])
                if x.get("skuCode") and x["skuCode"] not in seen]
        new = len(rows)
        for x in rows:
            seen.add(x["skuCode"])
        all_rows += rows
        log(f"  第{pg}页: +{new}（累计 {len(all_rows)}）")
        if new == 0 and pg > 1:
            zero += 1
            if zero >= 2:
                break
            # 慢加载时这一页可能还没渲染出来 —— 再等一次，别急着收工
            time.sleep(5)
            again = run_js_file(ws, HERE / "extract_sales.js")
            rows = [x for x in again.get("rows", [])
                    if x.get("skuCode") and x["skuCode"] not in seen]
            if rows:
                zero = 0
                for x in rows:
                    seen.add(x["skuCode"])
                all_rows += rows
                log(f"  第{pg}页(补): +{len(rows)}（累计 {len(all_rows)}）")
        else:
            zero = 0
    return all_rows, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, choices=list(STORES))
    ap.add_argument("--out", default="")
    ap.add_argument("--no-today", action="store_true",
                    help="只抓近7日（不发第二次「今日」请求；老行为）")
    a = ap.parse_args()

    store = a.store
    st = STORES[store]
    port = flux_auth.find_store_port(st["win"])
    if not port:
        log("!! 未探测到窗口 %s 的 CDP 端口" % st["win"])
        return 2
    log(f"== {store} ({st['label']}) port={port}")

    # 开工先清障：上一次运行被强杀（计划任务超时 0x41306）时留下的临时页没人会关，
    # 只清**登记过**的（自己的页），用户的页永远不碰。见 cdp_transport.sweep_leaked_scratch。
    try:
        import cdp_transport as cdp  # noqa: PLC0415
        n = len(cdp.sweep_leaked_scratch(port, log=log) or [])
        if n:
            log(f"  清掉 {n} 个上次残留的临时页")
    except Exception as e:  # noqa: BLE001
        log(f"  [warn] 清理残留临时页失败（不阻断）：{str(e)[:60]}")

    # ★ 2026-10-28 加：**先给窗口里的页做体检**。
    #   实测（BEL 18:45 那轮）：窗口里有一个卡死的 orders.html，
    #   于是 `create_target` 建出来的新页也是死的，连 WebSocket 都握不上手
    #   （`server rejected WebSocket connection: HTTP 500`），整个 BEL 分支报
    #   「抓取失败（继续，另一家照常）」⇒ BEL 数据停在 14:07 的旧值。
    #   卡死页不清理，建多少新页都是死的。
    port = _heal_hung(port, st["win"])
    log(f"  [页] 端口 = {port}")

    ws, tid, note = prepare(store, port)
    log("  " + note)
    if not ws:
        _close_target(port, tid)
        return 3
    try:
        try:
            rc = scrape_all(store, st, ws, a)
        except Exception as e:  # noqa: BLE001
            # ★ 2026-10-30 补：卡死是**异常**（websockets.InvalidStatus HTTP 500）而不是返回码，
            #   原来会直接把整个脚本抛穿，连下面那次「换新页重抓」都跑不到（BEL 实测）。
            log("  [!] 抓取中断：%s: %s" % (type(e).__name__, str(e)[:90]))
            rc = 5
        if rc == 0:
            return 0
        # ★ 2026-10-28 加：**中途卡死自动重试一次**（换一张新页重抓）。
        #
        # 为什么需要（BEL 实测两次）：这个窗口的渲染进程会**在翻页途中**卡死 ——
        #   第 1~3 页正常，第 4 页开始 `WebSocket HTTP 500`。
        #   开工时那次体检救不了"跑到一半才死"，于是整个 BEL 分支白跑。
        #   卡死是**页面级**的：同一窗口新开的页仍然能用（实测重开后 8 页全过），
        #   所以正确做法是**关掉死页、换张新页从头再抓一次**，而不是重试同一张。
        log("  [!] 本次抓取未完成（rc=%s）→ 清理卡死页后重试一次" % rc)
        _close_target(port, tid)
        port = _heal_hung(port, st["win"])
        ws2, tid2, note2 = prepare(store, port)
        log("  [重试] " + note2)
        if not ws2:
            _close_target(port, tid2)
            return rc
        try:
            return scrape_all(store, st, ws2, a)
        except Exception as e:  # noqa: BLE001
            log("  [!] 重试也失败了：%s: %s" % (type(e).__name__, str(e)[:90]))
            return 6
        finally:
            _close_target(port, tid2)
    finally:
        pass


def _heal_hung(port: int, window: str) -> int:
    """清理**我们自己登记的**卡死页，必要时重探端口。返回可用的 port。

    ★ 2026-10-29 修（用户：「为什么覆盖我的页面，然后自己又开了一个新页」）：
      旧实现遍历窗口里**所有**页，判 hung 就 `recover_hung_page` 关掉 —— 包括用户的页。
      实测 BHY 窗口 5 个页里 3 个被判 hung：`goods/list`、一个 `www.temu.com` 商品页、
      一个销量分析页。全关掉后 `prepare()` 又新开一张销量分析页
      ⇒ 用户看到「我的页没了 + 凭空多出一个销量分析页」，正是他截图里的现场。

      ⇒ 现在只处理**登记表里有的**页（`create_target` 建页时落盘的 targetId）。
        用户的页（没登记过）永不关闭、永不判定为可清理，哪怕它真的卡了 ——
        它卡了是用户自己的事，我们只保证自己的页不拖累流程。
    """
    try:
        import cdp_transport as cdp  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log("  [warn] 无法加载 cdp_transport，跳过清理：%s" % str(e)[:60])
        return port
    mine = cdp.registered_targets(port)
    healed = 0
    try:
        for t in cdp.list_pages(port):
            tid = t.get("targetId") or t.get("id")
            if tid not in mine:
                continue                      # 不是我们开的 → 用户的页，连判都不判
            if cdp.probe_page_alive(port, t) != "hung":
                continue
            u = (t.get("url") or "")[:60]
            r = cdp.recover_hung_page(port, t, log=log)
            log(f"  [页] 自有卡死页（{u}）→ {r}")
            if r == "closed":
                healed += 1
    except Exception as e:  # noqa: BLE001
        log("  [warn] 清理卡死页失败：%s" % str(e)[:80])
        return port
    if healed:
        log(f"  [页] 已清理 {healed} 个自有卡死页，重新探测端口")
        return cdp.find_port_after_recovery(window) or port
    return port


def scrape_all(store: str, st: dict, ws: str, a) -> int:
    # 开抓前先让页面彻底静下来（刚打开时数据还在往里灌，早读会读到半张表）
    _wait_settled(ws, must_differ=False, timeout=45)

    # ① 近7日（原有口径：重点 SPU 名单 + goods.json 的 sales7）
    before = _table_sig(ws)
    all_rows, err = scrape_period(ws, WINDOW, before=before)
    if err:
        log("!! " + err)
        return 4
    sold = [x for x in all_rows if (x.get("skuSales") or 0) >= 1]
    log(f"  抓到 {len(all_rows)} 个 SKU，其中{WINDOW}有销量 {len(sold)} 个")

    # ② 今日（新口径：大屏「今日销量」列 + 按今日销量排序）
    #    同一页换个时间胶囊即可，SKU 行集合一致；抓不到不影响近7日的结果
    today_rows, today_at, today_err = [], "", ""
    if not a.no_today:
        today_at = time.strftime("%Y-%m-%d %H:%M:%S")
        before = _table_sig(ws)
        today_rows, today_err = scrape_period(ws, TODAY, before=before)
        if today_err:
            log("  [warn] 今日销量抓取失败：%s（只写近7日）" % today_err)
        else:
            n = sum(1 for x in today_rows if (x.get("skuSales") or 0) > 0)
            log(f"  今日：{len(today_rows)} 个 SKU，其中有今日销量 {n} 个")

    # SKU → SPU / SKC（本地商品库）
    con = sqlite3.connect(st["db"])
    try:
        con.row_factory = sqlite3.Row
        code2spu = {r["sku_code"]: r["spu_id"] for r in
                    con.execute("SELECT sku_code, spu_id FROM skus")}
        code2skc = {r["sku_code"]: (r["skc_code"] or "") for r in
                    con.execute("SELECT sku_code, skc_code FROM skus")}
        skc2spu = {r["skc_code"]: r["spu_id"] for r in
                   con.execute("SELECT skc_code, MIN(spu_id) spu_id FROM skus "
                               "WHERE skc_code IS NOT NULL AND skc_code != '' GROUP BY skc_code")}
        spu_title = {r["spu_id"]: r["title"] for r in
                     con.execute("SELECT spu_id, MIN(title) title FROM skus GROUP BY spu_id")}
    finally:
        con.close()

    def collect(rows: list) -> tuple[dict, dict, list, dict]:
        """→ ({skc: 销量}, {spu: 销量}, 库外货号, {skc: spu})。

        两个关键口径（都是 2026-10-14 对账对出来的）：

        ① **SKC 的值优先取页面上那个合并单元格「今日SKC销量 / 近7日SKC销量」** ——
           它是平台给的 SKC 合计（含本页翻不到的 SKU 行）。逐行相加会偏小：
           实测合并格 26、逐行只累到 9（因为同一 SKC 第 2 行起 rowspan 合并、列位置左移，
           提取脚本按固定列号读会读错，所以只信每组首行的合并格）。
        ② **SKC 货号以页面为准，不要用本地商品库的 skc_code** ——
           卖家改过名：同一个 SKU 页面写 `BHY-204203-4SetC`、库里还是旧的 `BHY-204203-4SetA`，
           按库里汇总就跟官方导出/页面对不上（大屏显示 0）。
           SPU 归属才回落到库里的 sku→spu。
        """
        skc_sum: dict[str, float] = {}
        skc_merged: dict[str, float] = {}
        skc_spu: dict[str, str] = {}
        miss = []
        for x in rows:
            sku = x["skuCode"]
            skc = (x.get("skcCode") or "").strip() or code2skc.get(sku) or ""
            if not skc:
                miss.append({"skuCode": sku, "skuSales": x.get("skuSales")})
                continue
            skc_sum[skc] = skc_sum.get(skc, 0) + (x.get("skuSales") or 0)
            if x.get("skcSales") is not None:
                skc_merged[skc] = max(skc_merged.get(skc, 0), x.get("skcSales") or 0)
            if skc not in skc_spu:
                skc_spu[skc] = skc2spu.get(skc) or code2spu.get(sku) or ""
        keys = set(skc_sum) | set(skc_merged)
        skc_val = {k: max(skc_sum.get(k, 0), skc_merged.get(k, 0)) for k in keys}
        spu_val: dict[str, float] = {}
        for skc, v in skc_val.items():
            spu = skc_spu.get(skc, "")
            if not spu:
                miss.append({"skcCode": skc, "value": v})
                continue
            spu_val[spu] = spu_val.get(spu, 0) + v
        return skc_val, spu_val, miss, skc_spu

    skc_sales, spu_sales, unmatched, skc_spu = collect(all_rows)
    skc_today, spu_today, _, _ = collect(today_rows)

    # ③ 口径自检 —— 两种"抓错了"的指纹：
    #
    #    ① **两个口径读到同一份数**：大量 SKC 的 今日 == 近7日 且都 > 0。
    #       2026-10-30 实测（用户报「今天销量是不是抓错了」）：BHY 点「今日」后 8 秒
    #       表格还没重算，抓到的仍是近7日 ⇒ 6 个 SPU 全部 today==s7，
    #       大屏 102105-3BHL 显示今日 129（真实今日 22），整列与排序都错。
    #    ② **今日远大于近7日**：真矛盾（不是"近7日窗口不含今天"那种 1~2 件的边界差）。
    #
    #    命中就重抓一次「今日」（先把口径切走再切回来，否则点击是 no-op、表格还是半张）；
    #    还不行就把矛盾款置 0（宁缺勿错，并在 JSON 里留痕）。
    def _same_window(skc_now: dict) -> int:
        return sum(1 for k, v in skc_now.items()
                   if v > 0 and abs(v - (skc_sales.get(k) or 0)) < 1e-9)

    def _hard_bad(skc_now: dict) -> list[str]:
        return sorted(k for k, v in skc_now.items()
                      if v >= 3 and v > (skc_sales.get(k) or 0) + 1e-9)

    def _looks_broken(skc_now: dict) -> bool:
        pos = sum(1 for v in skc_now.values() if v > 0)
        same = _same_window(skc_now)
        return bool(_hard_bad(skc_now)) or (pos >= 3 and same >= max(3, int(pos * 0.6)))

    if _looks_broken(skc_today) and not a.no_today:
        pos = sum(1 for v in skc_today.values() if v > 0)
        log(f"  [!] 今日销量疑似没换过口径（有量 {pos} 个，其中与近7日完全相同 "
            f"{_same_window(skc_today)} 个；今日>近7日 {len(_hard_bad(skc_today))} 个）→ 重抓")
        # 先把口径切到「近7日」，再切回「今日」：这样第二次点击一定能等到真变化
        s0 = _table_sig(ws)
        run_js_file(ws, HERE / "click_period.js", PERIOD=WINDOW)
        _wait_settled(ws, before=s0, timeout=60)
        s1 = _table_sig(ws)
        run_js_file(ws, HERE / "click_period.js", PERIOD=TODAY)
        _wait_settled(ws, before=s1, timeout=60)
        today_rows2, err2 = scrape_period(ws, TODAY, before=_table_sig(ws))
        if not err2:
            skc2, spu2, _, _ = collect(today_rows2)
            pos2 = sum(1 for v in skc2.values() if v > 0)
            if not _looks_broken(skc2):
                today_rows, skc_today, spu_today, today_err = today_rows2, skc2, spu2, ""
                log(f"  [ok] 重抓后自检通过（有量 {pos2} 个，与近7日相同 {_same_window(skc2)} 个）")
            else:
                log(f"  [warn] 重抓后仍异常（有量 {pos2} 个，相同 {_same_window(skc2)} 个）")
                today_rows, skc_today, spu_today = today_rows2, skc2, spu2
        hard = _hard_bad(skc_today)
        same_now = _same_window(skc_today)
        if same_now >= 3:
            # 整列不可信 ⇒ 全部置 0，让大屏回落到「按近7日排序」，别把近7日当今日显示
            for k in list(skc_today):
                skc_today[k] = 0
            spu_today = {}
            today_err = "今日口径自检未通过（与近7日同值 %d 个 / 超近7日 %d 个），已全部置 0" % (
                same_now, len(hard))
            log("  [warn] " + today_err)
        elif hard:
            for k in hard:
                skc_today[k] = 0
            spu_today = {}
            for skc, v in skc_today.items():
                spu = skc_spu.get(skc, "")
                if spu:
                    spu_today[spu] = spu_today.get(spu, 0) + v
            today_err = "今日口径自检未通过（%d 个 SKC 今日>近7日，已置 0）" % len(hard)
            log("  [warn] " + today_err)

    spus = sorted(
        ({"spuId": k, "sales7": round(v, 2),
          "todaySales": round(spu_today.get(k, 0), 2),
          "title": (spu_title.get(k) or "")[:80]}
         for k, v in spu_sales.items()
         if v > 0 or (spu_today.get(k) or 0) > 0),
        key=lambda z: -z["sales7"],
    )
    # 按 SKC 汇总（大屏榜单的行就是 SKC 货号；一个 SPU 下多个 SKC 时不能混）
    # 取两遍的并集：某个 SKC 可能只在「今日」有量（近7日那遍没抓到它的行）
    all_skcs = set(skc_sales) | set(skc_today)
    skcs = sorted(
        ({"skcCode": k, "sales7": round(skc_sales.get(k, 0), 2),
          "todaySales": round(skc_today.get(k, 0), 2)}
         for k in all_skcs
         if (skc_sales.get(k) or 0) > 0 or (skc_today.get(k) or 0) > 0),
        key=lambda z: -z["todaySales"],
    )

    payload = {
        "store": store,
        "storeLabel": st["label"],
        "window": WINDOW,
        "windows": {"sales7": WINDOW, "today": TODAY, "todayAt": today_at,
                    "todayError": today_err},
        "source": "TEMU 销量分析 · " + SALES_URL,
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "skuRows": len(all_rows),
        "skuSold": len(sold),
        "todayRows": len(today_rows),
        "todaySkuSold": sum(1 for x in today_rows if (x.get("skuSales") or 0) > 0),
        "totalSpu": len(spus),
        "totalSkc": len(skcs),
        "spus": spus,
        "skcs": skcs,
        "unmatched": unmatched,
    }
    out = Path(a.out) if a.out else ROOT / "data" / "focus" / f"{store.lower()}_focus.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"完成：{len(spus)} 个有销量的 SPU / {len(skcs)} 个 SKC（SKU {len(sold)} 个，"
        f"库外货号 {len(unmatched)} 个）-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())