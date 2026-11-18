# -*- coding: utf-8 -*-
"""读取三个 TEMU 店铺（BEL / BHY / KinglamanSport）后台**真实**的清零 / 已开预售货号。

数据来源：商品列表页自己调的 `POST/GET /visage-agent-seller/product/skc/pageQuery`
（每页 20 个 SKC，用页面上的分页按钮翻页，抓它的响应体 —— **不伪造请求头、不调写接口**）。

产物：
    _logs/skc-raw-<STORE>.json     原始 pageItems（调试/复核用，gitignore）
    data/presale-live.json         解析后的 店-货号-尺码 级明细

用法：
    python scripts/goods/fetch_presale_live.py --store BEL
    python scripts/goods/fetch_presale_live.py --store ALL
    python scripts/goods/fetch_presale_live.py --store ALL --parse-only   # 只用缓存重算
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF", "KinglamanSport": "TM-KinglamanSport"}
#: 各店后台域名不同：BEL/BHY 是半托管（agentseller.temu.com），
#: KinglamanSport 是本土店（seller.kuajingmaihuo.com，API 前缀 xpm-visage-mms）。
LIST_URL = {
    "BEL": "https://agentseller.temu.com/goods/list",
    "BHY": "https://agentseller.temu.com/goods/list",
    "KinglamanSport": "https://seller.kuajingmaihuo.com/goods/list",
}
URL = LIST_URL["BEL"]
RAW_DIR = ROOT / "_logs"
OUT = ROOT / "data" / "presale-live.json"

CLICK_PAGE = r"""
(function (p) {
  var lis = Array.prototype.slice.call(document.querySelectorAll('li'));
  var want = String(p);
  var li = lis.filter(function (e) { return (e.innerText || '').trim() === want; })[0];
  if (!li) {
    var nx = lis.filter(function (e) { return /^(>|下一页|Next)/.test((e.innerText || '').trim()); })[0];
    if (!nx) return 'no-lis:' + lis.length;
    li = nx;
  }
  if (window.__zr) {
    var p2 = window.__zr.findProps(li);
    if (p2 && typeof p2.onClick === 'function') { window.__zr.call(li, 'onClick'); return 'zr'; }
  }
  li.click();
  return 'dom';
})(%s)
"""


async def ensure_page(port: int, url: str = URL) -> dict:
    for _ in range(3):
        pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                 if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
        if pages:
            return pages[-1]
        tid = await cdp_net.new_tab(port, url, foreground=True)
        print("  · 新开 goods/list 页:", tid)
        await asyncio.sleep(11)
    raise SystemExit("打不开 goods/list")


async def read_store(store: str, max_pages: int = 40) -> list:
    port = probe_port(WINDOWS[store])
    page = await ensure_page(port, LIST_URL.get(store, URL))
    print("[%s] 用页 %s" % (store, page["id"]))
    items: list = []
    seen = set()

    def collect(hits):
        got = 0
        for h in hits:
            url = h.get("url") or ""
            if "skc/pageQuery" not in url or "HKHistory" in url:
                continue
            res = (h.get("json") or {}).get("result") or {}
            for it in res.get("pageItems") or []:
                k = it.get("productSkcId") or it.get("extCode")
                if k in seen:
                    continue
                seen.add(k)
                items.append(it)
                got += 1
        return got

    async with cdp_net.NetCapture(page["webSocketDebuggerUrl"], match=("skc/pageQuery",)) as cap:
        await zreact.inject(cap)
        await cap.send("Page.reload", {"ignoreCache": False})
        n1 = 0
        for _ in range(25):
            await asyncio.sleep(2)
            n1 = collect(cap.hits)
            if n1:
                break
        if not n1:
            # 列表没自己请求（页面停在空态/待办浮层）→ 点左侧「商品列表」逼它重新取数
            print("[%s] 没等到列表请求，点一次左侧「商品列表」" % store)
            v, _ = await cap.eval(
                "(function(){var els=Array.prototype.slice.call(document.querySelectorAll('a,li,div,span'));"
                "var t=els.filter(function(e){return (e.innerText||'').trim()==='商品列表';});"
                "if(!t.length) return 'no-menu'; var e=t[t.length-1]; e.click(); return 'clicked';})()",
                timeout=20)
            print("[%s] 菜单=%s" % (store, v))
            for _ in range(20):
                await asyncio.sleep(2)
                n1 = collect(cap.hits)
                if n1:
                    break
        if not n1:
            v, _ = await cap.eval(
                "(function(){var b=window.__zr&&window.__zr.btn('查询');"
                "if(!b) return 'no-btn'; window.__zr.call(b,'onClick'); return 'clicked';})()",
                timeout=20)
            print("[%s] 查询=%s" % (store, v))
            for _ in range(10):
                await asyncio.sleep(2)
                n1 = collect(cap.hits)
                if n1:
                    break
        print("[%s] 第 1 页 +%d" % (store, n1))
        total = None
        for h in cap.hits:
            res = (h.get("json") or {}).get("result") or {}
            if res.get("total"):
                total = res["total"]
                break
        pages = max(1, min(max_pages, ((total or 20) + 19) // 20))
        print("[%s] total=%s，需要 %d 页" % (store, total, pages))
        for p in range(2, pages + 1):
            before = len(cap.hits)
            v, exc = await cap.eval(CLICK_PAGE % json.dumps(p), timeout=20)
            ok = False
            for _ in range(12):
                await asyncio.sleep(1.2)
                if len(cap.hits) > before and collect(cap.hits[before:]):
                    ok = True
                    break
            print("[%s] 第 %d 页 click=%s 累计 %d 条%s" % (store, p, v, len(items), "" if ok else "  ⚠ 没抓到新数据"))
            if not ok:
                break
    raw = RAW_DIR / ("skc-raw-%s.json" % store)
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    print("[%s] 原始 %d 个 SKC → %s" % (store, len(items), raw))
    return items


def _size_of(sku: dict) -> str:
    for sp in sku.get("productSkuSpecList") or []:
        name = str(sp.get("parentSpecName") or "")
        if name in ("尺码", "尺寸", "Size"):
            return str(sp.get("specName") or "").upper()
    # 兜底：从 extCode 尾部取
    code = str(sku.get("extCode") or "")
    return code.rsplit("-", 1)[-1].upper() if "-" in code else ""


def parse_store(store: str, items: list, day: str) -> list:
    rows = []
    for skc in items:
        code = str(skc.get("extCode") or "")
        spu = str(skc.get("productId") or "")
        skc_pre = bool(skc.get("enablePreSale"))
        end = skc.get("preTagEndTime") or ""
        for sku in skc.get("productSkuSummaries") or []:
            semi = sku.get("productSkuSemiManagedStock") or {}
            stock = semi.get("skuStockQuantity")
            if stock is None:
                stock = sku.get("virtualStock")
            on = semi.get("enablePreSale")
            rows.append({
                "store": store,
                "code": code,
                "spu": spu,
                "size": _size_of(sku),
                "skuCode": str(sku.get("extCode") or ""),
                "skuId": str(sku.get("productSkuId") or ""),
                "temuStock": int(stock or 0),
                "presaleOn": bool(on) if on is not None else skc_pre,
                "presaleEnd": str(end or "")[:10],
                "presaleEndTs": end,
                "readAt": day,
            })
    return rows


def build(stores: list[str]) -> dict:
    day = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out: dict = {"generatedAt": day, "source": "goods/list → skc/pageQuery（后台实读）",
                 "stores": {}, "counts": {}}
    for st in stores:
        raw = RAW_DIR / ("skc-raw-%s.json" % st)
        if not raw.exists():
            print("[%s] 没有缓存，跳过" % st)
            continue
        items = json.loads(raw.read_text(encoding="utf-8"))
        rows = parse_store(st, items, day)
        cleared = sorted({r["code"] for r in rows if r["temuStock"] == 0})
        presale = sorted({r["code"] for r in rows if r["presaleOn"]})
        out["stores"][st] = {
            "skc": len({r["code"] for r in rows}),
            "sku": len(rows),
            "clearedCodes": cleared,
            "presaleCodes": presale,
            "rows": rows,
        }
        out["counts"][st] = {"skc": len({r["code"] for r in rows}), "sku": len(rows),
                             "已清零货号": len(cleared), "已开预售货号": len(presale)}
        print("[%s] SKC %d / SKU %d｜清零货号 %d｜预售货号 %d"
              % (st, out["counts"][st]["skc"], len(rows), len(cleared), len(presale)))
        if presale:
            print("     预售中:", ", ".join(presale[:12]) + (" …" if len(presale) > 12 else ""))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写入:", OUT)
    return out


async def run(stores: list[str]) -> int:
    for st in stores:
        try:
            await read_store(st)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            print("[%s] 读取失败：%s: %s" % (st, type(e).__name__, str(e)[:200]))
    build(stores)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="ALL", help="BEL / BHY / KinglamanSport / ALL")
    ap.add_argument("--parse-only", action="store_true", help="只用 _logs 里的缓存重算")
    a = ap.parse_args()
    stores = list(WINDOWS) if a.store.upper() == "ALL" else [a.store]
    for st in stores:
        if st not in WINDOWS:
            raise SystemExit("未知店铺 %s（可选 %s）" % (st, "/".join(WINDOWS)))
    if a.parse_only:
        build(stores)
        return 0
    return asyncio.run(run(stores))


if __name__ == "__main__":
    raise SystemExit(main())
