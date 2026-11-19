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
  // ★ 2026-11-19：页码窗口只渲染「1..5 + 末页」（实测 BHY：li 里只有 1/2/3/4/5/10）。
  //   点第 6 页会找不到 li ⇒ 先点窗口里**最大的那个页码**，让窗口往后滚，再点目标页。
  function pagerLis() {
    return Array.prototype.slice.call(document.querySelectorAll('li'))
      .filter(function (e) { return /^\s*\d+\s*$/.test(e.innerText || ''); });
  }
  function click(el) {
    if (window.__zr) {
      var p2 = window.__zr.findProps(el);
      if (p2 && typeof p2.onClick === 'function') { window.__zr.call(el, 'onClick'); return 'zr'; }
    }
    el.click();
    return 'dom';
  }
  var want = String(p);
  var lis = pagerLis();
  var hit = lis.filter(function (e) { return (e.innerText || '').trim() === want; })[0];
  if (hit) return 'direct:' + click(hit);
  var nums = lis.map(function (e) { return parseInt((e.innerText || '').trim(), 10); })
    .filter(function (n) { return n > 0; });
  var maxN = nums.length ? Math.max.apply(null, nums) : 0;
  if (maxN && maxN < parseInt(want, 10)) {
    var last = lis.filter(function (e) { return (e.innerText || '').trim() === String(maxN); })[0];
    return 'roll:' + maxN + ':' + click(last);
  }
  return 'no-lis:' + lis.length;
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
    # ★ 2026-11-19：BHY 的商品列表页被用户/别的脚本筛成了「某个 SPU」（共有 1 条），
    #   而且该店的页码箭头不是 `li`（点不动）⇒ 复用旧页会只读到那一屏、还翻不了页。
    #   这里**一律新开一张干净页**读全量（不动用户自己的页面，读完就关）。
    tid = await cdp_net.new_tab(port, LIST_URL.get(store, URL), foreground=True)
    print("[%s] 新开干净页 %s" % (store, tid))
    # ⚠ 新开的 target 要等它出现在 /json/list 里才有 webSocketDebuggerUrl（立刻查会 KeyError）
    page = None
    for _ in range(20):
        await asyncio.sleep(1.5)
        for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port):
            if x.get("id") == tid and x.get("webSocketDebuggerUrl"):
                page = x
                break
        if page:
            break
    if not page:
        raise RuntimeError("[%s] 新页没枚举出来（%s）" % (store, tid))
    await asyncio.sleep(10)
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
        # ★ 2026-11-19：用户/别的脚本常把列表页筛选成「某个 SPU」，这时只有 1~20 条。
        #   分页前先**清掉筛选**（点「重置」），否则只读到那一屏就说「读完了」（BHY 只读到 21 条）。
        if n1:
            try:
                v, _ = await cap.eval(
                    "(function(){var b=window.__zr&&window.__zr.btn('重置');"
                    "if(!b) return 'no-reset'; window.__zr.call(b,'onClick'); return 'reset';})()",
                    timeout=20)
                print("[%s] 先重置筛选：%s" % (store, v))
            except Exception as e:  # noqa: BLE001
                print("[%s] 重置失败：%s" % (store, e))
            before = len(cap.hits)
            for _ in range(10):
                await asyncio.sleep(2)
                if len(cap.hits) > before and collect(cap.hits[before:]):
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
            # ⚠ 2026-11-19 实测：点页码不是每个数字都在 —— 一屏只渲染「1..5 … 末页」这类窗口，
            #   点第 6 页会找不到 `li`（click=no-lis），整轮就停在半路（BHY 只读到 41 个 SKC）。
            #   ⇒ 页码不在可视窗口时改点「下一页」箭头；两个都找不到就重载列表重来一次。
            for _try in range(3):
                v, exc = await cap.eval(CLICK_PAGE % json.dumps(p), timeout=20)
                ok = False
                for _ in range(10):
                    await asyncio.sleep(1.2)
                    if len(cap.hits) > before and collect(cap.hits[before:]):
                        ok = True
                        break
                if ok:
                    break
                print("[%s] 第 %d 页重试（%s）" % (store, p, v))
                await asyncio.sleep(1.5)
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


def parse_store(store: str, items: list, day: str, keep: dict | None = None) -> list:
    """`keep`：上一次的 {（code,size): row}，用来保住 refresh_presale_live_fields 读回来的
    在途预售库存 / 补货日期（重读列表不该把它们冲掉）。"""
    keep = keep or {}
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
            size = _size_of(sku)
            old = keep.get((code, size)) or {}
            row = {
                "store": store,
                "code": code,
                "spu": spu,
                "size": size,
                "skuCode": str(sku.get("extCode") or ""),
                "skuId": str(sku.get("productSkuId") or ""),
                "temuStock": int(stock or 0),
                "presaleOn": bool(on) if on is not None else skc_pre,
                "presaleEnd": str(end or "")[:10],
                "presaleEndTs": end,
                "readAt": day,
            }
            if old.get("presaleQty"):
                row["presaleQty"] = old["presaleQty"]
            if old.get("presaleDate"):
                row["presaleDate"] = old["presaleDate"]
            rows.append(row)
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
        prev = {}
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            for r in ((old.get("stores") or {}).get(st) or {}).get("rows") or []:
                prev[(str(r.get("code") or ""), str(r.get("size") or "").upper())] = r
        except Exception:  # noqa: BLE001
            prev = {}
        rows = parse_store(st, items, day, prev)
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
