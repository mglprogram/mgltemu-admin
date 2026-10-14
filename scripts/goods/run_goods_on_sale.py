# -*- coding: utf-8 -*-
"""TEMU 商品列表「在售中」导出为 JSON（前端看板数据源）。

背景（2026-10-12 现场实测，非推断）：
    · 页面 https://agentseller.temu.com/goods/list 是 qiankun 微前端（`__POWERED_BY_QIANKUN__`），
      子应用跑在沙箱里 —— 从页面顶层改写 `window.fetch/XMLHttpRequest` **抓不到它的请求**
      （实测 `window.__cap` 恒空，而 PerformanceObserver 能看到请求确实发出）；
      页面内同步 XHR 直调接口又缺风控头，一律 403 `No Permission to Access`。
    · ⇒ 走**协议层抓包**：CDP `Network.*`（cdp_net.py），既拿得到页面自己发的请求响应体，
      也与页面脚本无关。响应结构（实测）：
        POST /visage-agent-seller/product/skc/pageQuery
        {"success":true,"result":{"total":213,"pageItems":[{productId, productSkcId,
          productName, extCode(货号), skcTopStatus(0未发布/100在售中/200已下架/300已删除),
          leafCat.catName, productTotalSalesVolume, last7DaysSalesVolume,
          productSkuSummaries:[{productSkuId, extCode(SKU货号), skuSpecList|specList,
            supplierPrice(申报价), currencyType, siteStatus, stock…}]}]}}
        POST /visage-agent-seller/product/skc/countStatus → 各状态数量

用法：
    python scripts/goods/run_goods_on_sale.py --store BEL
    python scripts/goods/run_goods_on_sale.py --store BHY --max-pages 8
    python scripts/goods/run_goods_on_sale.py --store BEL --dry-run   # 只抓不落盘

产物：
    data/goods/<store>_on_sale.json     原始抓取结果（含全部状态，按状态分桶）
    temu-admin/public/data/goods.json   build_goods_data.py 合并后给前端读的静态数据
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cdp_net  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

ROOT = Path(r"D:\AI-workplace\TEMU")
DATA = ROOT / "data" / "goods"

#: 店铺 → (紫鸟窗口名, 紫鸟 store_id)。与 scripts/power/data/ziniao_power_map.json 一致。
STORES = {
    "BEL": ("TM-BEL-MGL", "27677489075954"),
    "BHY": ("TM-BHY-YJF", "27697436126374"),
}

URL = "https://agentseller.temu.com/goods/list"
API_PAGE = "product/skc/pageQuery"
API_COUNT = "product/skc/countStatus"

#: skcTopStatus → 中文状态（来自页面 tab：全部 / 在售中 / 未发布到站点 / 已下架已终止 / 已删除）
TOP_STATUS = {0: "未发布到站点", 100: "在售中", 200: "已下架/已终止", 300: "已删除"}

#: 需要的状态（本次只要「在售中」；其余仅用于对账日志，不落明细）
WANT_STATUS = 100

#: 调试开关（--debug 打开）：打印每次页面求值的表达式与原始返回
DEBUG = False

#: 需要替换的参数占位符（见 `_eval`：用 JSON 值原地替换，不再靠「函数 + 参数」拼接）
PH = "__ARG__"


# ---------------------------------------------------------------- 页面动作
JS_PAGE_INFO = r"""
(function () {
  try {
    return JSON.stringify({u: location.href, t: document.title});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

JS_MARK = r"""
(function () {
  try {
    window.__gs = {calls: [], rows: 0};
    return JSON.stringify({ok: true});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

JS_CLICK_TAB = r"""
(function () {
  try {
    var want = __ARG__;
    var nodes = Array.prototype.slice.call(document.querySelectorAll('*')).filter(function (e) {
      return (e.innerText || '').trim() === want && e.getClientRects().length > 0;
    });
    nodes = nodes.filter(function (e) {
      return !nodes.some(function (o) { return o !== e && e.contains(o); });
    });
    // 优先挑 BUTTON/LI 这类能承载点击的元素，文本节点本身点了不生效
    var target = nodes[0];
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var p = n.parentElement;
      if (n.tagName === 'BUTTON' || n.tagName === 'LI' ||
          (p && (p.tagName === 'LI' || p.tagName === 'BUTTON'))) { target = n; break; }
    }
    if (!target) return JSON.stringify({ok: false, reason: 'not-found', count: nodes.length});
    target.click();
    return JSON.stringify({ok: true, tag: target.tagName,
      cls: String(target.className).slice(0, 80), count: nodes.length});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

JS_ROW_COUNT = r"""
(function () {
  try {
    var rows = document.querySelectorAll('tbody tr');
    return JSON.stringify({rows: rows.length});
  } catch (e) { return JSON.stringify({rows: -1, error: String(e)}); }
})()
"""

JS_CLICK_PAGE = r"""
(function () {
  try {
    var want = String(__ARG__);
    var nodes = Array.prototype.slice.call(document.querySelectorAll('a,button,li,span'));
    var hit = nodes.filter(function (e) {
      return (e.innerText || '').trim() === want && e.getClientRects().length > 0;
    });
    // 分页项一般在 [class*=Pagination]/[class*=pager] 里，优先那批
    var inPager = hit.filter(function (e) {
      return e.closest('[class*=agination],[class*=ager]');
    });
    var target = (inPager[0] || hit[0]);
    if (!target) return JSON.stringify({ok: false, count: hit.length});
    target.click();
    return JSON.stringify({ok: true, tag: target.tagName, count: hit.length});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

JS_CLICK_NEXT = r"""
(function () {
  try {
    var nodes = Array.prototype.slice.call(document.querySelectorAll('a,button,li,span'));
    var hit = nodes.filter(function (e) {
      var t = (e.innerText || '').trim();
      return (t === '下一页' || t === 'Next' || t === '>' || t === '›')
        && e.getClientRects().length > 0;
    });
    if (!hit.length) return JSON.stringify({ok: false});
    hit[0].click();
    return JSON.stringify({ok: true, tag: hit[0].tagName});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""


def _flatten_js(src: str) -> str:
    """把多行 JS 压成单行（防 `Runtime.evaluate` 的 ASI 坑），**先剥掉行注释**。

    ⚠ 2026-10-12 踩坑：直接把多行 `join` 成一行时，源码里的 `// 说明文字`
    会把**后面所有代码**都注释掉，页面报 `SyntaxError: Unexpected end of input`
    （错误位置正好在表达式末尾，极易误判成「括号没配对」）。
    """
    out = []
    for ln in src.splitlines():
        code = ln.split("//", 1)[0].strip()      # 约定：JS 源码里不出现含 // 的字符串字面量
        if code:
            out.append(code)
    return " ".join(out)


async def _eval(cap: cdp_net.NetCapture, js: str, arg=None, timeout: float = 30.0,
                retries: int = 2, tag: str = ""):
    """页面内 eval：把 `__ARG__` 占位符**原地替换**成 JSON 值，再整段求值。

    为什么不用「函数 + 参数拼接」（2026-10-12 连踩三次，见 cdp_net.page_eval_js 头注）：
    拼接要靠数括号，`(f)()` 自带调用括号时极易变成 `((f)())("x")`（把返回值当函数调）。
    占位符替换没有这层拼接，表达式结构在源码里就是固定的，最省心。
    """
    src = js
    if arg is not None:
        src = js.replace(PH, json.dumps(arg, ensure_ascii=False))
    if PH in src:
        raise RuntimeError("JS 里还有未替换的 __ARG__ 占位符（调用方漏传参数）")
    expr = _flatten_js(src)
    last = ""
    for i in range(max(1, retries + 1)):
        r = await cap.send("Runtime.evaluate",
                           {"expression": expr, "returnByValue": True,
                            "awaitPromise": False, "userGesture": True}, timeout=timeout)
        if "exceptionDetails" not in r:
            res = r.get("result") or {}
            return None if res.get("subtype") == "error" else res.get("value")
        last = json.dumps(r["exceptionDetails"], ensure_ascii=False)[:300]
        if DEBUG:
            print(f"   [debug] {tag} 第{i+1}次失败 expr[-90:]={expr[-90:]!r}")
            print(f"   [debug] raw={json.dumps(r, ensure_ascii=False)[:400]}")
        await asyncio.sleep(1.5)
    print(f"   [warn] eval 连续失败{('(' + tag + ')') if tag else ''}: {last}")
    if DEBUG:
        print(f"   [debug] {tag} 完整表达式：\n{expr}")
    return None


async def _wait_rows(cap, min_rows: int, timeout: float = 40.0) -> int:
    """等表格渲染出行（说明本次请求的响应已经落到 DOM）。失败返回最后一次读数。"""
    deadline = time.time() + timeout
    last = -1
    while time.time() < deadline:
        v = await _eval(cap, JS_ROW_COUNT, retries=0)
        try:
            d = json.loads(v) if v else {}
            last = int(d.get("rows") or -1)
            if last >= min_rows:
                return last
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(1.0)
    return last


def _page_query_hits(cap) -> list:
    return [h for h in cap.hits if API_PAGE in h["url"]]


async def _click_and_wait(cap, js: str, arg, before: int, log, what: str,
                          settle: float, timeout: float = 45.0):
    """点一个控件并等「新的 pageQuery 响应」回来（DOM 读不到就只看响应条数）。"""
    r = await _eval(cap, js, arg, tag=what)
    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(1.0)
        if len(_page_query_hits(cap)) > before:
            await asyncio.sleep(min(settle, 3.0))     # 让 DOM 跟上，便于下一次点击
            return r, len(_page_query_hits(cap)) - before
    return r, 0


def _pick_pages_count(cap_hits: list) -> int:
    for h in cap_hits:
        r = (h.get("json") or {}).get("result") or {}
        if isinstance(r, dict) and r.get("total") is not None:
            return int(r["total"])
    return 0


async def fetch_store(store: str, max_pages: int, settle: float, log=print) -> dict:
    win, store_id = STORES[store]
    port = probe_port(win)
    if not port:
        raise RuntimeError(f"探不到窗口 {win} 的 CDP 端口（窗口没开？）")
    log(f"[{store}] 窗口 {win} CDP 端口 {port}")

    tid = await cdp_net.new_tab(port, URL)
    try:
        await asyncio.sleep(3)
        t = next((x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                  if (x.get("id") or x.get("targetId")) == tid), None)
        if not t:
            raise RuntimeError("新标签页未注册（CDP /json/list 里找不到）")
        ws = t["webSocketDebuggerUrl"]

        pages: list[dict] = []
        counts: dict = {}
        page_size = 0

        async def act(cap: cdp_net.NetCapture):
            nonlocal counts, page_size
            info = await _eval(cap, JS_PAGE_INFO, tag="page-info")
            log(f"[{store}] 目标页: {info}")
            log(f"[{store}] 等首屏数据 …")
            rows = await _wait_rows(cap, 5, timeout=60)
            log(f"[{store}] 首屏渲染 {rows} 行；已捕获 {len(_page_query_hits(cap))} 次 pageQuery")

            # 切到「在售中」：默认 tab 就是它时点一次也无副作用
            before = len(_page_query_hits(cap))
            r, got = await _click_and_wait(cap, JS_CLICK_TAB, "在售中", before, log,
                                           "切在售中", settle)
            log(f"[{store}] 点击「在售中」→ {r}（新增响应 {got} 条）")

            # 逐页翻：先算页数，再点 2..N
            hits_p1 = _page_query_hits(cap)
            total = _pick_pages_count(hits_p1)
            if hits_p1:
                for h in hits_p1:
                    r0 = (h.get("json") or {}).get("result") or {}
                    if r0.get("total") == 100 or r0.get("total"):
                        page_size = len(r0.get("pageItems") or []) or page_size
                        total = r0.get("total") or total
            n_pages = max_pages
            if total and page_size:
                n_pages = min(max_pages, (total + page_size - 1) // page_size)
            log(f"[{store}] 总数 {total} · 每页 {page_size} → 计划翻 {n_pages} 页")

            # ⚠ 只用「下一页」顺序翻页（2026-10-12 现场教训）：
            #   分页器只渲染当前附近几页（1..5 + 下一页），直接点页码 7/8/9 会点空；
            #   而「点空 → 兜底点下一页 → 下一次再点页码」会让**页码与实际页错位**，
            #   出现漏页。顺序点「下一页」没有这个问题。
            for p in range(2, n_pages + 1):
                before = len(_page_query_hits(cap))
                r, got = await _click_and_wait(cap, JS_CLICK_NEXT, None, before, log,
                                               f"下一页→第{p}页", settle)
                log(f"[{store}] 翻到第 {p} 页 → {r}（新增响应 {got} 条）")
                if got == 0:
                    # 兜底：下一页按钮没文本命中时，改点页码 p
                    before = len(_page_query_hits(cap))
                    r2, got2 = await _click_and_wait(cap, JS_CLICK_PAGE, p, before, log,
                                                     f"页码{p}", settle)
                    log(f"[{store}]   页码 {p} 兜底 → {r2}（新增响应 {got2} 条）")
                    if got2 == 0:
                        log(f"[{store}]   第 {p} 页拿不到，停止翻页")
                        break

        hits = await cdp_net.capture_on_target(
            ws, match=(API_PAGE, API_COUNT), action=act, settle=3.0)

        for h in hits:
            name = h["url"].rsplit("/", 1)[-1]
            j = h.get("json") or {}
            res = j.get("result") or {}
            if name == "countStatus" and isinstance(res, dict):
                for row in res.get("skcTopStatusCountList") or []:
                    counts[int(row.get("skcTopStatus"))] = int(row.get("skcTotal") or 0)
            elif name == "pageQuery" and isinstance(res, dict):
                pages.append(res)

        # 翻页可能重复抓到同一页（点空了），按 productSkcId 去重
        items: dict[int, dict] = {}
        for res in pages:
            for it in res.get("pageItems") or []:
                key = it.get("productSkcId") or it.get("goodsId")
                if key:
                    items[int(key)] = it
        log(f"[{store}] 抓到 {len(items)} 个 SKC（去重后），状态分布 "
            + json.dumps({TOP_STATUS.get(k, k): v for k, v in counts.items()},
                         ensure_ascii=False))
        return {
            "store": store,
            "window": win,
            "storeId": store_id,
            "url": URL,
            "total": _pick_pages_count(pages),
            "statusCounts": {str(k): v for k, v in counts.items()},
            "count": len(items),
            "items": list(items.values()),
        }
    finally:
        await cdp_net.close_tab(port, tid)


# ---------------------------------------------------------------- 输出
def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  写入 {path}  ({path.stat().st_size/1024:.0f} KB)")


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 TEMU 商品列表「在售中」")
    ap.add_argument("--store", default="BEL", help="BEL / BHY / all")
    ap.add_argument("--max-pages", type=int, default=30, help="最多翻多少页（防翻车）")
    ap.add_argument("--settle", type=float, default=3.0, help="每次点击后等待秒数")
    ap.add_argument("--dry-run", action="store_true", help="只抓不写 data/goods")
    ap.add_argument("--debug", action="store_true", help="打印页面求值的表达式与原始返回")
    a = ap.parse_args()

    global DEBUG
    DEBUG = bool(a.debug)

    stores = list(STORES) if a.store.lower() == "all" else [a.store.upper()]
    results = []
    for s in stores:
        if s not in STORES:
            print(f"!! 未知店铺 {s}（可选 {list(STORES)}）")
            return 2
        try:
            r = asyncio.run(fetch_store(s, a.max_pages, a.settle))
            r["scrapedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            results.append(r)
            if not a.dry_run:
                write_json(DATA / f"{s.lower()}_goods_raw.json", r)
        except Exception as e:  # noqa: BLE001
            print(f"!! [{s}] 抓取失败: {str(e)[:300]}")
            results.append({"store": s, "error": str(e)[:300]})

    if all("error" in r for r in results):
        print("!! 全部店铺抓取失败 —— 不覆盖任何已有数据")
        return 1
    print("完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
