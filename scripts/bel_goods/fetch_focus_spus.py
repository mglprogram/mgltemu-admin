# -*- coding: utf-8 -*-
"""从「销量分析」页抓取**近7日有销量**的 SPU，作为动态重点 SPU 名单。

页面：https://agentseller.temu.com/main/data-center/goods-data
      （时间口径：今日 / 近7日 / 近30日 / 自定义；本脚本用**近7日**）

流程（每店）：
  1. 找一个已登录的卖家页 tab，导航到销量分析页；若被重定向到认证页则走授权
  2. 点「近7日」
  3. 逐页抓取表格（SKU 级），校验翻页真的换了数据
  4. 取「近7日SKU销量 >= 1」的行
  5. SKU货号 → SPU：优先查本地商品库 data/<store>/<store>_products.db；
     查不到的记入 unmatched（多为快照之后新建的款），不影响其余 SPU

产物：data/focus/<store>_focus.json
  { store, window: "近7日", generatedAt, totalSpu, spus: [...], unmatched: [...] }

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
import flux_auth  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SALES_URL = "https://agentseller.temu.com/main/data-center/goods-data"
STORES = {
    "BEL": {"win": "TM-BEL-MGL", "db": ROOT / "data/bel/bel_products.db", "label": "BEL 店"},
    "BHY": {"win": "TM-BHY-YJF", "db": ROOT / "data/bhy/bhy_products.db", "label": "BHY 店"},
}
WINDOW = "近7日"


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
def prepare(store: str, port: int) -> tuple[str, str]:
    """确保销量分析页已就绪，返回 (websocket 或 "", 说明)。"""
    st = STORES[store]
    t = flux_auth.find_tab(port, "goods-data")
    if not t:
        # 用一个已登录的卖家页 tab 导航过去
        pick = None
        for x in flux_auth.list_tabs(port):
            if flux_auth.is_seller_page(x.get("url") or ""):
                pick = x
                break
        if not pick:
            tabs = flux_auth.list_tabs(port)
            pick = tabs[0] if tabs else None
        if not pick:
            return "", "窗口里没有可用 tab"
        flux_auth.navigate(pick["webSocketDebuggerUrl"], SALES_URL)
        time.sleep(10)
        t = flux_auth.find_tab(port, "goods-data")

    if t:
        u = t.get("url") or ""
        if "auth" not in u and "login" not in u:
            return t["webSocketDebuggerUrl"], "已在销量分析页"

    # 跳到认证页了 → 走授权，再导航一次
    log("  [auth] 销量分析页被重定向到认证页，走授权…")
    ok, msg = flux_auth.ensure_logged_in(port, store, timeout=240)
    log("  [auth] " + ("OK " if ok else "FAIL ") + msg)
    if not ok:
        return "", msg

    t = flux_auth.find_tab(port, "goods-data")
    if not t:
        pick = next((x for x in flux_auth.list_tabs(port)
                     if flux_auth.is_seller_page(x.get("url") or "")), None)
        if not pick:
            return "", "授权后找不到可用 tab"
        flux_auth.navigate(pick["webSocketDebuggerUrl"], SALES_URL)
        time.sleep(10)
        t = flux_auth.find_tab(port, "goods-data")
    if not t:
        return "", "授权后仍打不开销量分析页"
    return t["webSocketDebuggerUrl"], "授权后已打开"


# ---------------------------------------------------------------- 抓取
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, choices=list(STORES))
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    store = a.store
    st = STORES[store]
    port = flux_auth.find_store_port(st["win"])
    if not port:
        log("!! 未探测到窗口 %s 的 CDP 端口" % st["win"])
        return 2
    log(f"== {store} ({st['label']}) port={port}")

    ws, note = prepare(store, port)
    log("  " + note)
    if not ws:
        return 3

    # 选「近7日」
    period = run_js_file(ws, HERE / "click_period.js", PERIOD=WINDOW)
    log("  时间口径 " + json.dumps(period, ensure_ascii=False))
    if not period.get("ok"):
        log("!! 找不到「%s」时间胶囊" % WINDOW)
        return 4
    time.sleep(8)

    # 逐页抓
    all_rows, seen = [], set()
    for pg in range(1, 8):
        if pg > 1:
            r = run_js_file(ws, HERE / "click_page.js", PAGE=str(pg))
            if not r.get("ok"):
                log(f"  翻页到第{pg}页失败：{r.get('msg')}")
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

    sold = [x for x in all_rows if (x.get("skuSales") or 0) >= 1]
    log(f"  抓到 {len(all_rows)} 个 SKU，其中近7日有销量 {len(sold)} 个")

    # SKU → SPU
    con = sqlite3.connect(st["db"])
    try:
        con.row_factory = sqlite3.Row
        code2spu = {r["sku_code"]: r["spu_id"] for r in
                    con.execute("SELECT sku_code, spu_id FROM skus")}
        spu_title = {r["spu_id"]: r["title"] for r in
                     con.execute("SELECT spu_id, MIN(title) title FROM skus GROUP BY spu_id")}
    finally:
        con.close()

    spu_sales: dict[str, float] = {}
    unmatched = []
    for x in sold:
        spu = code2spu.get(x["skuCode"])
        if not spu:
            unmatched.append({"skuCode": x["skuCode"], "skuSales": x.get("skuSales")})
            continue
        spu_sales[spu] = spu_sales.get(spu, 0) + (x.get("skuSales") or 0)

    spus = sorted(
        ({"spuId": k, "sales7": round(v, 2), "title": (spu_title.get(k) or "")[:80]}
         for k, v in spu_sales.items()),
        key=lambda z: -z["sales7"],
    )

    payload = {
        "store": store,
        "storeLabel": st["label"],
        "window": WINDOW,
        "source": "TEMU 销量分析 · " + SALES_URL,
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "skuRows": len(all_rows),
        "skuSold": len(sold),
        "totalSpu": len(spus),
        "spus": spus,
        "unmatched": unmatched,
    }
    out = Path(a.out) if a.out else ROOT / "data" / "focus" / f"{store.lower()}_focus.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"完成：{len(spus)} 个有销量的 SPU（SKU {len(sold)} 个，"
        f"库外货号 {len(unmatched)} 个）-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())