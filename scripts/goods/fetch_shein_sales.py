# -*- coding: utf-8 -*-
"""fetch_shein_sales.py — SHEIN 三店（BS / BD / BAN）在售明细 + 销量，产出面板数据。

数据来源（全部只读 OpenAPI）：
    ① POST /open-api/openapi-business-backend/product/query   商品列表（skcName / spuName / skuCodeList，分页）
    ② POST /open-api/goods/spu-info                           每个 SPU 的款号(supplierCode)、SKU、上架状态、图片
    ③ POST /open-api/goods/query-sku-sales                    按 SKU 的近7天 / 近30天 / 昨日销量
    ④ POST /open-api/stock/stock-query                        线上可用 / 锁定 / 总库存（半托管虚拟库存）

产出：temu-admin/public/data/shein-goods.json（面板「在售款明细」SHEIN 页签直接 fetch）

用法：
    python scripts\\goods\\fetch_shein_sales.py                    # 三店全跑
    python scripts\\goods\\fetch_shein_sales.py --store BD         # 只跑一店
    python scripts\\goods\\fetch_shein_sales.py --no-publish       # 只写 public/data，不发布
    python scripts\\goods\\fetch_shein_sales.py --max-spu 10       # 调试：每店只取 10 个 SPU

⚠ spu-info 的必填第二个字段是 **languageList**（不是 language / skcNameList），
   只传 spuName 会一直报 `020003 不能为空`。2026-11-12 实测。
⚠ 该接口不支持批量（spuNameList 会报错），只能一个 SPU 一次；因此本脚本带本地缓存
   data/shein/spu-cache-<store>.json，日常只补新增 SPU。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = Path(r"C:\Users\saing\.agents\skills\shein-buylabel\scripts")
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

import shein_client as sc  # noqa: E402

CONFIG = Path(r"C:\Users\saing\.agents\skills\shein-buylabel\config.json")
OUT = ROOT / "temu-admin" / "public" / "data" / "shein-goods.json"
CACHE_DIR = ROOT / "data" / "shein"

# 面板店名 → config.json 里的密钥段
STORES = {"BS": "TELALEO", "BD": "BD", "BAN": "BAN", "BE": "BE"}
LABEL = {"BS": "BS 店", "BD": "BD 店", "BAN": "BAN 店", "BE": "BE 店"}

P_PRODUCT = "/open-api/openapi-business-backend/product/query"
P_SPU = "/open-api/goods/spu-info"
P_SALES = "/open-api/goods/query-sku-sales"
P_STOCK = "/open-api/stock/stock-query"
BATCH = 100


def keys(store: str):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    seg = cfg.get("shein_openapi") or {}
    kid, sec, used = sc.get_keys(seg, STORES.get(store, store))
    if not kid:
        raise SystemExit("config.json 里没有 %s（%s）的 openKeyId/secretKey" % (store, STORES.get(store)))
    return kid, sec


def catalog(kid: str, sec: str) -> list[dict]:
    """商品列表：返回 [{skcName, spuName, skuCodeList}]，pageSize 上限 100。"""
    out, page = [], 1
    while True:
        r = sc.signed_post(sc.API_BASE, P_PRODUCT, kid, sec,
                           {"pageNum": page, "pageSize": 100}, retry=2)
        if str(r.get("code")) != "0":
            raise SystemExit("product/query 失败 code=%s msg=%s" % (r.get("code"), r.get("msg")))
        data = (r.get("info") or {}).get("data") or []
        out.extend(data)
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.2)
    return out


def _pick_name(info: dict) -> str:
    for lang in ("zh-cn", "en", ""):
        for it in (info.get("productMultiNameList") or []):
            if (it.get("language") or "") == lang and it.get("productName"):
                return it["productName"]
    for it in (info.get("productMultiNameList") or []):
        if it.get("productName"):
            return it["productName"]
    return ""


def spu_detail(kid: str, sec: str, spu: str) -> dict | None:
    r = sc.signed_post(sc.API_BASE, P_SPU, kid, sec,
                       {"spuName": spu, "languageList": ["zh-cn", "en"]}, retry=3)
    if str(r.get("code")) != "0":
        return None
    info = r.get("info") or {}
    out = {"supplierCode": (info.get("supplierCode") or "").strip(),
           "title": _pick_name(info), "categoryId": info.get("categoryId"), "skcs": []}
    for skc in (info.get("skcInfoList") or []):
        shelf = (skc.get("shelfStatusInfoList") or [{}])[0]
        imgs = skc.get("skcImageInfoList") or []
        img = ""
        for im in imgs:
            if im.get("imageUrl"):
                img = im["imageUrl"]
                break
        skus = []
        for s in (skc.get("skuInfoList") or []):
            price = {}
            for p in (s.get("priceInfoList") or []):
                if p.get("site") in ("shein-us", "US", ""):
                    price = p
                    break
            skus.append({
                "skuCode": s.get("skuCode") or "",
                "supplierSku": s.get("supplierSku") or "",
                "size": (s.get("supplierSku") or "").rsplit("-", 1)[-1],
                "price": price.get("basePrice"),
                "currency": price.get("currency") or "USD",
                "mallState": s.get("mallState"),
            })
        out["skcs"].append({
            "skcName": skc.get("skcName") or "",
            "supplierCode": (skc.get("supplierCode") or "").strip(),
            "image": img,
            "shelfStatus": shelf.get("shelfStatus"),
            "firstShelfTime": shelf.get("firstShelfTime"),
            "skus": skus,
        })
    return out


def load_cache(store: str) -> dict:
    p = CACHE_DIR / ("spu-cache-%s.json" % store)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(store: str, cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = CACHE_DIR / ("spu-cache-%s.json" % store)
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def sales_map(kid: str, sec: str, skus: list[str]) -> tuple[dict, str]:
    out, dt = {}, ""
    for i in range(0, len(skus), BATCH):
        chunk = skus[i:i + BATCH]
        r = sc.signed_post(sc.API_BASE, P_SALES, kid, sec, {"skuCodeList": chunk}, retry=2)
        if str(r.get("code")) != "0":
            print("   ! query-sku-sales 失败 code=%s msg=%s" % (r.get("code"), r.get("msg")))
            continue
        for d in ((r.get("info") or {}).get("dataList") or []):
            out[d.get("skuCode")] = d
            dt = d.get("dt") or dt
        time.sleep(0.2)
    return out, dt


def stock_map(kid: str, sec: str, skus: list[str]) -> dict:
    out = {}
    for i in range(0, len(skus), BATCH):
        chunk = skus[i:i + BATCH]
        r = sc.signed_post(sc.API_BASE, P_STOCK, kid, sec,
                           {"skuCodeList": chunk, "warehouseType": "2", "invType": "VI"}, retry=2)
        if str(r.get("code")) != "0":
            print("   ! stock-query 失败 code=%s msg=%s" % (r.get("code"), r.get("msg")))
            continue
        for g in (r.get("info") or []):
            for inv in (g.get("goodsInventory") or []):
                for s in (inv.get("skuList") or []):
                    out[s.get("skuCode")] = {
                        "usable": s.get("totalUsableInventory") or 0,
                        "total": s.get("totalInventoryQuantity") or 0,
                        "locked": s.get("totalLockedQuantity") or 0,
                    }
        time.sleep(0.2)
    return out


def build_store(store: str, max_spu: int = 0, refresh_cache: bool = False) -> dict:
    kid, sec = keys(store)
    cats = catalog(kid, sec)
    spus = []
    for c in cats:
        if c.get("spuName") and c["spuName"] not in spus:
            spus.append(c["spuName"])
    if max_spu:
        spus = spus[:max_spu]
    print("[%s] 在售 SKC %d 个 / SPU %d 个" % (store, len(cats), len(spus)), flush=True)

    cache = {} if refresh_cache else load_cache(store)
    todo = [s for s in spus if s not in cache]
    print("[%s] spu-info 需拉 %d 个（命中缓存 %d）" % (store, len(todo), len(spus) - len(todo)), flush=True)

    if todo:
        t0 = time.time()
        done = 0
        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = {ex.submit(spu_detail, kid, sec, s): s for s in todo}
            for f in as_completed(futs):
                s = futs[f]
                try:
                    d = f.result()
                except Exception as e:  # noqa: BLE001
                    print("   ! %s 失败：%s" % (s, str(e)[:120]))
                    continue
                if d:
                    cache[s] = d
                done += 1
                if done % 20 == 0:
                    print("   spu-info %d/%d  %.0fs" % (done, len(todo), time.time() - t0), flush=True)
        save_cache(store, cache)
        print("[%s] spu-info 完成，用时 %.0fs" % (store, time.time() - t0), flush=True)

    # 组装：每个 SKC 一条，带款号 / 标题 / SKU
    skc_rows = []
    for c in cats:
        d = cache.get(c.get("spuName")) or {}
        hit = None
        for skc in (d.get("skcs") or []):
            if skc.get("skcName") == c.get("skcName"):
                hit = skc
                break
        if hit is None and (d.get("skcs") or []):
            hit = d["skcs"][0]
        hit = hit or {}
        skc_rows.append({
            "skcName": c.get("skcName") or "",
            "spuName": c.get("spuName") or "",
            "code": (d.get("supplierCode") or hit.get("supplierCode") or "").strip(),
            "title": d.get("title") or "",
            "image": hit.get("image") or "",
            "shelfStatus": hit.get("shelfStatus"),
            "firstShelfTime": hit.get("firstShelfTime") or "",
            "skus": hit.get("skus") or [{"skuCode": s, "supplierSku": "", "size": ""} for s in (c.get("skuCodeList") or [])],
        })

    all_skus = [s["skuCode"] for r in skc_rows for s in r["skus"] if s.get("skuCode")]
    print("[%s] 拉销量 / 库存：%d 个 SKU" % (store, len(all_skus)), flush=True)
    sales, sales_dt = sales_map(kid, sec, all_skus)
    stock = stock_map(kid, sec, all_skus)

    # 按款号聚合（款号缺失时用 SKC 兜底）
    items: dict[str, dict] = {}
    for r in skc_rows:
        code = r["code"] or r["skcName"]
        it = items.setdefault(code, {
            "store": store, "code": code, "skc": [], "title": r["title"], "image": r["image"],
            "firstShelfTime": r["firstShelfTime"], "sizes": [], "skuCount": 0,
            "stock": 0, "sales7": 0, "sales30": 0, "salesYd": 0, "prices": [],
        })
        if r["skcName"] and r["skcName"] not in it["skc"]:
            it["skc"].append(r["skcName"])
        if not it["title"] and r["title"]:
            it["title"] = r["title"]
        if not it["image"] and r["image"]:
            it["image"] = r["image"]
        for s in r["skus"]:
            sku = s.get("skuCode")
            if not sku:
                continue
            sm = sales.get(sku) or {}
            st = stock.get(sku) or {}
            row = {
                "size": s.get("size") or "",
                "supplierSku": s.get("supplierSku") or "",
                "skuCode": sku,
                "skuName": s.get("supplierSku") or sku,
                "sales7": sm.get("c7dSaleCnt") or 0,
                "sales30": sm.get("c30dSaleCnt") or 0,
                "salesYd": sm.get("cydSaleCnt") or 0,
                "salesRt": sm.get("realTimeSaleCnt") or 0,
                "stock": st.get("usable") or 0,
                "stockTotal": st.get("total") or 0,
                "price": s.get("price"),
            }
            it["sizes"].append(row)
            it["skuCount"] += 1
            it["stock"] += row["stock"]
            it["sales7"] += row["sales7"]
            it["sales30"] += row["sales30"]
            it["salesYd"] += row["salesYd"]
            if row["price"]:
                it["prices"].append(row["price"])
    for it in items.values():
        it["prices"].sort()
        it["priceMin"] = it["prices"][0] if it["prices"] else None
        it["priceMax"] = it["prices"][-1] if it["prices"] else None
        it.pop("prices", None)
        it["sizes"].sort(key=lambda x: x["size"])
        it["sizeCount"] = len(it["sizes"])
        it["unmapped"] = not bool(it["code"] != it["skc"][0] if it["skc"] else False)

    store_block = {
        "label": LABEL.get(store, store),
        "counts": {
            "onSaleSkc": len({r["skcName"] for r in skc_rows if r["skcName"]}),
            "spu": len(spus),
            "sku": len(all_skus),
            "style": len(items),
            "stock": sum(i["stock"] for i in items.values()),
            "sales7": sum(i["sales7"] for i in items.values()),
            "sales30": sum(i["sales30"] for i in items.values()),
            "salesYd": sum(i["salesYd"] for i in items.values()),
            "zeroStock": sum(1 for i in items.values() for s in i["sizes"] if s["stock"] == 0),
        },
        "salesDate": sales_dt,
        "items": sorted(items.values(), key=lambda x: (-x["sales7"], x["code"])),
    }
    print("[%s] 款号 %d / SKU %d / 近7天 %d 件 / 线上可用 %d"
          % (store, len(items), len(all_skus), store_block["counts"]["sales7"], store_block["counts"]["stock"]), flush=True)
    return store_block


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SHEIN 三店在售明细 + 销量 → 面板数据")
    ap.add_argument("--store", nargs="+", default=["BS", "BD", "BAN", "BE"])
    ap.add_argument("--max-spu", type=int, default=0, help="调试：每店最多取多少个 SPU")
    ap.add_argument("--refresh-cache", action="store_true", help="忽略 spu 缓存，全量重拉")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-publish", action="store_true")
    a = ap.parse_args(argv)

    data = {"generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"), "platform": "SHEIN",
            "source": "SHEIN OpenAPI：product/query + spu-info + query-sku-sales + stock-query",
            "stores": {}}
    for store in [s.upper() for s in a.store]:
        data["stores"][store] = build_store(store, max_spu=a.max_spu, refresh_cache=a.refresh_cache)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("已写 %s（%.1f KB）" % (out, out.stat().st_size / 1024))

    if not a.no_publish:
        try:
            sys.path.insert(0, str(ROOT / "scripts" / "goods"))
            import publish_admin  # noqa: PLC0415
            publish_admin.publish(["public/data/shein-goods.json"], production=True)
            print("已发布到面板")
        except Exception as e:  # noqa: BLE001
            print("发布失败（数据已写好）：%s" % e)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
