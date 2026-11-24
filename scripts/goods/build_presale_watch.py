# -*- coding: utf-8 -*-
"""构建「修改库存 · 预售」面板数据 → public/data/presale-watch.json
  
用户 2026-11-09 需求：TEMU 运营下加子节点「修改库存-预售」，分两个页签：
  · 预售中   —— 正在预售的「款号-尺码」，带上 SPU、补货日、在途量、开预售时间
  · 已过预售 —— 开到第 7 天自动转进来的记录；**同一尺码满 7 天可重新开预售时清掉**
  一级筛选：TEMU / SHEIN

数据来源
--------
1. 开预售台账（本面板自己的持久化）：`data/presale-ledger.json`
     { "items": [ {store, platform, code, size, spu, qty, date, openedAt, clearedAt} ] }
   —— 开预售成功 / 过期都由这里记录，面板只读它 + 现算状态。
2. 在售款明细 goods.json：款号 → SPU / 商品标题 / TEMU 当前库存
3. （可选）库存跟踪 stock-track.json：7 天日均，用来在面板上提示「建议在途量」

状态口径（用户定的规则）
------------------------
  打开满 7 天（openedAt + 7d ≤ now）→ 从「预售中」移到「已过预售」
  已过预售里，同一「店+款号+尺码」的记录若已满 7 天且平台允许重开 → 视为可清除
  （平台规则：每 SKU 7 天内只允许开启一次）

    python scripts/goods/build_presale_watch.py            # 构建
    python scripts/goods/build_presale_watch.py --print    # 顺便打印统计
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import presale_plan as PP  # noqa: E402

ROOT = Path(r"D:\AI-workplace\TEMU")
DATA = ROOT / "temu-admin" / "public" / "data"
LEDGER = ROOT / "data" / "presale-ledger.json"
#: SHEIN 侧没有预售，只有「清零」：台账来自 shein_stock_sync.record_clear()
CLEAR_LEDGER = ROOT / "data" / "shein-clear-ledger.json"
# TEMU 侧的清零台账（用户 2026-11-12：清零也要进「修改库存-预售」面板）
TEMU_CLEAR_LEDGER = ROOT / "data" / "temu-clear-ledger.json"
#: 后台实读快照（scripts/goods/fetch_presale_live.py 生成；BEL/BHY/KinglamanSport 三店）
LIVE = ROOT / "data" / "presale-live.json"
#: 已开 15 天预售（长时效预售）明细（scripts/goods/fetch_presale15.py 生成）
PRESALE15 = ROOT / "data" / "presale-15d.json"
OUT = DATA / "presale-watch.json"
PRESALE_DAYS = 7

#: SHEIN 的店铺筛选按钮（用户 2026-11-12 指定：BS / BD / BAN / BE）
SHEIN_STORES = ["BS", "BD", "BAN", "BE"]


def _load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except Exception:  # noqa: BLE001
        return default


def load_ledger() -> list:
    led = _load(LEDGER, {"items": []})
    return list(led.get("items") or [])


def goods_index() -> tuple[dict, dict]:
    """(store, code) → item；以及 (store, code) → {size: stock}"""
    g = _load(DATA / "goods.json", {})
    idx, sizes = {}, {}
    for st, s in (g.get("stores") or {}).items():
        for it in s.get("items") or []:
            idx[(st, it.get("code"))] = it
            m = {}
            for sku in it.get("skus") or []:
                m[sku.get("size")] = sku.get("stock")
            sizes[(st, it.get("code"))] = m
    return idx, sizes


def daily_index() -> dict:
    t = _load(DATA / "stock-track.json", {})
    out = {}
    for src in (t.get("sources") or {}).values():
        for st in src.get("styles") or []:
            for v in st.get("variants") or []:
                for r in v.get("rows") or []:
                    out[("%s-%s" % (st["code"], v["variant"]), r["size"])] = float(r.get("daily") or 0)
    return out


def transit_index() -> dict:
    """(款号-套系, 尺码) → 真实在途件数（领星 LM739 的在途，非预售填的量）。

    用户 2026-11-12 指正：面板「在途量」列显示的是**我们给预售填的数量**（196/368…），
    不是仓库真在途（608/819…）。两者意义完全不同 —— 真在途要用领星口径单独出一列。
    来源优先级：linxing-stock.json（全产品）→ stock-track.json（那 14 个款）兜底。
    """
    out = {}
    lx = _load(DATA / "linxing-stock.json", {})
    for x in lx.get("items") or []:
        code = str(x.get("code") or "").strip()
        var = str(x.get("variant") or "").strip()
        size = str(x.get("size") or "").strip()
        if not code or not size:
            continue
        k = ("%s-%s" % (code, var) if var else code, size)
        out[k] = max(out.get(k, 0.0), float(x.get("transit") or 0))
    t = _load(DATA / "stock-track.json", {})
    for src in (t.get("sources") or {}).values():
        for st in src.get("styles") or []:
            for v in st.get("variants") or []:
                for r in v.get("rows") or []:
                    k = ("%s-%s" % (st["code"], v["variant"]), r["size"])
                    if k not in out:
                        out[k] = float(r.get("transit") or 0)
    return out


def prune_ledger() -> int:
    """把「开预售满 7 天」的记录从台账里清掉（用户 2026-11-10：已过预售超过 7 天的自动清理）。

    口径：openedAt + 7 天 ≤ 现在 ⇒ 从台账删除。平台上该 SKU 的预售早已到期，
    面板「已过预售」只看最近 7 天内的；更早的不再保留。
    """
    led = _load(LEDGER, {"items": []})
    items = list(led.get("items") or [])
    now = datetime.now()
    keep, drop = [], []
    for x in items:
        t = parse_dt(x.get("openedAt") or "")
        if t is not None and (now - t) >= timedelta(days=PRESALE_DAYS):
            drop.append(x)
        else:
            keep.append(x)
    if drop:
        LEDGER.write_text(json.dumps({"items": keep}, ensure_ascii=False, indent=1), encoding="utf-8")
        for x in drop:
            print("  清理：%s %s %s（开于 %s）" % (x.get("store"), x.get("code"), x.get("size"), x.get("openedAt")))
    return len(drop)


def parse_dt(s: str):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s)[:19], f)
        except Exception:  # noqa: BLE001
            continue
    return None


def clear_items() -> list:
    """SHEIN「清零」记录（用户 2026-11-12：SHEIN 没有预售，只有清零）。

    来源 data/shein-clear-ledger.json —— 由 run_shein_stock.py --apply 在
    「可售<=1 天 → OVERWRITE 0」成功后写入（带 SPU/仓/日均/清零前后数量）。
    """
    led = _load(CLEAR_LEDGER, {"items": []})
    out = []
    # TEMU 的清零也进同一张表（用户 2026-11-12：清零也要记进「修改库存-预售」面板）
    for src_path, default_plat in ((CLEAR_LEDGER, "SHEIN"), (TEMU_CLEAR_LEDGER, "TEMU")):
        for r in (_load(src_path, {"items": []}).get("items") or []):
            items = [r]
            for r in items:
                online = r.get("onlineNow")
                # 用户 2026-11-12：**已回补（线上又有货）的不要再算"已清零"**，也不要出现在这一页。
                # 台账里仍保留这条（次数、首次清零留着），等它下次又被清零时自然回到列表里。
                if online is not None and online > 0:
                    continue
                plat = str(r.get("platform") or default_plat).upper()
                out.append({
                    "platform": plat,
                    "store": r.get("store"),
                    "code": r.get("code") or "",
                    "variant": r.get("variant") or "",
                    "size": r.get("size") or "",
                    # SHEIN 没有 SPU 概念，用商家SKU 顶上，搜索框还能按它搜；TEMU 填真 SPU
                    "spu": r.get("spu") or r.get("seller") or "",
                    "title": "",
                    "category": "",
                    "qty": 0,
                    "date": "",
                    "openedAt": r.get("clearedAt") or "",
                    "ageDays": None,
                    "status": "已清零",
                    "clearable": False,
                    "temuStock": 0,
                    "daily": float(r.get("daily") or 0),
                    "suggestQty": 0,
                    # —— 清零专用字段 ——
                    "seller": r.get("seller") or "",
                    "sku": r.get("sku") or "",
                    "wh": r.get("wh") or "",
                    "before": r.get("before"),
                    "total": r.get("total"),
                    "days": r.get("days"),
                    "clearedAt": r.get("clearedAt") or "",
                    "firstClearedAt": r.get("firstClearedAt") or r.get("clearedAt") or "",
                    "times": r.get("times") or 1,
                    "confirmed": bool(r.get("confirmed")),
                    "onlineNow": online,
                    "checkedAt": r.get("checkedAt") or "",
                })
    return out


def live_items(gidx: dict, gsizes: dict, tmap: dict | None = None,
               dmap: dict | None = None) -> tuple[list, dict]:
    """后台实读（scripts/goods/fetch_presale_live.py → data/presale-live.json）。

    用户 2026-11-18：「读取三个店铺清零和已经开预售的货号并记录到面板」。
    口径：
      · 预售中 = 该 SKU 的 `enablePreSale` = true（后台弹窗里那个「已开启」开关）
      · 已清零 = 该 SKU 「TEMU 现货库存」= 0，且没有开预售
    **只读数据**，不写任何台账；与台账重复时以台账为准（台账多了在途预售库存/开售时间）。
    """
    d = _load(LIVE, {})
    out = []
    meta = {"generatedAt": d.get("generatedAt") or "", "source": d.get("source") or "",
            "stores": {}}
    seen: set = set()          # 同一 店-货号-尺码 只留一行（多 SKC 同货号会在实读里重复）
    for st, blk in (d.get("stores") or {}).items():
        pre = clr = 0
        for r in blk.get("rows") or []:
            code = str(r.get("code") or "")
            size = str(r.get("size") or "").upper()
            if not code or not size:
                continue
            if (st, code, size) in seen:
                continue
            seen.add((st, code, size))
            it = gidx.get((st, code)) or {}
            base = code.split("-", 1)[1] if "-" in code else code
            cur = (gsizes.get((st, code)) or {}).get(size)
            on = bool(r.get("presaleOn"))
            stock = int(r.get("temuStock") or 0)
            # 在途预售库存 / 补货日期由 refresh_presale_live_fields.py 从「修改库存」弹窗读回
            pqty = int(r.get("presaleQty") or 0)
            pdate = str(r.get("presaleDate") or "")
            daily = float((dmap or {}).get((base, size)) or 0)
            if daily <= 0:
                daily = float(it.get("dailyUse") or 0)   # 兜底：在售款明细里的 7 天日均
            base_row = {
                "platform": "TEMU",
                "store": st,
                "code": code,
                "size": size,
                "spu": r.get("spu") or it.get("spuId") or "",
                "title": it.get("title") or "",
                "category": it.get("category") or "",
                "transit": (tmap or {}).get((base, size)),
                "daily": daily,
                "suggestQty": 0,
                "temuStock": stock if r.get("temuStock") is not None else cur,
                "temuStockRaw": stock if r.get("temuStock") is not None else cur,
                # 商品列表显示的库存里含「在途预售库存」⇒ 现货 = 显示 − 预售
                "temuStockExPresale": max(0, (stock if r.get("temuStock") is not None else int(cur or 0)) - pqty),
                "clearable": False,
                "source": "后台实读",
                "readAt": r.get("readAt") or "",
                # 15 天预售的「最近补货日」：前台用来排序 + 点它跳头程跟踪
                "nextRestock": pdate,
            }
            if on:
                pre += 1
                base_row.update({
                    "qty": pqty,
                    "date": pdate,
                    "openedAt": "",
                    "ageDays": None,
                    "status": "预售中",
                    "planReason": "后台实读：在途预售开关=已开启"
                                  + (("，在途预售库存 %d" % pqty) if pqty
                                     else "（弹窗里的在途预售库存没读到，先按 0 显示）")
                                  + (("，补货日期 %s" % pdate) if pdate else ""),
                })
            elif stock == 0:
                clr += 1
                base_row.update({
                    "qty": 0,
                    "date": "",
                    "openedAt": "",
                    "ageDays": None,
                    "status": "已清零",
                    "planReason": "后台实读：TEMU 现货库存 0 且未开在途预售",
                })
            else:
                continue
            out.append(base_row)
        meta["stores"][st] = {"预售中": pre, "已清零": clr}
    return out, meta


def merge_presale15(items: list, gidx: dict) -> list:
    """把「已开 15 天预售」的明细合进面板（用户 2026-11-19）。

    · 同 (店, 货号, 尺码) 的旧行被**替换**：`data/presale-15d.json` 的补货日期来自弹窗实读，
      比台账/列表推断的准；
    · 新行打 `is15d=True`，`source="15天预售"`，并带 `nextRestock`（最近补货日）、
      `trackKey`（货号-变体-尺码，点它跳头程跟踪）、`trackNode/trackShelf`（最早那票节点/上架日）；
    · 没开开关的尺码（同款其余尺码）不进来，避免把「没预售的尺码」标成预售中。
    """
    p = _load(PRESALE15, {})
    rows = []
    for st, blk in (p.get("stores") or {}).items():
        for r in blk.get("rows") or []:
            if not r.get("on"):
                continue
            code = str(r.get("code") or "")
            size = str(r.get("size") or "").upper()
            it = gidx.get((st, code)) or {}
            base = code.split("-", 1)[1] if "-" in code else code
            rows.append({
                "platform": "TEMU",
                "store": st,
                "code": code,
                "size": size,
                "variant": base,
                "spu": r.get("spu") or it.get("spuId") or "",
                "title": it.get("title") or "",
                "category": it.get("category") or "",
                "qty": int(r.get("qty") or 0),
                "temuStock": r.get("temuStock"),
                "temuStockRaw": r.get("temuStock"),
                "temuStockExPresale": (None if r.get("temuStock") is None
                                       else max(0, int(r["temuStock"]) - int(r.get("qty") or 0))),
                "date": str(r.get("date") or ""),
                "nextRestock": str(r.get("date") or ""),
                "openedAt": "",
                "ageDays": None,
                "status": "预售中",
                "clearable": False,
                "is15d": True,
                "source": "15天预售",
                "trackKey": "%s-%s" % (base, size),
                "trackNode": str(r.get("node") or ""),
                "trackShelf": str(r.get("shelf") or ""),
                "trackAccount": str(r.get("account") or ""),
                "planReason": "15 天预售（长时效）：补货 %s，只能加、不能清/不能关"
                              % (r.get("date") or "未知"),
            })
    if not rows:
        return items
    keys = {(x["store"], x["code"], x["size"]) for x in rows}
    kept = [x for x in items if (x["store"], x["code"], x["size"]) not in keys]
    return kept + rows


def build() -> dict:
    now = datetime.now()
    gidx, gsizes = goods_index()
    dmap = daily_index()
    tmap = transit_index()
    items = []
    for r in load_ledger():
        opened = parse_dt(r.get("openedAt") or "")
        # ⚠ 2026-11-12 用户反馈「已开天数不随天数累加」：原来用 (now - opened).days 是
        #   **24 小时向下取整**（11-10 17:36 开到 11-12 13:02 只算 1 天），面板看着不涨。
        #   改成**自然日差**（今天日期 − 开预售日期）：11-10 开 → 11-12 显示 2 天。
        #   「已过预售 / 可清除」仍按 7×24 小时精确算（平台就是按满 7 天锁的），
        #   所以 11-17 00:00 会显示 7 天，但要到 17:36 才转「已过预售」。
        age = (now.date() - opened.date()).days if opened else None
        code = r.get("code") or ""
        base = code.split("-", 1)[1] if "-" in code else code
        it = gidx.get((r.get("store"), code)) or {}
        cur = (gsizes.get((r.get("store"), code)) or {}).get(r.get("size"))
        daily = dmap.get((base, r.get("size"))) or 0
        # 已过预售 = 开满 7 天；超过 7 天的会在 prune_ledger 里被自动清掉，这里只看 7~14 天
        expired = opened is not None and (now - opened) >= timedelta(days=PRESALE_DAYS)
        items.append({
            "platform": (r.get("platform") or "TEMU").upper(),
            "store": r.get("store"),
            "code": code,
            "size": r.get("size"),
            "spu": r.get("spu") or it.get("spuId"),
            "title": it.get("title") or "",
            "category": it.get("category") or "",
            "qty": r.get("qty"),
            "transit": tmap.get((base, r.get("size"))),
            "date": r.get("date") or "",
            "openedAt": r.get("openedAt") or "",
            "ageDays": age,
            "status": "已过预售" if expired else "预售中",
            # 已过预售里「满 7 天、可重新开预售」⇒ 可以清掉这条
            "clearable": bool(expired),
            "temuStock": cur,
            # ⚠ 2026-11-12 用户指正：开了在途预售之后，TEMU 商品列表那个「库存」会把
            #   **预售填的量算进去**（清零 0 + 预售 368 → 显示 368），面板直接显示它
            #   就等于把「在途预售库存」抄了一遍，看着像没清零。
            #   ⇒ 「TEMU 库存」列改为**剔除预售占用的现货**：max(0, 显示库存 − 在途预售库存)。
            "temuStockRaw": cur,
            "temuStockExPresale": (None if cur is None else
                                   max(0, int(cur) - int(r.get("qty") or 0))),
            "daily": daily,
            "suggestQty": (round(daily * 7) if daily > 0 else 20),
        })
    # SHEIN 侧：只有清零记录，没有预售
    # ⚠ 2026-11-13 用户要求「要不预售中、要不库存清零」：同一 店-款-尺码
    #   既有预售又有清零记录时只保留**预售中**那条，避免面板出现两行。
    _pre = {(x["store"], x["code"], x["size"]) for x in items}
    _cleared = {(x["store"], x["code"], x["size"]) for x in clear_items()}
    items += [x for x in clear_items()
              if (x["store"], x["code"], x["size"]) not in _pre]
    # ★ 2026-11-18 用户：「读取三个店铺清零和已经开预售的货号并记录到面板」
    #   后台实读快照（presale-live.json）补进面板：台账里没有的预售 / 线上 0 且没开预售的清零。
    #   台账已有的同 店-款-尺码 不动（台账还带在途预售库存、开售时间，信息更全）。
    live, live_meta = live_items(gidx, gsizes, tmap, dmap)
    _known = {(x["store"], x["code"], x["size"]) for x in items}
    live_added = [x for x in live if (x["store"], x["code"], x["size"]) not in _known]
    _known |= {(x["store"], x["code"], x["size"]) for x in live_added}
    items += live_added
    # ★ 2026-11-19 用户：「把 BHY 和 BEL 开了 15 天预售的抓取下来并置顶在对应店铺的预售面板；
    #   显示最近的补货日期，点击携带货号-变体-尺码即可跳转到头程跟踪」
    #   ⇒ fetch_presale15.py 抓的明细覆盖同 key 的行（它的补货日期才是弹窗里的真实值），
    #     并打上 is15d 标记；排序时 is15d 排最前、再按补货日期升序。
    items = merge_presale15(items, gidx)
    live15 = {st: len([x for x in items if x["store"] == st and x.get("is15d")])
              for st in ("BEL", "BHY")}
    if any(live15.values()):
        live_meta["presale15"] = live15
    # ★ 2026-11-13 用户要求：按「在售款明细 + 领星总库存」算出今天该清零/该开预售的待办，
    #   直接挂到面板（待清零 / 待开预售），人工照着点。
    for r in PP.build():
        k = (r["store"], r["code"], r["size"])
        # ⚠ 2026-11-24 修：原来只比 _pre/_cleared，漏了刚合并进来的后台实读（live_added）。
        #   后果：实读已开预售的尺码，计划若仍判「待开预售」→ 面板出现两行（踩过：
        #   BHY-2039-4SetA XL 一行 qty30 在库0、一行 qty5 在库26）。
        if k in _pre or k in _cleared or k in _known:
            continue
        items.append({
            "platform": "TEMU",
            "store": r["store"],
            "code": r["code"],
            "size": r["size"],
            "spu": r.get("spu") or "",
            "title": r.get("title") or "",
            "category": "",
            "qty": r["suggestQty"],
            "date": r.get("eta") or "",
            "openedAt": "",
            "ageDays": None,
            "status": "待开预售" if r["action"] == "开预售" else "待清零",
            "clearable": False,
            "temuStock": r.get("temuStock"),
            "temuStockRaw": r.get("temuStock"),
            "temuStockExPresale": r.get("temuStock"),
            "transit": r.get("transit"),
            "daily": r.get("daily") or 0,
            "suggestQty": r["suggestQty"],
            # 计算依据，鼠标悬停/排查用
            "d0": r.get("d0"),
            "leadDays": r.get("leadDays"),
            "availRaw": r.get("availRaw"),
            "subFrom": r.get("subFrom") or "",
            # ★ 2026-11-18 用户：「要看到港时间有没有值，再看有没有被查验」——
            #   在途票 ETA 不可信时（没到港 / 查验未放行 / 卡在港）把原因带出来
            "etaNote": r.get("etaNote") or "",
            "planReason": "在库 %.2f 天（恒信+帕沃尔 %.0f%s）/ 日均 %.2f；在途 %d；ETA %s（%s 天）%s"
                          % (r.get("d0") or 0, r.get("avail") or 0,
                             ("，含" + r["subFrom"]) if r.get("subFrom") else "",
                             r.get("daily") or 0,
                             r.get("transit") or 0, r.get("eta") or "无",
                             "-" if r.get("leadDays") is None else r["leadDays"],
                             ("；上架日不可用：" + r["etaNote"]) if r.get("etaNote") else ""),
        })
    # ★ 2026-11-19：15 天预售的「只能加」大类置顶；15 天预售内部按最近补货日升序
    items.sort(key=lambda x: (x["platform"], 0 if x.get("is15d") else 1, x["status"],
                              x.get("nextRestock") or "9999-12-31",
                              x["code"], x.get("variant") or "", x["size"]))
    by = {"预售中": 0, "已过预售": 0, "已清零": 0, "待开预售": 0, "待清零": 0}
    for x in items:
        by[x["status"]] = by.get(x["status"], 0) + 1
    return {
        "generatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "presaleDays": PRESALE_DAYS,
        "rule": {
            "note": "TEMU：「已开天数」= 今天日期 − 开预售日期（自然日，会天天涨）；"
                    "开预售满 7×24 小时自动进入「已过预售」；同一 SKU 7 天内只允许开启一次，"
                    "满 7 天可重新开预售时该记录可清除。"
                    "SHEIN：半托管没有预售，只有清零（可售<=1 天 → 线上 OVERWRITE 0），"
                    "记录保留最近 60 天，按 店+款+变体+尺码 去重。",
            "openedFrom": str(LEDGER),
            "clearedFrom": str(CLEAR_LEDGER),
        },
        "counts": by,
        "live": live_meta,
        "platforms": ["TEMU", "SHEIN"],
        "stores": sorted({x["store"] for x in items if x.get("store")}),
        "sheinStores": SHEIN_STORES,
        "items": items,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--append", nargs=6, metavar=("STORE", "CODE", "SIZE", "SPU", "QTY", "DATE"),
                    help="把一次开预售记进台账：店 款号 尺码 SPU 在途量 补货日期")
    ap.add_argument("--opened-at", default="", help="开预售时间（默认现在）")
    ap.add_argument("--clear", nargs=3, metavar=("STORE", "CODE", "SIZE"),
                    help="把台账里已过预售、可重开的那条清掉")
    ap.add_argument("--print", action="store_true", dest="do_print")
    a = ap.parse_args()
    led = _load(LEDGER, {"items": []})
    items = list(led.get("items") or [])
    if a.append:
        store, code, size, spu, qty, dt = a.append
        items = [x for x in items if not (x.get("store") == store and x.get("code") == code and x.get("size") == size)]
        items.append({"platform": "TEMU", "store": store, "code": code, "size": size, "spu": spu,
                      "qty": int(qty or 0), "date": dt or "",
                      "openedAt": a.opened_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        print("台账 +1：", store, code, size)
    if a.clear:
        store, code, size = a.clear
        before = len(items)
        items = [x for x in items if not (x.get("store") == store and x.get("code") == code and x.get("size") == size)]
        print("台账 清除 %d 条：%s %s %s" % (before - len(items), store, code, size))
    if a.append or a.clear:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    removed = prune_ledger()
    if removed:
        print("自动清理已过期（>7 天）记录 %d 条" % removed)
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写入:", OUT, "| 预售中 %d / 已过预售 %d / 已清零 %d（SHEIN）" % (
        data["counts"]["预售中"], data["counts"]["已过预售"], data["counts"]["已清零"]))
    if a.do_print:
        for x in data["items"]:
            print("  %-5s %-3s %-22s %-8s %-4s SPU %-12s %-19s %s"
                  % (x["platform"], x["store"], x["code"], x.get("variant") or "",
                     x["size"], x["spu"], x["openedAt"], x["status"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
