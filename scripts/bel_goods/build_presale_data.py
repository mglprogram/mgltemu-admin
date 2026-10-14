# -*- coding: utf-8 -*-
"""把「销量-库存」导成前端静态 JSON（temu-admin/public/data/presale.json）。

三方数据合起来判：哪些尺码现在**没货但马上到货**，可以开预售。

  ① TEMU 销量分析页（紫鸟窗口 CDP，只读）
       → 只取「已售罄 + 即将售罄」的 SKU 行，带**预计未来7日总销量**（每个尺码的预测）
  ② 海外仓库存看板 warehouse-probe
       → 「代发库存」= 已经在海外仓的现货（代发途中不算）
  ③ 飞书多维表格「领星」那几张表
       → 到货计划甘特（按尺码拆的未入仓批次 + 到货日）/ 库存覆盖甘特（空窗、缺口、下一批到货日）

判定（口径在 scripts/power/data/temu_presale.json）：
    可开预售 = 预计未来7日 > 0 且（现货 = 0 或 现货可售天数 < 阈值7）且 在途 > 0 且 有到货日

用法：
  python build_presale_data.py                  # 全量导出
  python build_presale_data.py --stores BHY     # 只导一店
  python build_presale_data.py --offline        # 不连窗口，用上次的 TEMU 行缓存
  python build_presale_data.py --dry-run        # 只打印不写文件
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\AI-workplace\TEMU")
POWER_LIB = ROOT / "scripts" / "power" / "lib"
POWER_DATA = ROOT / "scripts" / "power" / "data"
DATA_DIR = ROOT / "data" / "presale"
API_DIR = ROOT / "temu-admin" / "public" / "data"
OUT_NAME = "presale.json"

sys.path.insert(0, str(POWER_LIB))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # 同目录的兄弟模块（stock_topup_plan 等）
import feishu_bitable as FB  # noqa: E402
import temu_presale as P  # noqa: E402
import temu_stock_topup as T  # noqa: E402
import stock_topup_plan as TOPUP  # noqa: E402  （加库存建议：纯计算，不写任何东西）

SIZE_RE = re.compile(r"([A-Za-z0-9]+)\s*[:：]\s*(\d+)")
SHOP_RE = re.compile(r"^[A-Za-z]{2,4}\d?-")


def _strip_shop(sku: str) -> str:
    """`BS2-202504-5SetA-L` → `202504-5SetA-L`（领星库存总表里每行是一个店铺的 SKU）。"""
    s = (sku or "").strip()
    return SHOP_RE.sub("", s, count=1)


def log(msg: str) -> None:
    print(msg, flush=True)


def today_str() -> str:
    return time.strftime("%Y-%m-%d")


def _n(v) -> str:
    """数字显示成短串（1 位小数，整数不带小数点）。"""
    if v is None:
        return "—"
    f = float(v)
    return str(int(f)) if abs(f - round(f)) < 0.005 else "%.1f" % f


# ------------------------------------------------------------------ 飞书：领星
def load_feishu(cfg: dict, offline: bool) -> dict:
    """读飞书领星表 → {到货计划: {key: [批次]}, 覆盖: {key: {...}}, 上架: {key: 上架时刻}, meta}"""
    fc = cfg.get("飞书") or {}
    base = fc.get("base") or ""
    names = fc.get("表") or {}
    cache = DATA_DIR / "feishu_cache.json"
    ttl = float(fc.get("缓存秒") or 0)
    if offline or (cache.exists() and ttl > 0 and time.time() - cache.stat().st_mtime < ttl):
        if cache.exists():
            obj = json.loads(cache.read_text(encoding="utf-8"))
            log("[飞书] 用缓存（%s 抓的）" % obj.get("fetched_at"))
            return obj
        if offline:
            log("[飞书] offline 且无缓存 → 跳过领星数据")
            return {"到货计划": {}, "覆盖": {}, "上架": {}, "fetched_at": "", "empty": True}

    log("[飞书] 读多维表格 %s" % base)
    out: dict = {"到货计划": {}, "覆盖": {}, "总表": {}, "上架": {},
                 "tables": {}, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        ids = {k: FB.table_id_by_name(base, v) for k, v in names.items()}
    except Exception as e:  # noqa: BLE001
        log("   [warn] 找不到飞书表：%s" % e)
        return {"到货计划": {}, "覆盖": {}, "总表": {}, "上架": {}, "fetched_at": "", "empty": True}
    out["tables"] = ids

    # ① 到货计划甘特：票级 + 按尺码拆
    if "到货计划" in ids:
        recs = FB.list_records(base, ids["到货计划"])
        for r in recs:
            model = FB.text_of(r.get("SKU-型号")).strip()
            if not model:
                continue
            day = FB.ms_to_day(r.get("到货日"))
            arrived = FB.text_of(r.get("是否入仓")).strip() == "已入仓"
            qty = FB.num_of(r.get("数量")) or 0
            sizes = dict((m.group(1), int(m.group(2))) for m in SIZE_RE.finditer(FB.text_of(r.get("尺码明细"))))
            item = {"到货日": day, "件数": qty, "是否入仓": arrived,
                    "批次": FB.text_of(r.get("发货批次")), "渠道": FB.text_of(r.get("渠道")),
                    "入库单号": FB.text_of(r.get("领星入库单号")),
                    "实际入仓日": FB.ms_to_day(r.get("实际入仓日"))}
            for size, n in (sizes or {}).items():
                out["到货计划"].setdefault("%s-%s" % (model, size), []).append(dict(item, 尺码件数=n))
            if not sizes:  # 没拆尺码 → 记到型号级，尺寸匹配不到时兜底
                out["到货计划"].setdefault(model, []).append(dict(item, 尺码件数=None))
        log("   [飞书] 到货计划 %d 条 → %d 个款-尺码键" % (len(recs), len(out["到货计划"])))

    # ② 库存总表：per seller sku 的 代发库存 / 代发途中 / 在途合计（与领星后台同口径）
    if "库存总表" in ids:
        recs = FB.list_records(base, ids["库存总表"])
        for r in recs:
            key = _strip_shop(FB.text_of(r.get("产品名称")) or FB.text_of(r.get("seller sku")))
            if not key:
                continue
            cur = out["总表"].setdefault(key, {"代发库存": 0, "代发途中": 0, "仓库": [],
                                               "库存档位": "", "产品名称": ""})
            cur["代发库存"] += FB.num_of(r.get("代发库存")) or 0
            cur["代发途中"] += FB.num_of(r.get("代发途中")) or 0
            cur["产品名称"] = cur["产品名称"] or FB.text_of(r.get("产品名称"))
            wh = FB.text_of(r.get("仓库"))
            if wh and wh not in cur["仓库"]:
                cur["仓库"].append(wh)
            cur["库存档位"] = FB.text_of(r.get("库存档位")) or cur["库存档位"]
        log("   [飞书] 库存总表 %d 条 → %d 个款-尺码键" % (len(recs), len(out["总表"])))

    # ③ 库存覆盖甘特：空窗/缺口/下一批到货日 + 在途件数（用户看到的那个「在途」）
    if "库存覆盖" in ids:
        recs = FB.list_records(base, ids["库存覆盖"])
        for r in recs:
            key = FB.text_of(r.get("聚合键")).strip()
            if not key:
                continue
            cur = out["覆盖"].setdefault(key, {"在途件数": 0, "空窗天数": None, "缺口件数": None,
                                               "可用库存": None, "下一批到货日": "", "到货日": "",
                                               "风险": "", "日耗": None, "段类型": ""})
            cur["在途件数"] = max(cur["在途件数"], FB.num_of(r.get("在途件数")) or 0)
            # 空窗/缺口可能是空（领星样本不足）—— 空就是「没数据」，不能当 0 显示
            for fld in ("空窗天数", "缺口件数"):
                v = FB.num_of(r.get(fld))
                if v is not None:
                    cur[fld] = v if cur[fld] is None else max(cur[fld], v)
            cur["可用库存"] = FB.num_of(r.get("可用库存"))
            cur["日耗"] = FB.num_of(r.get("日耗"))
            cur["风险"] = FB.text_of(r.get("风险")) or cur["风险"]
            cur["段类型"] = FB.text_of(r.get("段类型")) or cur["段类型"]
            nxt = FB.ms_to_day(r.get("下一批到货日"))
            if nxt and (not cur["下一批到货日"] or nxt < cur["下一批到货日"]):
                cur["下一批到货日"] = nxt
            if FB.text_of(r.get("段类型")) == "到货":
                d = FB.ms_to_day(r.get("段起"))
                if d and (not cur["到货日"] or d < cur["到货日"]):
                    cur["到货日"] = d
        log("   [飞书] 库存覆盖 %d 条 → %d 个款-尺码键" % (len(recs), len(out["覆盖"])))

    # ④ 上架时间（哪些尺码已经上架过）
    if "上架时间" in ids:
        for r in FB.list_records(base, ids["上架时间"]):
            key = FB.text_of(r.get("型号-变体-尺码")).strip()
            if key:
                out["上架"][key] = {"上架时刻": FB.text_of(r.get("上架时刻")),
                                    "上架天数": FB.num_of(r.get("上架天数"))}
        log("   [飞书] 上架时间 %d 条" % len(out["上架"]))

    out["counts"] = {k: len(v) for k, v in out.items() if isinstance(v, dict)}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def arrival_for(key: str, fs: dict, model_key: str = "") -> dict:
    """某「型号-变体-尺码」的在途件数与到货日。

    **在途件数**必须跟用户在飞书里看到的一致 → 优先「领星库存覆盖甘特」的 `在途件数`，
    其次「领星库存总表」的 `代发途中`，最后才用「到货计划甘特」按尺码拆的票级合计。
    （2026-10-12 教训：票级表只覆盖部分批次 —— 202504-5SetA-L 票级只有 141 件，
      而领星在途是 495 件，拿票级当在途会报小 3 倍多。）

    **到货日**只能从「到货计划甘特」的未入仓批次拿（覆盖甘特的下一批到货日兜底），
    并给出「票级覆盖」，让人知道这个日期只解释了多少件在途。
    """
    cov = fs.get("覆盖", {}).get(key) or {}
    tot = fs.get("总表", {}).get(key) or {}
    batches = list(fs.get("到货计划", {}).get(key) or [])
    if not batches and model_key:
        batches = [b for b in (fs.get("到货计划", {}).get(model_key) or []) if b.get("尺码件数") is None]
    pend = [b for b in batches if not b.get("是否入仓")]
    ticket_qty = sum((b.get("尺码件数") if b.get("尺码件数") is not None else b.get("件数") or 0) for b in pend)
    days = sorted(b["到货日"] for b in pend if b.get("到货日"))

    qty, src = 0, ""
    if cov.get("在途件数"):
        qty, src = cov["在途件数"], "领星库存覆盖甘特"
    elif tot.get("代发途中"):
        qty, src = tot["代发途中"], "领星库存总表"
    elif ticket_qty:
        qty, src = ticket_qty, "到货计划票级合计"

    day, day_src = (days[0] if days else ""), ("到货计划甘特" if days else "")
    if not day:
        day = cov.get("下一批到货日") or cov.get("到货日") or ""
        day_src = "库存覆盖甘特" if day else ""
    return {"在途件数": qty, "在途来源": src, "到货日": day, "到货日来源": day_src,
            "票级覆盖": ticket_qty, "票级批次": pend[:5]}


# ------------------------------------------------------------------ TEMU 侧
def tem_store_map() -> dict:
    with (POWER_DATA / "ziniao_power_map.json").open(encoding="utf-8-sig") as f:
        data = json.load(f)
    return {m["power_name"]: m for m in data.get("mapping", []) if m.get("power_platform") == "TEMU"}


def read_temu_rows(store: str, window: str, offline: bool) -> tuple:
    """实时读该店「已售罄 + 即将售罄」的行；失败/离线时退回本地缓存。

    掉登录（NeedAuth）不算异常失败：退回上次结果，并把「需要重新授权」写进来源说明，
    页面上那条 tag 会跟着变色，一眼能看出这份数据是旧的。
    """
    cache = DATA_DIR / ("temu_rows_%s.json" % store)
    auth_note = ""
    if not offline:
        try:
            import ziniao_store  # noqa: WPS433
            port = ziniao_store.probe_port_by_window(window)
        except Exception as e:  # noqa: BLE001
            log("   [warn] 窗口探测失败：%s" % e)
            port = 0
        if port:
            try:
                d = P.read_presale_rows(port, log=log)
                if d["rows"]:
                    DATA_DIR.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(
                        {"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"), "port": port, "rows": d["rows"]},
                        ensure_ascii=False, indent=1), encoding="utf-8")
                    return d["rows"], "紫鸟窗口实时读取（端口 %d，%s）" % (port, time.strftime("%H:%M:%S"))
                auth_note = "页面没读到数据行"
            except P.NeedAuth as e:
                log("   [需授权] %s" % e)
                auth_note = "窗口会话掉了，需重新授权"
            except Exception as e:  # noqa: BLE001
                log("   [warn] 端口 %d 读取失败：%s" % (port, e))
                auth_note = "读取失败：%s" % str(e)[:40]
        else:
            log("   [跳过] 窗口 %s 没开（探不到 CDP 端口）" % window)
            auth_note = "窗口没开"
    if cache.exists():
        obj = json.loads(cache.read_text(encoding="utf-8-sig"))
        note = "上次实时结果（%s）" % obj.get("fetched_at", "?")
        if auth_note:
            note += "｜⚠ %s" % auth_note
        return obj.get("rows") or [], note
    return [], ("⚠ %s（没有可用的历史结果）" % auth_note if auth_note else "")


# ------------------------------------------------------------------ 判定
def judge(row: dict, agg: dict, fs: dict, cfg_t: dict, cfg_p: dict) -> dict:
    rule = cfg_p.get("预售判定") or {}
    thr = float(rule.get("现货可售天数阈值") or 7)
    parsed = T.parse_sku_code(row["sku_code"], cfg_t)
    out = {
        "skuCode": row["sku_code"], "skuId": row.get("sku_id", ""), "size": row.get("size", ""),
        "title": row.get("title", ""), "status": row.get("status", ""),
        "forecast7": row.get("forecast7"), "forecast14": row.get("forecast14"),
        "available": row.get("available"), "availDays": None, "probeSku": "", "factor": 1,
        # 发货模式：卖家自发货 = 后台有「立即加库存」入口；合作仓对接发货 = 库存由合作仓推，卖家加不了
        "shipMode": row.get("ship_mode") or "",
        "probeStockRaw": None, "probeStock": None, "probeTransitRaw": None,
        "dailyNeed": None, "stockDays": None,
        "lingxingKey": "", "lingxingAvailable": None, "lingxingTransit": None,
        "lingxingGapDays": None, "lingxingShortage": None, "lingxingNextArrival": "",
        "lingxingStockGrade": "", "lingxingDailyUse": None, "lingxingRisk": "",
        "lingxingSegType": "",
        "transitRaw": None, "transitSource": "", "transit": None,
        "arrivalDate": "", "arrivalSource": "", "arrivalTicketQty": None, "arrivalBatches": [],
        "arrivalDays": None, "verdict": "", "reason": "", "listedAt": "", "listedDays": None,
        "canPresale": False, "canRestock": False, "actions": [],
    }
    if not parsed["ok"]:
        out.update({"verdict": "跳过", "reason": parsed["为什么"]})
        return out
    conv = T.map_variant(parsed["variant"], cfg_t)
    key = "%s-%s-%s" % (parsed["product"], conv["看板变体"], parsed["size"])
    f = float(conv["factor"] or 1)
    out.update({"probeSku": key, "factor": f, "lingxingKey": key})

    # 现货：看板「代发库存」（已在仓）→ 原始件数 + 折 TEMU 单位
    src = agg.get(key) or {}
    stock_raw = float(src.get("代发库存") or 0)
    transit_raw_probe = float(src.get("代发途中") or 0)
    stock = stock_raw / f
    out["probeStockRaw"] = stock_raw
    out["probeStock"] = round(stock, 2)
    out["probeTransitRaw"] = transit_raw_probe

    cov = fs.get("覆盖", {}).get(key) or {}
    tot = fs.get("总表", {}).get(key) or {}
    out["lingxingAvailable"] = cov.get("可用库存")
    out["lingxingTransit"] = cov.get("在途件数") or tot.get("代发途中")
    out["lingxingGapDays"] = cov.get("空窗天数")
    out["lingxingShortage"] = cov.get("缺口件数")
    out["lingxingNextArrival"] = cov.get("下一批到货日") or cov.get("到货日") or ""
    out["lingxingStockGrade"] = tot.get("库存档位") or ""
    out["lingxingDailyUse"] = cov.get("日耗")
    out["lingxingRisk"] = cov.get("风险") or ""
    out["lingxingSegType"] = cov.get("段类型") or ""
    if key in fs.get("上架", {}):
        out["listedAt"] = fs["上架"][key].get("上架时刻", "")
        out["listedDays"] = fs["上架"][key].get("上架天数")

    # 在途：优先领星 per-SKU 在途（与用户看到的数一致），到货日来自票级
    arr = arrival_for(key, fs, model_key="%s-%s" % (parsed["product"], conv["看板变体"]))
    transit_raw = float(arr["在途件数"] or 0)
    out.update({
        "transitRaw": transit_raw, "transitSource": arr["在途来源"],
        "transit": round(transit_raw / f, 2) if transit_raw else 0,
        "arrivalDate": arr["到货日"], "arrivalSource": arr["到货日来源"],
        "arrivalTicketQty": arr["票级覆盖"] or None,
        "arrivalBatches": arr["票级批次"],
    })

    f7 = float(row.get("forecast7") or 0)
    if f7 > 0:
        out["dailyNeed"] = round(f7 / 7.0, 3)
        out["stockDays"] = round(stock / (f7 / 7.0), 2) if stock > 0 else 0.0

    # 到货日距今天数（负 = 逾期未入仓，表里没更新）
    if out["arrivalDate"]:
        try:
            d0 = time.mktime(time.strptime(today_str(), "%Y-%m-%d"))
            d1 = time.mktime(time.strptime(out["arrivalDate"], "%Y-%m-%d"))
            out["arrivalDays"] = int(round((d1 - d0) / 86400.0))
        except Exception:  # noqa: BLE001
            out["arrivalDays"] = None

    # ---- 两个动作**各自独立判定**（同一个货号可能两个都中）
    transit = out["transit"] or 0
    sold_out = row.get("status") == "已售罄"
    short = stock <= 0 or (out["stockDays"] or 0) < thr          # 仓库现货撑不撑得住
    # TEMU 侧「申报的销售可用库存」撑不撑得住 —— 平台只看这个数，仓库再多货，没申报也卖不出去
    available = row.get("available")
    out["availDays"] = (round(float(available) / (f7 / 7.0), 2)
                        if (available is not None and f7 > 0) else None)
    temu_short = (available is not None and float(available) <= 0) or \
                 (out["availDays"] is not None and out["availDays"] < thr)

    # ① 可以重新加库存：仓里有现货，而 TEMU 这边快断/已断 → 把「销售可用库存」加上去
    #    2026-10-13 修：判据从「仓库现货够不够」改成「TEMU 可售够不够」——
    #    实例 BHY-204203-4SetA-S：仓库 364 件（够 127 天）但 TEMU 只申报了 14 件（够 4.9 天），
    #    平台已标「即将售罄」；按仓库算会漏报，按可售算才对。
    can_restock = stock > 0 and (sold_out or (f7 > 0 and temu_short))
    # ② 可以开预售：仓库现货撑不住（含已断货），但在途有货且知道到货日
    can_presale = f7 > 0 and short and transit > 0 and bool(out["arrivalDate"])

    actions = []
    if can_presale:
        actions.append("开预售")
    if can_restock:
        actions.append("加库存")
    reasons = []
    if can_presale:
        reasons.append("现货 %s 件（折 TEMU %s）只够 %.0f 天，在途 %s 件（折 TEMU %s），%s 到货"
                       % (int(stock_raw), _n(stock), out["stockDays"] or 0,
                          int(transit_raw), _n(transit), out["arrivalDate"]))
    if can_restock:
        if sold_out:
            head = "已售罄"
        elif out["availDays"] is not None:
            head = "TEMU 可售 %s 件只够 %.1f 天" % (_n(available), out["availDays"])
        else:
            head = "TEMU 可售偏低"
        reasons.append("%s，仓里现货 %s 件（够卖 %s 天）→ %s"
                       % (head, int(stock_raw),
                          "—" if out["stockDays"] is None else "%.0f" % out["stockDays"],
                          "直接加库存" if "合作仓" not in (out.get("shipMode") or "")
                          else "合作仓对接发货：得让合作仓推库存，卖家后台没有「立即加库存」入口"))
    if not actions:
        if f7 <= 0:
            reasons.append("预计未来 7 日无销量" + ("，仓里也没货" if stock <= 0 else "，仓里有 %s 件备着" % int(stock_raw)))
        elif transit > 0 and not out["arrivalDate"]:
            reasons.append("在途 %s 件但查不到到货日；现货也不够" % int(transit_raw))
        elif stock > 0:
            reasons.append("TEMU 可售够卖 %s 天、仓里现货够卖 %.1f 天（都 ≥%g 天）"
                           % ("—" if out["availDays"] is None else "%.1f" % out["availDays"],
                              out["stockDays"] or 0, thr))
        else:
            reasons.append("现货 0 件、领星在途 0 → 先补货，开预售也没货可发")

    out["canPresale"] = can_presale
    out["canRestock"] = can_restock
    out["actions"] = actions
    out["reason"] = "；".join(reasons)
    out["verdict"] = ("可开预售" if can_presale else "先加库存" if can_restock
                      else "要补货" if (f7 > 0 and stock <= 0) else "不用开")
    return out


def build_message(payload: dict, link: str = "") -> str:
    """把清单拼成一条飞书纯文本（两店合计）。"""
    stores = payload.get("stores") or []
    rows = [r for s in stores for r in s.get("rows") or []]
    pre = [r for r in rows if r.get("canPresale")]
    res = [r for r in rows if r.get("canRestock")]
    todo = [r for r in rows if r.get("verdict") == "要补货"]
    day = (payload.get("generatedAt") or "")[:10]

    def line(r):
        bits = [r["skuCode"], r.get("status") or ""]
        if r.get("probeStockRaw") is not None:
            bits.append("现货 %s 件" % int(r["probeStockRaw"]))
        if r.get("stockDays") is not None:
            bits.append("够 %.1f 天" % r["stockDays"])
        if r.get("transitRaw"):
            bits.append("在途 %s 件" % int(r["transitRaw"]))
        if r.get("arrivalDate"):
            bits.append("%s 到货" % r["arrivalDate"])
        return "· " + "  ".join(bits)

    out = ["【TEMU 预售 / 加库存提醒】%s" % day,
           "两店「已售罄 + 即将售罄」共 %d 个尺码" % len(rows), ""]
    out.append("▲ 可开预售 %d" % len(pre))
    out += [line(r) for r in pre] or ["  （无 —— 要「现货撑不住 + 在途有货且有到货日」三条同时满足）"]
    out.append("")
    out.append("▲ 可以重新加库存 %d（仓里有货，把 TEMU 可售库存加上去）" % len(res))
    out += [line(r) for r in res] or ["  （无）"]
    # 加库存建议：算清楚加多少、填哪个仓（**不代写**，人去后台点）
    tup = payload.get("topup") or {}
    items = tup.get("items") or []
    plan_rows = [r for r in rows if (r.get("topup") or {}).get("increase")]
    out.append("")
    if plan_rows:
        out.append("▲ 建议加库存 %d 个（目标覆盖 %s 天，上限=海外仓现货；我只算不写，你去后台点）"
                   % (len(plan_rows), tup.get("days") or 30))
        for r in plan_rows:
            t = r["topup"]
            out.append("  → %s %s  可售 %s → %s（加 %s）%s"
                       % (r["skuCode"], r.get("size") or "", _n(r.get("available")),
                          t["target"], t["increase"],
                          ("  建议填「%s」仓" % t["warehouse"]) if t.get("warehouse") else ""))
    else:
        out.append("▲ 建议加库存 0（没有「仓里有货、又没全申报」的尺码）")
    blocked = [r for r in res if (r.get("topup") or {}).get("skip")]
    for r in blocked[:4]:
        out.append("  · %s 不加：%s" % (r["skuCode"], r["topup"]["skip"]))
    if todo:
        out.append("")
        out.append("只能补货 %d 个：%s（现货/在途都为 0）"
                   % (len(todo), "、".join(r["skuCode"] for r in todo[:8])))
    stale = [s for s in stores if s.get("source") and "⚠" in s["source"]]
    if stale:
        out.append("")
        for s in stale:
            out.append("⚠ %s：%s" % (s["store"], s["source"]))
    out.append("")
    src = payload.get("sources") or {}
    out.append("看板 %s / 领星 %s" % ((src.get("看板") or {}).get("builtAt") or "?",
                                     (src.get("飞书") or {}).get("fetchedAt") or "?"))
    if link:
        out.append("明细：%s" % link)
    return "\n".join(out).rstrip()


def _to_min(hm: str) -> int:
    h, m = str(hm).split(":")
    return int(h) * 60 + int(m)


def _slot_due(now_hm: str, slots: list, grace_min: int, sent=()) -> str:
    """当前该发哪一档？返回档位（如 '13:50'）或 ''。

    规则：**时刻已到（含宽限）且今天这一档还没发过**，取最早的那一档。所以：
      · 一档跑多次 → 只有第一次发（后面全是「已发过」）；
      · 提前跑（还没到点）→ 不发，不抢跑；
      · 某档漏跑（机器关机）→ 下一次运行会补发**最早那一档**，不会一次连发两条。
    """
    now = _to_min(now_hm) + max(0, int(grace_min or 0))
    sent_set = {str(s) for s in (sent or [])}
    pending = [s for s in slots if _to_min(s) <= now and s not in sent_set]
    return pending[0] if pending else ""


def notify(payload: dict, cfg_p: dict, force: bool = False) -> str:
    """按配置的「发送时刻」发飞书。返回结果说明。"""
    nc = cfg_p.get("通知") or {}
    if not nc.get("enabled"):
        return "通知未开启"
    today = today_str()
    state_path = DATA_DIR / "notify_state.json"
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            state = {}
    sent_today = list((state.get("sent") or {}).get(today) or [])
    slots = list(nc.get("发送时刻") or [])
    grace = nc.get("宽限分钟") or 5
    now_hm = time.strftime("%H:%M")
    if force:
        slot = now_hm
    else:
        if not slots:
            return "没配发送时刻，跳过"
        due = [s for s in slots if _to_min(s) <= _to_min(now_hm) + max(0, int(grace))]
        if not due:
            return "还没到发送时刻（%s），跳过" % "、".join(slots)
        pending_due = [s for s in due if s not in sent_today]
        if not pending_due:
            nxt = [s for s in slots if _to_min(s) > _to_min(now_hm) + max(0, int(grace))]
            return "今天已发 %s；下一档 %s" % ("、".join(sent_today),
                                            nxt[0] if nxt else "无（今天发完了）")
        slot = pending_due[0]
    text = build_message(payload, (nc.get("明细链接") or ""))
    try:
        FB.send_text(nc["receive_id"], text, nc.get("receive_id_type") or "open_id")
    except Exception as e:  # noqa: BLE001
        log("   [通知] 发送失败：%s" % e)
        return "发送失败：%s" % str(e)[:80]
    if slot not in sent_today:
        sent_today.append(slot)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state.setdefault("sent", {})[today] = sent_today
    state["last_sent_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state["last_slot"] = slot
    state["to"] = nc.get("receive_id")
    # 只保留最近 7 天，别让它无限长
    for d in sorted((state.get("sent") or {}).keys())[:-7]:
        state["sent"].pop(d, None)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return "已发送（%s 档）给 %s" % (slot, nc.get("receive_id"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stores", default="", help="逗号分隔店后缀（默认配置里的 TEMU店）")
    ap.add_argument("--offline", action="store_true", help="不连窗口/不读飞书，只用缓存")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--notify", dest="notify_on", action="store_true", help="强制发飞书（默认按配置）")
    ap.add_argument("--no-notify", dest="notify_on", action="store_false", help="不发飞书")
    ap.add_argument("--force-notify", action="store_true", help="忽略「发送时刻」直接发一条")
    ap.set_defaults(notify_on=None)
    a = ap.parse_args()

    cfg_p = json.loads((POWER_DATA / "temu_presale.json").read_text(encoding="utf-8-sig"))
    cfg_t = T.load_cfg()
    smap = tem_store_map()
    stores = [s.strip() for s in a.stores.split(",") if s.strip()] or (cfg_p.get("TEMU店") or sorted(smap))
    unknown = [s for s in stores if s not in smap]
    if unknown:
        log("!! 未知店后缀：%s" % ",".join(unknown))
        return 2

    # ① 看板（现货）
    alert = {"builtAt": "", "rows": 0, "url": (cfg_t.get("看板") or {}).get("url")}
    agg: dict = {}
    try:
        snap = T.fetch_snapshot(cfg_t, force=False, log=log)
        agg = T.aggregate_by_sku(snap.get("rows") or [])
        alert.update({"builtAt": snap.get("built_at") or "", "rows": len(snap.get("rows") or [])})
    except Exception as e:  # noqa: BLE001
        log("!! 看板取数失败：%s" % e)
        return 3

    # ② 飞书领星（在途 / 到货日）
    fs = load_feishu(cfg_p, a.offline)

    # ③ TEMU（已售罄 / 即将售罄 + 预测）
    out_stores = []
    for s in stores:
        log("[%s] 窗口 %s" % (s, smap[s]["ziniao_window_name"]))
        rows, source = read_temu_rows(s, smap[s]["ziniao_window_name"], a.offline)
        items = [judge(r, agg, fs, cfg_t, cfg_p) for r in rows]
        pre = [i for i in items if i["canPresale"]]
        res = [i for i in items if i["canRestock"]]
        out_stores.append({
            "store": s, "window": smap[s]["ziniao_window_name"], "source": source or "无数据",
            "live": source.startswith("紫鸟窗口实时读取"),
            "rowCount": len(items), "presaleCount": len(pre), "restockCount": len(res),
            "rows": items,
        })
        log("   %d 行 → 可开预售 %d：%s" % (len(items), len(pre),
                                        "、".join(i["skuCode"] for i in pre) or "无"))
        log("           可加库存 %d：%s" % (len(res), "、".join(i["skuCode"] for i in res) or "无"))

    # ④ 加库存建议（纯计算，**不写任何东西** —— 后台「立即加库存」由人手动点）
    topup_items: list = []
    topup_total, topup_days = 0, None
    try:
        for s in out_stores:
            summ = TOPUP.attach(s["rows"], snap.get("rows") or [], cfg_p, store=s["store"])
            topup_items += summ["items"]
            topup_total += summ["total"]
            topup_days = summ["days"]
    except Exception as e:  # noqa: BLE001
        log("!! 加库存建议算失败（不影响其它）：%s" % str(e)[:120])

    payload = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rule": {
            "text": (cfg_p.get("预售判定") or {}).get("_口径") or "",
            "restock": "可以重新加库存 = 仓里有现货 且（TEMU 已售罄 或 TEMU 申报的销售可用库存撑不到阈值）",
            "threshold": (cfg_p.get("预售判定") or {}).get("现货可售天数阈值"),
            "forecast": "预计未来7日总销量",
            "note": "现货 = 海外仓看板「代发库存」（已在仓）；在途 = 领星「库存覆盖甘特」的在途件数",
        },
        "sources": {"看板": alert,
                    "飞书": {"base": (cfg_p.get("飞书") or {}).get("base"),
                             "fetchedAt": fs.get("fetched_at") or "",
                             "counts": fs.get("counts") or {},
                             "empty": bool(fs.get("empty"))}},
        "topup": {"days": topup_days or 30, "total": topup_total, "items": topup_items,
                  "_说明": "加库存建议：目标 = min(30天需求, 海外仓现货)，增加量 = 目标 − TEMU现有可售。"
                          "只算不写 —— 后台「立即加库存」由人手动点。"},
        "stores": out_stores,
    }

    if a.dry_run:
        log("--dry-run：不写文件（但仍可按需发通知，用 --notify）")
        if a.notify_on:
            log("   [通知] %s" % notify(payload, cfg_p, force=a.force_notify))
        return 0
    API_DIR.mkdir(parents=True, exist_ok=True)
    path = API_DIR / OUT_NAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log("  %s  %.1f KB → %s" % (OUT_NAME, path.stat().st_size / 1024, path))

    want_notify = a.notify_on if a.notify_on is not None else bool((cfg_p.get("通知") or {}).get("enabled"))
    if want_notify:
        log("   [通知] %s" % notify(payload, cfg_p, force=a.force_notify))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
