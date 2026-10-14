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
    """关掉自己开的临时标签页（异常路径也别留）。"""
    if not tid:
        return
    try:
        import cdp_transport as cdp  # noqa: PLC0415
        cdp.Cdp(cdp.http_version(port)["webSocketDebuggerUrl"]).send(
            "Target.closeTarget", {"targetId": tid})
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
def scrape_period(ws: str, period: str, max_pages: int = 7) -> tuple[list, str]:
    """点时间胶囊 + 逐页抓，返回 (rows, 错误说明)。rows 里的 skuSales/skcSales 就是该口径的值。"""
    r = run_js_file(ws, HERE / "click_period.js", PERIOD=period)
    log("  时间口径 " + json.dumps(r, ensure_ascii=False))
    if not r.get("ok"):
        return [], "找不到「%s」时间胶囊" % period
    time.sleep(8)

    all_rows, seen = [], set()
    for pg in range(1, max_pages + 1):
        if pg > 1:
            rr = run_js_file(ws, HERE / "click_page.js", PAGE=str(pg))
            if not rr.get("ok"):
                log(f"  翻页到第{pg}页失败：{rr.get('msg')}")
                break
            time.sleep(7)
        data = run_js_file(ws, HERE / "extract_sales.js")
        rows = [x for x in data.get("rows", [])
                if x.get("skuCode") and x["skuCode"] not in seen]
        new = len(rows)
        for x in rows:
            seen.add(x["skuCode"])
        all_rows += rows
        log(f"  第{pg}页: +{new}（累计 {len(all_rows)}）")
        if new == 0:
            break
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

    ws, tid, note = prepare(store, port)
    log("  " + note)
    if not ws:
        _close_target(port, tid)
        return 3
    try:
        return scrape_all(store, st, ws, a)
    finally:
        _close_target(port, tid)


def scrape_all(store: str, st: dict, ws: str, a) -> int:
    # ① 近7日（原有口径：重点 SPU 名单 + goods.json 的 sales7）
    all_rows, err = scrape_period(ws, WINDOW)
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
        today_rows, today_err = scrape_period(ws, TODAY)
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
        spu_title = {r["spu_id"]: r["title"] for r in
                     con.execute("SELECT spu_id, MIN(title) title FROM skus GROUP BY spu_id")}
    finally:
        con.close()

    def agg(rows: list, key_of) -> tuple[dict, list]:
        """按 SPU 或 SKC 汇总 skuSales；返回 ({key: 数量}, 库外货号)。"""
        total: dict[str, float] = {}
        miss = []
        for x in rows:
            k = key_of(x)
            if not k:
                miss.append({"skuCode": x["skuCode"], "skuSales": x.get("skuSales")})
                continue
            total[k] = total.get(k, 0) + (x.get("skuSales") or 0)
        return total, miss

    spu_sales, unmatched = agg(sold, lambda x: code2spu.get(x["skuCode"]))
    spu_today, _ = agg(today_rows, lambda x: code2spu.get(x["skuCode"]))
    skc_sales, _ = agg(sold, lambda x: code2skc.get(x["skuCode"]) or x.get("skcCode"))
    skc_today, _ = agg(today_rows, lambda x: code2skc.get(x["skuCode"]) or x.get("skcCode"))

    spus = sorted(
        ({"spuId": k, "sales7": round(v, 2),
          "todaySales": round(spu_today.get(k, 0), 2),
          "title": (spu_title.get(k) or "")[:80]}
         for k, v in spu_sales.items()),
        key=lambda z: -z["sales7"],
    )
    # 按 SKC 汇总（大屏榜单的行就是 SKC 货号；一个 SPU 下多个 SKC 时不能混）
    skcs = sorted(
        ({"skcCode": k, "sales7": round(v, 2), "todaySales": round(skc_today.get(k, 0), 2)}
         for k, v in skc_sales.items()),
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