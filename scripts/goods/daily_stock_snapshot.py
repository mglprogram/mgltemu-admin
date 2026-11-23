# -*- coding: utf-8 -*-
"""每日 06:30 的「BHY/BEL 库存快照」：领星库存 → 恒信仓加 1/2、清零、待开预售、已开预售。

用户 2026-11-20：
    「每天定时 6.30 加 BHY 和 BEL 的领星库存加 1/2 库存、在恒信仓，和清零和要开预售和
      已开预售的库存数量，都更新到面板，现在只拿 BHY 店铺测试，要开预售的产品先让我检查再做决定。」

口径（**只算数，绝不写后台**）：
    · 领星库存 = `linxing-stock.json`（LM739 恒信）的 `avail` / `transit` / `daily`
    · 恒信仓 1/2  = ⌈领星可用 ÷ 2⌉（用户原话「领星库存加 1/2 库存在恒信仓」）
      —— 先把领星那份数的**一半**挂到 TEMU 的「恒信加州3仓」，剩下的留给别处
    · 清零 / 待开预售 / 已开预售 走 v4 口径（`plan_v4`，可售 ≤2 天 + 上架 ≤6 天 + 节点合格）
    · **前置闸门**：只处理 `--stores` 里点名的店（现在只放 BHY）；BEL 只出数不落盘

产物：
    data/stock-snapshot/YYYY-MM-DD.json   当天快照（每店每 SKU：领星可用/一半/线上/开关/在途预售/分类）
    temu-admin/public/data/stock-snapshot.json   面板数据（含「待开预售需人工确认」标记）
    data/stock-snapshot/pending-presale.json     待你确认的清单（confirm/reject 后这里会变）

用法：
    python scripts/goods/daily_stock_snapshot.py --dry-run          # 只看不写
    python scripts/goods/daily_stock_snapshot.py                    # 写快照 + 面板数据
    python scripts/goods/daily_stock_snapshot.py --stores BEL       # 换店（默认 BHY）
    python scripts/goods/daily_stock_snapshot.py --confirm-status   # 看待确认清单
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power"))

DATA = ROOT / "temu-admin" / "public" / "data"
SNAP_DIR = ROOT / "data" / "stock-snapshot"
PANEL_OUT = DATA / "stock-snapshot.json"
PENDING = SNAP_DIR / "pending-presale.json"
LIVE = ROOT / "data" / "presale-live.json"

#: 三店都跑（用户 2026-11-23：「A：把 BEL、KLS 也加入每天循环（三店都跑）」）
DEFAULT_STORES = ("BHY", "BEL", "KLS")

#: 业务简称 → goods.json / 快照里用的店名（goods.json 里 KLS 叫 KinglamanSport）
STORE_ALIAS = {"KLS": "KinglamanSport"}


def canon(store: str) -> str:
    return STORE_ALIAS.get(str(store).upper(), store)


def _code_map() -> dict:
    """KLS 这类未进 goods.json 的店：SPU → 货号（来自 `_logs/kls_spu_code.json`）。"""
    try:
        return json.loads((ROOT / "_logs" / "kls_spu_code.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _store_items(store: str, goods: dict) -> list:
    """该店在售中的款；goods.json 里没有的店（KLS 等）用 SPU→货号映射兜底。

    KLS 的 goods.json 没有 skus 明细，尺码从领星那边反查（见下方 build 里的 `sizes` 分支）。
    返回统一结构：{spuId, code, skus:[{size, stock}]}，skus 可能为空。
    """
    items = ((goods.get("stores") or {}).get(canon(store)) or {}).get("items") or []
    live = [it for it in items if it.get("status") == "在售中"]
    if live:
        return live
    cm = _code_map() if canon(store) == "KinglamanSport" else {}
    return [{"spuId": spu, "code": code, "skus": [], "status": "在售中"}
            for spu, code in sorted(cm.items())]



def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def linxing_index() -> dict:
    """(款-套系大写, 尺码) → {avail, transit, daily}（恒信 LM739）。"""
    idx: dict = {}
    for x in (_load(DATA / "linxing-stock.json").get("items") or []):
        code = str(x.get("code") or "").strip()
        var = str(x.get("variant") or "").strip()
        size = str(x.get("size") or "").upper()
        if not code or not size:
            continue
        k = (("%s-%s" % (code, var)).upper(), size)
        cur = idx.setdefault(k, {"avail": 0.0, "transit": 0.0, "daily": 0.0})
        cur["avail"] += float(x.get("avail") or 0)
        cur["transit"] = max(cur["transit"], float(x.get("transit") or 0))
        cur["daily"] = max(cur["daily"], float(x.get("daily") or 0))
    return idx


def live_index(store: str) -> dict:
    """(店) → {(货号, 尺码): 实读行}"""
    out: dict = {}
    d = _load(LIVE)
    for r in ((d.get("stores") or {}).get(store) or {}).get("rows") or []:
        out[(str(r.get("code") or ""), str(r.get("size") or "").upper())] = r
    return out


def _plan_by_store() -> dict:
    """v4 计划（只算数）。返回 {店: {(货号, 尺码): plan 行}}"""
    import plan_v4 as V4  # noqa: PLC0415
    out: dict = {}
    for row in V4.build():
        out.setdefault(row["store"], {})[(row["code"], row["size"].upper())] = row
    return out


def build(stores: tuple) -> dict:
    lx = linxing_index()
    plans = _plan_by_store()
    goods = _load(DATA / "goods.json")
    now = datetime.now()
    result = {"generatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
              "date": str(date.today()), "stores": {}, "rule": {}}
    for store in stores:
        live = live_index(canon(store))
        rows = []
        skus = []
        for it in _store_items(store, goods):
            code = str(it.get("code") or "")
            base = code.split("-", 1)[1] if "-" in code else code
            sku_list = it.get("skus") or []
            if not sku_list:
                # 没有 skus 明细的店（KLS）：尺码与库存从领星反查
                got = {k[1]: d for k, d in lx.items()
                       if k[0].startswith(base.upper() + "-")}
                sku_list = [{"size": sz, "stock": 0} for sz in sorted(got)]
            for sku in sku_list:
                size = str(sku.get("size") or "").upper()
                k = (base.upper(), size)
                l = lx.get(k) or {}
                avail = float(l.get("avail") or 0)
                daily = float(l.get("daily") or 0)
                # 恒信仓 = 领星可用的一半（向上取整；0 就是 0）
                half = int(math.ceil(avail / 2.0)) if avail > 0 else 0
                lv = live.get((code, size)) or {}
                plan = (plans.get(store) or {}).get((code, size)) or {}
                action = plan.get("action") or ""
                if lv.get("presaleOn"):
                    cls = "已开预售"
                elif action.startswith("不动"):
                    cls = "不动（已开15天预售）"
                elif action == "开预售":
                    cls = "待开预售"
                elif action == "只清零":
                    cls = "待清零"
                elif float(sku.get("stock") or 0) == 0:
                    cls = "已清零"
                else:
                    cls = "正常"
                rows.append({
                    "store": store, "code": code, "variant": base, "size": size,
                    "spu": it.get("spuId") or lv.get("spu") or "",
                    "title": it.get("title") or "",
                    "lxAvail": round(avail, 1), "lxTransit": int(float(l.get("transit") or 0)),
                    "daily": round(daily, 2),
                    "hxHalf": half,
                    "temuStock": sku.get("stock"),
                    "presaleOn": bool(lv.get("presaleOn")),
                    "presaleQty": int(lv.get("presaleQty") or 0),
                    "presaleDate": str(lv.get("presaleDate") or ""),
                    "class": cls,
                    "planAction": action,
                    "planQty": plan.get("qty") or 0,
                })
                skus.append(cls)
        cnt: dict = {}
        for c in skus:
            cnt[c] = cnt.get(c, 0) + 1
        result["stores"][canon(store)] = {
            "skc": len({r["code"] for r in rows}), "sku": len(rows), "counts": cnt,
            "rows": rows,
        }
    result["rule"] = {
        "恒信仓一半": "ceil(领星可用 / 2) 挂到 TEMU「恒信加州3仓」；另一半留给别处",
        "分类": "已开预售 > 不动(15天) > 待开预售(v4) > 待清零 > 已清零 > 正常",
        "门槛": "v4：可售 ≤2 天 且 预测上架 ≤6 天 且 最新节点合格（提柜/提重柜/待预约送仓）",
        "前置闸门": "只有 --stores 点名的店才落盘；当前 %s（BEL 只出数，等 BHY 测通）" % ",".join(stores),
        "确认": "待开预售的行**不会**自动开，写在 pending-presale.json 等人工确认",
    }
    return result


def _merge_stores(data: dict) -> dict:
    """把本次算的店铺并进已有快照，而不是覆盖掉别的店。

    ⚠ 2026-11-21 修：以前 `--stores BHY` 会把文件写成只剩 BHY，
      之后按 BEL 跑就 0 个货号（pipeline 里三店分片跑必踩）。
    """
    day = data.get("date") or str(date.today())
    p = SNAP_DIR / ("%s.json" % day)
    old = _load(p)
    merged = dict(old)
    merged.update({k: v for k, v in data.items() if k != "stores"})
    stores = dict(old.get("stores") or {})
    stores.update(data.get("stores") or {})
    merged["stores"] = stores
    return merged


def write_outputs(data: dict) -> None:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    data = _merge_stores(data)
    (SNAP_DIR / ("%s.json" % data["date"])).write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    # 面板数据：历史快照（保留最近 14 天）+ 今天明细
    hist = []
    for p in sorted(SNAP_DIR.glob("*.json"))[-14:]:
        d = _load(p)
        if d.get("date"):
            hist.append({"date": d["date"], "generatedAt": d.get("generatedAt"),
                         "stores": {st: {"counts": (blk or {}).get("counts") or {},
                                         "sku": (blk or {}).get("sku") or 0}
                                    for st, blk in (d.get("stores") or {}).items()}})
    panel = dict(data)
    panel["history"] = hist
    panel["pending"] = [r for st, blk in (data.get("stores") or {}).items()
                        for r in blk.get("rows") or [] if r.get("class") == "待开预售"]
    PANEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    PANEL_OUT.write_text(json.dumps(panel, ensure_ascii=False, indent=1), encoding="utf-8")
    old = _load(PENDING)
    PENDING.write_text(json.dumps({
        "generatedAt": data["generatedAt"],
        "note": "待开预售清单：人工确认后再执行；status 由 confirm_presale.py 改",
        "items": [{"store": r["store"], "code": r["code"], "size": r["size"],
                   "suggestQty": r["planQty"], "lxAvail": r["lxAvail"], "daily": r["daily"],
                   "status": (next((o.get("status") for o in old.get("items") or []
                                    if o.get("store") == r["store"] and o.get("code") == r["code"]
                                    and o.get("size") == r["size"]), "pending"))}
                  for r in panel["pending"]],
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="每日 06:30 库存快照（只算数，不写后台）")
    ap.add_argument("--stores", default=",".join(DEFAULT_STORES))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm-status", action="store_true")
    a = ap.parse_args()
    if a.confirm_status:
        d = _load(PENDING)
        items = d.get("items") or []
        print("待开预售清单（%s）共 %d 条：" % (d.get("generatedAt"), len(items)))
        for it in items:
            print("  %-5s %-24s %-4s 建议填 %-4s 领星可用 %-6s 日均 %-6s %s"
                  % (it["store"], it["code"], it["size"], it["suggestQty"],
                     it["lxAvail"], it["daily"], it["status"]))
        return 0
    stores = tuple(s.strip() for s in a.stores.split(",") if s.strip())
    data = build(stores)
    for st, blk in data["stores"].items():
        print("[%s] SKU %d / 货号 %d｜%s" % (st, blk["sku"], blk["skc"],
                                            "，".join("%s %d" % kv for kv in sorted(blk["counts"].items()))))
    if a.dry_run:
        print("--dry-run：不写文件")
        return 0
    write_outputs(data)
    print("已写：%s" % (SNAP_DIR / ("%s.json" % data["date"])))
    print("已写：%s" % PANEL_OUT)
    print("已写：%s" % PENDING)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
