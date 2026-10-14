# -*- coding: utf-8 -*-
"""给「在售款明细」补上**可加库存判定**：TEMU 库存 **< 阈值** 的款·尺码，
拿海外仓库存看板 + 飞书领星到货表来判能不能加。

三方数据（都是只读）：
  ① goods.json 的 lowStock.rows —— TEMU 侧：款号 / 尺码 / 当前可售库存 / 申报价
  ② 海外仓库存看板（warehouse-probe）`代发库存` / `代发途中` / `近7天日均`
     —— 数据以内嵌 `const SNAP = {...}` 形式在静态页里（temu_stock_topup 负责抠出来）
  ③ 飞书多维表格「领星」三张表
     · 领星到货计划甘特 → 未入仓批次（发货批次 / 数量 / 到货日 / 渠道 / 入库单号）
     · 领星库存覆盖甘特 → 可用库存 / 在途件数 / 日耗 / 空窗天数 / 缺口件数 / 风险
     · 领星库存总表     → 代发库存 / 代发途中（看板没有该款时的兜底来源）

判定（阈值与目标天数在 scripts/power/data/temu_restock.json）：
    建议增加量 = round_half_up(日均 × 目标天数) − 仓内现货(折 TEMU 单位) − TEMU 当前可售
       · 日均优先取「覆盖甘特·日耗」，其次「看板·近7天日均」
       · 没日均数据时退化为「仓内现货 > 阈值」就直接判可加
    结论：
      可加库存      仓里现货折成 TEMU 单位后 > 0（并给出建议增加量 / 加后库存）
      待到货        现货 0 但在途 > 0 且有到货日 → 先别加，等到货
      需补货        现货 0 且无在途 → 这条款·尺码仓里没有，得走补货
      未匹配        解析失败，或看板/飞书都没有这个款（给出最接近的看板键供人工确认）

使用 `--clean-void` 时，货号含 `ERROR` 的**作废链接**（用户 2026-10-12：『这是作废的链接』）
会从面板数据里物理删除；不加该开关时只标注为「作废链接」（在售明细里仍能看到它）。

用法：
    python scripts/goods/enrich_restock.py                 # 就地更新 goods.json
    python scripts/goods/enrich_restock.py --dry-run       # 只打印，不写文件
    python scripts/goods/enrich_restock.py --offline       # 不下载看板/不读飞书，只用缓存
    python scripts/goods/enrich_restock.py --clean-void    # 顺带清掉作废链接
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\AI-workplace\TEMU")
POWER_LIB = ROOT / "scripts" / "power" / "lib"
POWER_DATA = ROOT / "scripts" / "power" / "data"
GOODS_JSON = ROOT / "temu-admin" / "public" / "data" / "goods.json"
CFG_PATH = POWER_DATA / "temu_restock.json"

sys.path.insert(0, str(POWER_LIB))
import feishu_bitable as FB            # noqa: E402
import temu_stock_topup as T           # noqa: E402

DEFAULT_CFG = {
    "_说明": "「在售款明细」可加库存判定口径（enrich_restock.py 读）",
    "库存阈值": 10,
    "_库存阈值说明": "TEMU 可售库存 **< 该值** 的款·尺码才进判定清单"
                     "（用户 2026-10-13 修正：严格小于 10，不含 10）",
    "目标天数": 30,
    "_目标天数说明": "与 scripts/power/data/temu_stock_topup.json 的 目标天数 同口径",
    "现货来源优先": ["看板", "总表"],
    "_现货来源说明": "看板(warehouse-probe)是实时快照，领星总表是台账；看板缺该款时用总表兜底",
    #: ⚠「建议加库存」必须与 `run_stock_topup.py` 用**同一个算法**（用户 2026-10-12 要求）：
    #:   仓内现货（只看加州仓）≥ 充足阈值 → 加默认加量；否则 现货×比例（不足保底）；
    #:   且不超过加州仓现货。以前这里是用「全部海外仓合计 + 扣 30 天需求」算的，
    #:   结果出现「加州仓 0 件却建议加 4」这种与加库存口径矛盾的行。
    "加库存": {
        "仓库关键字": ["加州", "kansas"],
        "_仓库关键字说明": ("加量依据的仓库：名字里含任一关键字就计入。"
                              "用户 2026-10-12 修正：加州仓与 kansas 仓**都有货时按两仓之和**算加量；"
                              "加库存动作仍然只落在加州仓"),
        "充足阈值": 150,
        "默认加量": 30,
        "不足比例": 0.2,
        "不足保底": 3,
    },
    "飞书": {
        "base": "JfFEbMOYQa3ImLsjqeHc5Oi4nLe",
        "url": "https://dcnslffawvzl.feishu.cn/base/JfFEbMOYQa3ImLsjqeHc5Oi4nLe",
        "_url说明": "用户 2026-10-12 给的到货表入口；面板「最近到货」悬浮里会给出可点击的核对入口",
        "表": {
            "到货计划": "领星到货计划甘特",
            "库存覆盖": "领星库存覆盖甘特",
            "库存总表": "领星库存总表",
        },
        "缓存秒": 600,
    },
}


def log(msg: str) -> None:
    print(msg, flush=True)


def load_cfg() -> dict:
    """读口径配置；文件不存在就落一份默认值（口径只在 JSON 里改，不动代码）。"""
    if CFG_PATH.exists():
        cfg = json.loads(CFG_PATH.read_text(encoding="utf-8-sig"))
    else:
        cfg = dict(DEFAULT_CFG)
        CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        log("[口径] 已生成默认配置 %s" % CFG_PATH)
    return cfg


def fetch_board_live(cfg_t: dict, log=log) -> dict:
    """**实时**取海外仓看板（绕过 `temu_stock_topup` 的磁盘缓存）。

    ⚠ 2026-10-12 用户指出「数据明显不对」：看板页是**实时**的（代理实测 1091-1K-L
    代发库存 8 = kansas 4 + 加州3 4），而这里是**一小时前**的缓存快照（4 = 只有恒信），
    对着看板页比对当然对不上。补货判断是「要不要现在加库存」，必须用当前库存 ⇒ 强制刷新。
    """
    note = (cfg_t.get("看板") or {}).get("缓存秒")
    log("[看板] 强制实时下载（忽略缓存，原 TTL=%s 秒）" % note)
    return T.fetch_snapshot(cfg_t, force=True, log=log)


# ---------------------------------------------------------------- 飞书
def load_feishu(cfg: dict, offline: bool) -> dict:
    """读飞书三张领星表 → {到货计划, 覆盖, 总表, fetched_at, empty}。"""
    fc = cfg.get("飞书") or {}
    base = fc.get("base") or ""
    names = fc.get("表") or {}
    cache = ROOT / "data" / "goods" / "restock_feishu_cache.json"
    ttl = float(fc.get("缓存秒") or 0)
    empty = {"到货计划": {}, "覆盖": {}, "总表": {}, "fetched_at": "", "empty": True}

    if cache.exists() and (offline or (ttl > 0 and time.time() - cache.stat().st_mtime < ttl)):
        obj = json.loads(cache.read_text(encoding="utf-8"))
        log("[飞书] 用缓存（%s 抓的）" % obj.get("fetched_at"))
        return obj
    if offline:
        log("[飞书] offline 且无缓存 → 跳过")
        return empty

    log("[飞书] 读多维表格 %s" % base)
    out = {"到货计划": {}, "覆盖": {}, "总表": {}, "fetched_at": "", "empty": False}
    try:
        ids = {k: FB.table_id_by_name(base, v) for k, v in names.items()}
    except Exception as e:  # noqa: BLE001
        log("   [warn] 找不到飞书表：%s" % e)
        return empty

    if "到货计划" in ids:
        for r in FB.list_records(base, ids["到货计划"]):
            model = FB.text_of(r.get("SKU-型号")).strip()
            if not model:
                continue
            item = {
                "到货日": FB.ms_to_day(r.get("到货日")),
                "实际入仓日": FB.ms_to_day(r.get("实际入仓日")),
                "是否入仓": FB.text_of(r.get("是否入仓")).strip() == "已入仓",
                "件数": FB.num_of(r.get("数量")) or 0,
                "批次": FB.text_of(r.get("发货批次")),
                "渠道": FB.text_of(r.get("渠道")),
                "入库单号": FB.text_of(r.get("领星入库单号")),
                "尺码明细": FB.text_of(r.get("尺码明细")),
            }
            import re
            sizes = dict((m.group(1), int(m.group(2)))
                         for m in re.finditer(r"([A-Za-z0-9]+)\s*[:：]\s*(\d+)", item["尺码明细"]))
            for size, n in sizes.items():
                out["到货计划"].setdefault("%s-%s" % (model, size), []).append(
                    dict(item, 尺码件数=n))
            if not sizes:
                out["到货计划"].setdefault(model, []).append(dict(item, 尺码件数=None))

    if "库存覆盖" in ids:
        for r in FB.list_records(base, ids["库存覆盖"]):
            key = FB.text_of(r.get("聚合键")).strip()
            if not key:
                continue
            cur = out["覆盖"].setdefault(key, {})
            for f, src_name in (("在途件数", "在途件数"), ("可用库存", "可用库存"),
                                ("日耗", "日耗"), ("缺口件数", "缺口件数"),
                                ("空窗天数", "空窗天数")):
                v = FB.num_of(r.get(src_name))
                if v is None:
                    continue
                if f in ("缺口件数", "空窗天数"):
                    cur[f] = v if f not in cur else max(cur[f], v)
                else:
                    cur[f] = v if f not in cur else max(cur[f], v)
            nxt = FB.ms_to_day(r.get("下一批到货日"))
            if nxt and (not cur.get("下一批到货日") or nxt < cur["下一批到货日"]):
                cur["下一批到货日"] = nxt
            seg = FB.text_of(r.get("段类型"))
            if seg == "到货":
                d = FB.ms_to_day(r.get("段起"))
                if d and (not cur.get("到货日") or d < cur["到货日"]):
                    cur["到货日"] = d
            cur["风险"] = FB.text_of(r.get("风险")) or cur.get("风险", "")

    if "库存总表" in ids:
        for r in FB.list_records(base, ids["库存总表"]):
            name = FB.text_of(r.get("产品名称")) or FB.text_of(r.get("seller sku"))
            key = (name or "").strip()
            if not key:
                continue
            # seller sku 形如 BH-1091-1B-S，产品名称同值 → 统一剥掉店铺前缀
            key = re.sub(r"^[A-Za-z]{2,4}\d?-", "", key)
            cur = out["总表"].setdefault(key, {"代发库存": 0, "代发途中": 0, "库存档位": ""})
            cur["代发库存"] += FB.num_of(r.get("代发库存")) or 0
            cur["代发途中"] += FB.num_of(r.get("代发途中")) or 0
            cur["库存档位"] = FB.text_of(r.get("库存档位")) or cur["库存档位"]
            cur["仓库"] = FB.text_of(r.get("仓库")) or cur.get("仓库", "")

    out["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log("   [飞书] 到货计划 %d 键 · 覆盖 %d 键 · 总表 %d 键"
        % (len(out["到货计划"]), len(out["覆盖"]), len(out["总表"])))
    return out


def pending_batches(key: str, model_key: str, fs: dict) -> list:
    """该款-尺码未入仓的批次（含型号级兜底）。"""
    batches = list((fs.get("到货计划") or {}).get(key) or [])
    if not batches and model_key:
        batches = [b for b in ((fs.get("到货计划") or {}).get(model_key) or [])
                   if b.get("尺码件数") is None]
    return [b for b in batches if not b.get("是否入仓")]


# ---------------------------------------------------------------- 判定
#: 作废链接的货号特征（用户 2026-10-12：『货号带 ERROR 的直接清理，这是作废的链接』）
VOID_CODE_RE = re.compile(r"ERROR", re.I)


def is_void_code(code: str) -> bool:
    return bool(VOID_CODE_RE.search(code or ""))


def warehouse_breakdown(row_agg: dict, factor: float, board_key: str,
                        board_url: str = "") -> list:
    """把「仓内现货」按仓/货源拆开，供前端悬浮显示是哪个仓的货。

    看板每行带 `货源`（帕沃尔 / 恒信…）与 `仓库名称`（帕沃尔·kansas仓 等）：
    用户 2026-10-12 要求「鼠标悬浮时要知道哪些库存数在领星还是帕沃尔」。
    """
    out = []
    for wh, w in ((row_agg or {}).get("按仓") or {}).items():
        out.append({
            "仓库": wh,
            "货源": w.get("货源") or "",
            "代发库存": w.get("代发库存"),
            "代发途中": w.get("代发途中"),
            "代发库存Temu": round(float(w.get("代发库存") or 0) / factor, 2) if factor else None,
            "近7天日均": (row_agg or {}).get("近7天日均"),
        })
    out.sort(key=lambda x: -(x.get("代发库存") or 0))
    if out:
        out.append({"来源": "海外仓库存看板", "看板键": board_key, "url": board_url})
    return out


def judge_row(row: dict, agg: dict, fs: dict, cfg: dict, cfg_t: dict) -> dict:
    """一条低库存（款·尺码）→ 可加库存判定。"""
    sku = row.get("skuCode") or row.get("code") or ""
    out = {
        "verdict": "未匹配", "canRestock": False, "reason": "",
        "factor": 1.0, "boardKey": "", "variantNote": "",
        # ⚠ 字段分工必须分清（2026-10-12 踩过）：
        #   row["stock"] 是 **TEMU 当前可售库存**，绝不能覆盖；
        #   仓内现货写 `whStockRaw`（看板原始件数）/ `whStock`（折 TEMU 单位）。
        #   早先把仓内现货写成 `stock`，`top` 里就冒出「现有218」这种和「库存≤10」矛盾的数
        #   —— 一旦 data 被写回文件，下次运行读回来还会拿它当 TEMU 可售再算一遍（不幂等）。
        "stockSource": "", "whStockRaw": None, "whStock": None,
        "whByWarehouse": [],
        "transitRaw": None, "transit": None, "transitSource": "",
        "inboundConfirmed": 0, "transitNoEta": 0, "canAddMax": 0,
        "preSale": False, "daysToArrival": None,
        "arrivalDate": "", "arrivalBatches": [], "arrivalSource": "",
        "dailyUse": None, "dailySource": "", "boardDaily": None, "lingxingDaily": None,
        "needUnits": None, "suggestAdd": None,
        "afterStock": None, "lingxingAvailable": None, "gapDays": None,
        "shortageUnits": None, "grade": "", "risk": "", "nearKeys": [],
    }
    # ⓪ 作废链接：带 ERROR 的货号直接标出来，不参与判定（也建议清理）
    if is_void_code(sku) or is_void_code(row.get("code") or ""):
        out["verdict"] = "作废链接"
        out["reason"] = ("货号 %s 含 ERROR（作废链接）⇒ 建议在 TEMU 后台清理该商品，"
                         "不参与补货判断" % (sku or row.get("code")))
        return out

    parsed = T.parse_sku_code(sku, cfg_t)
    if not parsed["ok"]:
        out["reason"] = parsed["为什么"]
        return out
    conv = T.map_variant(parsed["variant"], cfg_t)
    key = "%s-%s-%s" % (parsed["product"], conv["看板变体"], parsed["size"])
    model_key = "%s-%s" % (parsed["product"], conv["看板变体"])
    f = float(conv["factor"] or 1)
    out.update({"factor": f, "boardKey": key})

    # ⓪b 原样查不到时兜底：剥掉货号末尾多打的序号（3BHL1→3BHL、1B1→1B、5SetA1→5SetA）
    if key not in agg:
        conv2, note = T.map_variant_fallback(parsed["variant"], cfg_t, agg,
                                             parsed["product"], parsed["size"])
        if conv2:
            conv, key = conv2, "%s-%s-%s" % (parsed["product"], conv2["看板变体"], parsed["size"])
            model_key = "%s-%s" % (parsed["product"], conv2["看板变体"])
            f = float(conv2["factor"] or 1)
            out.update({"factor": f, "boardKey": key, "variantNote": note})

    # ① 仓内现货：看板优先，总表兜底
    stock_raw, src = 0.0, ""
    board_row = None
    for srcname in (cfg.get("现货来源优先") or ["看板", "总表"]):
        if srcname == "看板":
            a = agg.get(key)
            if a is not None and a.get("代发库存") is not None:
                stock_raw, src, board_row = float(a.get("代发库存") or 0), "看板·代发库存", a
                break
        elif srcname == "总表":
            t = (fs.get("总表") or {}).get(key)
            if t is not None:
                stock_raw, src = float(t.get("代发库存") or 0), "领星总表·代发库存"
                break
    out["whStockRaw"], out["stockSource"] = stock_raw, src
    out["whStock"] = round(stock_raw / f, 2)
    out["whByWarehouse"] = warehouse_breakdown(
        board_row, f, key, ((cfg_t.get("看板") or {}).get("url") or ""))

    # ② 在途 / 到货日：覆盖甘特的在途件数 + 到货计划的未入仓批次
    cov = (fs.get("覆盖") or {}).get(key) or {}
    tot = (fs.get("总表") or {}).get(key) or {}
    batches = pending_batches(key, model_key, fs)
    ticket_qty = sum((b.get("尺码件数") if b.get("尺码件数") is not None else b.get("件数") or 0)
                     for b in batches)
    days = sorted(b["到货日"] for b in batches if b.get("到货日"))
    # 在途件数：**优先看板自己的「代发途中」**（与用户在看板页看到的数一致），
    # 其次「领星总表·代发途中」，最后才用票级明细合计。
    # ⚠ 2026-10-12 踩坑：早先把「领星覆盖甘特·在途件数」放第一位，而它在有些款上是 0/缺失，
    #   于是 1091-1K-L 明明看板显示「途中 209」，面板却报「在途 0」，看起来像没货要来。
    board_transit = float(board_row.get("代发途中") or 0) if board_row else 0.0
    transit_raw = float(board_transit or tot.get("代发途中") or ticket_qty or 0)
    out["transitRaw"] = transit_raw
    out["transit"] = round(transit_raw / f, 2) if transit_raw else 0
    out["transitSource"] = ("看板·代发途中" if board_transit
                            else "领星总表·代发途中" if tot.get("代发途中")
                            else "到货计划票级合计" if ticket_qty else "")
    out["arrivalDate"] = days[0] if days else (cov.get("下一批到货日") or cov.get("到货日") or "")
    out["arrivalSource"] = ("领星到货计划甘特（未入仓批次）" if days
                            else "领星库存覆盖甘特·下一批到货日" if out["arrivalDate"] else "")
    out["arrivalBatches"] = [
        {"批次": b.get("批次"), "件数": b.get("尺码件数") if b.get("尺码件数") is not None else b.get("件数"),
         "到货日": b.get("到货日"), "渠道": b.get("渠道"), "入库单号": b.get("入库单号")}
        for b in sorted(batches, key=lambda x: x.get("到货日") or "9999")[:5]
    ]

    # ③ 日均：**优先看板自己的「近7天日均」**，与用户在看板页看到的数一致；
    #    看板没有该款时才退回领星覆盖甘特的「日耗」。
    #    （2026-10-12 教训：早先只认领星日耗，1091-1K-L 用 2.714 算 → 建议加 72，
    #      而看板同款近7天日均只有 1.571；用户对着看板看就会觉得数不对。）
    daily, dsrc = None, ""
    a = agg.get(key) or {}
    if a.get("近7天日均") is not None:
        daily, dsrc = float(a["近7天日均"]) / f, "看板·近7天日均"
    elif cov.get("日耗") is not None:
        daily, dsrc = float(cov["日耗"]) / f, "领星覆盖甘特·日耗"
    out["dailyUse"], out["dailySource"] = (round(daily, 4) if daily is not None else None), dsrc
    out["boardDaily"] = (round(float(a["近7天日均"]) / f, 4)
                         if a.get("近7天日均") is not None else None)
    out["lingxingDaily"] = (round(float(cov["日耗"]) / f, 4)
                            if cov.get("日耗") is not None else None)
    out["lingxingAvailable"] = cov.get("可用库存")
    out["gapDays"] = cov.get("空窗天数")
    out["shortageUnits"] = cov.get("缺口件数")
    out["grade"] = tot.get("库存档位") or ""
    out["risk"] = cov.get("风险") or ""

    cur = float(row.get("stock") or 0)          # TEMU 当前可售
    days_target = float(cfg.get("目标天数") or 30)
    stock_t = stock_raw / f
    #: 仓内现货（看板「代发库存」）= 唯一**现在就能加**的量。
    out["canAddMax"] = int(stock_t)
    out["availableNow"] = int(stock_t)
    #: 加量口径（与 `run_stock_topup.py` 的 `calc_add` 必须一致 —— 用户 2026-10-12 要求）
    #: ⚠ 2026-10-12 用户修正：**加州仓 + kansas 仓都有货时，按两仓之和算加量**
    #:   （加库存动作仍然只落在加州仓，但"能加多少"看两仓合计的现货）。
    tk = (cfg.get("加库存") or {}) if isinstance(cfg.get("加库存"), dict) else {}
    if tk:
        keys = tk.get("仓库关键字") or ["加州", "kansas"]
        if isinstance(keys, str):
            keys = [keys]
        ca_all = 0.0
        for w in out.get("whByWarehouse") or []:
            nm = w.get("仓库") or ""
            if nm and any(k in nm for k in keys):
                ca_all += float(w.get("代发库存") or 0) / (f or 1)
        out["whCa"] = round(ca_all, 2)          # 加州 + kansas 现货（折 TEMU 单位）
    else:
        out["whCa"] = round(stock_t, 2)          # 没配口径时退回「全部仓」
    if daily is not None:
        need = daily * days_target
        out["needUnits"] = round(need, 1)
        if tk:
            # 只按**加州仓**算：≥充足阈值 → 默认加量；否则 现货×比例（不足保底）
            cap = out["whCa"]
            want = (float(tk.get("默认加量") or 30) if cap >= float(tk.get("充足阈值") or 150)
                    else max(float(tk.get("不足保底") or 3),
                             cap * float(tk.get("不足比例") or 0.2)))
            add = min(T.round_half_up(want), int(cap)) if cap > 0 else 0
        else:
            add = max(0, T.round_half_up(min(need - cur, stock_t)))
        out["suggestAdd"] = add
        out["afterStock"] = int(cur) + add
        cover_units = stock_t + cur                       # 全加进去后 TEMU 能卖多少
        out["coverDays"] = (round(cover_units / daily, 1) if daily > 0 else None)
        out["shortDays"] = (round(max(0.0, days_target - cover_units / daily), 1)
                            if daily > 0 else None)
    else:
        # 没有日均 → 不做定量推算，只报「仓里到底有没有货」
        out["suggestAdd"] = None
        out["coverDays"] = None
        out["shortDays"] = None

    if not src:
        # 看板与总表都没有这个款：给出最接近的看板键，方便人工确认是不是变体没映射
        near = [k for k in agg if k.startswith("%s-%s-" % (parsed["product"], conv["看板变体"]))][:5]
        if not near:
            near = [k for k in agg if k.split("-")[0] == parsed["product"]][:5]
        out["nearKeys"] = near
        out["reason"] = ("看板与领星总表都查不到 %s（%s）⇒ 这个款不在海外仓台账里"
                         % (key, parsed["variant"]))
        if near:
            out["reason"] += "；最接近的看板键：%s" % "、".join(near)
        return out

    #: 未入仓批次里**有到货日**的部分才算「已确认会到的货」（飞书到货计划甘特里的才有）
    ticket_confirmed = sum((b.get("尺码件数") if b.get("尺码件数") is not None else b.get("件数") or 0)
                           for b in batches if b.get("到货日")) if batches else 0
    out["inboundConfirmed"] = round(float(ticket_confirmed) / f, 2) if ticket_confirmed else 0
    #: 没有任何到货日/批次信息的在途（看板只给了"代发途中"）
    out["transitNoEta"] = (round(max(0.0, transit_raw - ticket_confirmed) / f, 2)
                           if transit_raw > ticket_confirmed else 0)
    #: 距到货日还有几天（用本地日期算，够判「能不能赶上 7 天预售」）
    out["daysToArrival"] = None
    if out["arrivalDate"]:
        try:
            out["daysToArrival"] = (datetime.strptime(out["arrivalDate"], "%Y-%m-%d").date()
                                    - date.today()).days
        except ValueError:
            out["daysToArrival"] = None

    if stock_t > 0:
        out["canRestock"] = True          # 仓里有货，具备加库存的物理条件
        cover = out.get("coverDays")
        cover_txt = ("；全加进去能卖 %s 天（目标 %g 天，还差 %s 天）"
                     % (cover, days_target, out.get("shortDays"))
                     if cover is not None else "")
        # 在途（还没到仓、或无到货日）单独说清楚，避免被当成"可以加"
        eta_txt = ""
        if out.get("inboundConfirmed"):
            eta_txt = "；另有 %s 件已排到货（%s）" % (out["inboundConfirmed"],
                                                 out.get("arrivalDate") or "日期待确认")
        elif out.get("transitNoEta"):
            eta_txt = ("；另有 %s 件在途但**没有到货日**（飞书到货计划里没有该款-尺码的未入仓批次）"
                       "⇒ 这部分现在加不了，等确认到货再补" % out["transitNoEta"])
        if out["suggestAdd"] is None:
            out["verdict"] = "仓里有货(未定量)"
            out["reason"] = ("仓里现货 %s 件（折 TEMU %s 件），TEMU 现有 %d 件；"
                             "缺日均数据，未做定量推算%s"
                             % (int(stock_raw), out["whStock"], int(cur), eta_txt))
        elif out["suggestAdd"] > 0:
            out["verdict"] = "可加库存"
            out["reason"] = ("仓内现货 %s 件 = **现在能加的上限**，TEMU 现有 %d 件 ⇒ 建议加 %d 件"
                             "（按 %g 天需求 %s 件算，但不超过仓内现货；加后 TEMU %d 件）%s%s"
                             % (int(stock_raw), int(cur), out["suggestAdd"], days_target,
                                out["needUnits"], out["afterStock"], cover_txt, eta_txt))
        else:
            # TEMU 现有库存已经够目标天数，仓里那点不用急着加 → 提示"够卖"，而不是"现货不足"
            out["verdict"] = "库存够卖(不用加)"
            out["reason"] = ("TEMU 现有 %d 件、仓里还有 %s 件；按日均 %s 算%s ⇒ 暂时不用加库存%s"
                             % (int(cur), out["whStock"], out.get("dailyUse"), cover_txt, eta_txt))
    elif transit_raw > 0 and out["arrivalDate"]:
        # 仓内没现货、但飞书到货计划里有**带到货日**的未入仓批次：
        #   · 7 天内到 → TEMU 可以开 7 天预售（用户 2026-10-12：这种别说"干等"，要提示可开预售）
        #   · 超过 7 天 → 仍是「待到货」，并说明赶不上预售窗口
        d2a = out.get("daysToArrival")
        window = int(cfg.get("预售天数") or 7)
        if d2a is not None and 0 <= d2a <= window:
            out["preSale"] = True
            out["verdict"] = "可开预售"
            out["reason"] = ("仓里现货 0、TEMU 现有 %d 件，但 %s 到货 %s 件（还有 %d 天，"
                             "在 %d 天预售窗口内）⇒ 建议**开 7 天预售**先接单，"
                             "等这批到仓再加库存"
                             % (int(cur), out["arrivalDate"], int(out["inboundConfirmed"] or 0),
                                d2a, window))
        else:
            out["verdict"] = "待到货"
            out["reason"] = ("仓里现货 0，在途 %s 件，%s 到货%s ⇒ 到货超出 %d 天预售窗口，"
                             "先开不了预售，等货到再加库存"
                             % (int(transit_raw), out["arrivalDate"],
                                "（还有 %d 天）" % d2a if d2a is not None else "", window))
    elif transit_raw > 0:
        out["verdict"] = "待到货确认"
        out["reason"] = ("仓里现货 0，在途 %s 件但**没有到货日**"
                         "（飞书到货计划里没有该款-尺码的未入仓批次）⇒ 现在加不了，"
                         "得先确认到货日" % int(transit_raw))
    else:
        out["verdict"] = "需补货"
        out["reason"] = "仓里现货 0、在途 0 ⇒ 得先补货"
    return out


def build(goods: dict, agg: dict, fs: dict, cfg: dict, cfg_t: dict, log=log) -> dict:
    """给 goods.json 的每个 lowStock 行补判定，并汇总。

    作废链接（货号含 ERROR）**只是标出来，不剔除**：首页提醒卡与面板明细都读同一份
    「在售中全量」数据，静默删行会让口径对不上。清理这个动作交给 `--clean-void`
    显式执行（或你在 TEMU 后台下架）。
    """
    threshold = int(cfg.get("库存阈值") or 10)
    counts: dict = {}
    void_rows = []
    for store, s in (goods.get("stores") or {}).items():
        rows = ((s.get("lowStock") or {}).get("rows")) or []
        for r in rows:
            r.update(judge_row(r, agg, fs, cfg, cfg_t))
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
            if r["verdict"] == "作废链接":
                void_rows.append("%s %s-%s" % (r["store"], r["code"], r.get("size")))
    # 顶层 rows 是各店 rows 的拷贝，重新拼一份指向同一批对象
    all_rows = [r for s in (goods.get("stores") or {}).values()
                for r in ((s.get("lowStock") or {}).get("rows") or [])]
    all_rows.sort(key=lambda x: (x.get("stock") if x.get("stock") is not None else 0,
                                 x.get("code") or "", x.get("size") or ""))
    restock = [r for r in all_rows if r.get("verdict") == "可加库存"]
    waiting = [r for r in all_rows if r.get("verdict") == "待到货"]

    low = goods.get("lowStock") or {}
    low["rows"] = all_rows
    low["threshold"] = threshold
    low["restock"] = {
        "targetDays": cfg.get("目标天数"),
        "verdictCounts": counts,
        "suggestAddTotal": sum(int(r.get("suggestAdd") or 0) for r in restock),
        "canRestockSkus": [r.get("skuCode") for r in restock],
        "preSaleRows": [{"store": r["store"], "code": r["code"], "size": r.get("size"),
                         "stock": r.get("stock"), "arrivalDate": r.get("arrivalDate"),
                         "daysToArrival": r.get("daysToArrival"),
                         "inboundConfirmed": r.get("inboundConfirmed")}
                        for r in all_rows if r.get("verdict") == "可开预售"],
        "top": [{"store": r["store"], "code": r["code"], "size": r.get("size"),
                 "color": r.get("color"), "stock": r.get("stock"),
                 "whStockRaw": r.get("whStockRaw"), "whStock": r.get("whStock"), "dailyUse": r.get("dailyUse"),
                 "dailySource": r.get("dailySource"),
                 "suggestAdd": r.get("suggestAdd"),
                 "arrivalDate": r.get("arrivalDate")}
                for r in sorted(restock, key=lambda x: -(x.get("suggestAdd") or 0))[:8]],
        "waitingTop": [{"store": r["store"], "code": r["code"], "size": r.get("size"),
                        "transitRaw": r.get("transitRaw"), "arrivalDate": r.get("arrivalDate")}
                       for r in waiting[:8]],
        "sources": {
            "看板": {"builtAt": (goods.get("_board") or {}).get("built_at"),
                     "fetchedAt": (goods.get("_board") or {}).get("fetchedAt"),
                     "url": (goods.get("_board") or {}).get("url"),
                     "live": not bool((goods.get("_board") or {}).get("stale"))},
            "飞书": {"fetchedAt": fs.get("fetched_at"), "empty": bool(fs.get("empty")),
                     "url": (cfg.get("飞书") or {}).get("url") or "",
                     "base": (cfg.get("飞书") or {}).get("base") or ""},
        },
        "rule": ("「可加库存」= 该款该尺码在**加州仓**（看板实时「代发库存」，不含代发途中）有现货；"
                 "建议增加量 = 加州仓现货 ≥ 150 → 30；否则 加州仓现货 × 20%（不足 3 按 3），"
                 "且不超过加州仓现货、不超过「补到 30 天需求」所需；"
                 "只需看日均可算时给量，没日均的只报「仓里有货(未定量)」；"
                 "日均优先「看板·近7天日均」（与看板页一致），看板没有该款才用「领星日耗」；"
                 "仓内现货列显示的是**全部海外仓合计**（= 看板页那一列），悬浮里给逐仓拆分"),
        "voidRows": void_rows,
    }
    # 每店也带一份小汇总，便于面板按店显示
    for store, s in (goods.get("stores") or {}).items():
        rows = ((s.get("lowStock") or {}).get("rows")) or []
        c: dict = {}
        for r in rows:
            c[r["verdict"]] = c.get(r["verdict"], 0) + 1
        s["lowStock"]["restock"] = {
            "verdictCounts": c,
            "suggestAddTotal": sum(int(r.get("suggestAdd") or 0)
                                   for r in rows if r.get("verdict") == "可加库存"),
        }
    goods["lowStockThreshold"] = threshold
    return goods


def clean_void(goods: dict, log=log) -> dict:
    """把「作废链接」（货号含 ERROR）从在售明细与低库存清单里**物理删除**。

    用户 2026-10-12：『货号带 ERROR 的直接清理，这是作废的链接』。
    删的是**面板数据**（goods.json），TEMU 后台那个商品本身要不要下架由用户决定。
    原始导出 xlsx / 抓取 json 一律不动，随时可回放。
    """
    removed = []
    for store, s in (goods.get("stores") or {}).items():
        before = len(s.get("items") or [])
        keep = []
        for it in s.get("items") or []:
            if is_void_code(it.get("code")) or any(is_void_code(k.get("code"))
                                                   for k in (it.get("skus") or [])):
                removed.append("%s %s" % (store, it.get("code")))
                continue
            keep.append(it)
        s["items"] = keep
        low = s.get("lowStock") or {}
        low["rows"] = [r for r in (low.get("rows") or [])
                       if not is_void_code(r.get("code")) and not is_void_code(r.get("skuCode"))]
        if before != len(keep):
            log("   [清理] %s 作废链接 %d 个款" % (store, before - len(keep)))
    # 顺带把总数重算，别让统计停在旧值上
    if removed:
        for s in (goods.get("stores") or {}).values():
            items = s.get("items") or []
            c = s.setdefault("counts", {})
            c["onSaleSkc"] = len(items)
            c["spu"] = len({i.get("spuId") for i in items})
            c["sku"] = sum(len(i.get("skus") or []) for i in items)
            c["stock"] = sum(int(i.get("stock") or 0) for i in items)
            # 低库存的结论分布也要跟着重算（删掉的是 5 行「作废链接」）
            rc = (s.get("lowStock") or {}).get("restock") or {}
            c2: dict = {}
            for r in (s.get("lowStock") or {}).get("rows") or []:
                v = r.get("verdict") or "未判定"
                c2[v] = c2.get(v, 0) + 1
            if rc:
                rc["verdictCounts"] = c2
        tot = goods.setdefault("totals", {})
        tot["onSaleSkc"] = sum(len(s.get("items") or []) for s in goods["stores"].values())
        tot["spu"] = sum(len({i.get("spuId") for i in (s.get("items") or [])})
                         for s in goods["stores"].values())
        tot["sku"] = sum(sum(len(i.get("skus") or []) for i in (s.get("items") or []))
                         for s in goods["stores"].values())
        tot["stock"] = sum(sum(int(i.get("stock") or 0) for i in (s.get("items") or []))
                           for s in goods["stores"].values())
        # 顶层汇总同样重算
        low = goods.get("lowStock") or {}
        # ⚠ 关键：顶层的 `lowStock.rows` 是 build() 时**拷贝出来的一份列表**，
        #   清库时只删了各店的那份 ⇒ 顶层会残留 5 行「作废链接」（2026-10-12 实际踩到）。
        #   这里从各店重拼一遍。
        low["rows"] = [r for s in (goods.get("stores") or {}).values()
                       for r in ((s.get("lowStock") or {}).get("rows") or [])]
        low["rows"].sort(key=lambda x: (x.get("stock") if x.get("stock") is not None else 0,
                                        x.get("code") or "", x.get("size") or ""))
        rct = low.get("restock") or {}
        allc: dict = {}
        for s in (goods.get("stores") or {}).values():
            for r in (s.get("lowStock") or {}).get("rows") or []:
                v = r.get("verdict") or "未判定"
                allc[v] = allc.get(v, 0) + 1
        if rct:
            rct["verdictCounts"] = allc
            rct["voidRows"] = []
            rct["top"] = [t for t in (rct.get("top") or [])
                          if not is_void_code(t.get("code"))]
        # 低库存 summary 也要跟着重算（作废行已经不在 rows 里了）
        sm = low.get("summary") or {}
        if sm:
            sm["sku"] = len(low["rows"])
            sm["style"] = len({(r.get("store"), r.get("code")) for r in low["rows"]})
            sm["zero"] = sum(1 for r in low["rows"] if (r.get("stock") or 0) <= 0)
            sm["buckets"] = [
                {"name": "0", "value": sm["zero"]},
                {"name": "1-4", "value": sum(1 for r in low["rows"]
                                             if 1 <= (r.get("stock") or 0) <= 4)},
                {"name": "5-9", "value": sum(1 for r in low["rows"]
                                             if 5 <= (r.get("stock") or 0) <= 9)},
            ]
            sm["top"] = [t for t in (sm.get("top") or []) if not is_void_code(t.get("code"))]
    return goods


def main() -> int:
    ap = argparse.ArgumentParser(description="补充「可加库存」判定到 goods.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--keep-void", action="store_true",
                    help="保留货号含 ERROR 的作废链接（默认直接清理掉）")
    a = ap.parse_args()

    cfg = load_cfg()
    cfg_t = T.load_cfg()
    void_rows: list = []          # 作废链接条目（build 里填，供 --keep-void 打印）
    log("[口径] 库存阈值 <= %s · 目标天数 %s" % (cfg.get("库存阈值"), cfg.get("目标天数")))

    if not GOODS_JSON.exists():
        log("!! 找不到 %s —— 先跑 build_goods_data.py" % GOODS_JSON)
        return 2
    goods = json.loads(GOODS_JSON.read_text(encoding="utf-8"))
    if goods.get("restockGeneratedAt"):
        # 本脚本**只接受 build_goods_data.py 刚生成的干净 goods.json**：
        # 它会给每行写入 whStock 等字段，重复跑会把上一轮结果当输入（判定会累积走偏）。
        log("[warn] goods.json 已带 restockGeneratedAt（%s）—— 可能已增强过；"
            "建议先跑 build_goods_data.py 再跑本脚本" % goods.get("restockGeneratedAt"))

    # ① 看板（**实时**，不复用缓存 —— 补货判断必须看当前库存）
    agg: dict = {}
    board_meta = {"built_at": "", "url": (cfg_t.get("看板") or {}).get("url")}
    try:
        snap = fetch_board_live(cfg_t, log=log)
        agg = T.aggregate_by_sku(snap.get("rows") or [])
        board_meta = {"built_at": snap.get("built_at") or "", "url": snap.get("_source_url"),
                      "rows": len(snap.get("rows") or []),
                      "fetchedAt": snap.get("_fetched_at") or ""}
    except Exception as e:  # noqa: BLE001
        log("!! 看板取数失败：%s" % str(e)[:160])
        if a.offline:
            log("   （--offline：退回缓存）")
            try:
                snap = T.fetch_snapshot(cfg_t, force=False, log=log)
                agg = T.aggregate_by_sku(snap.get("rows") or [])
                board_meta = {"built_at": snap.get("built_at") or "",
                              "url": snap.get("_source_url"),
                              "rows": len(snap.get("rows") or []),
                              "stale": True}
            except Exception as e2:  # noqa: BLE001
                log("!! 缓存也读不到：%s" % str(e2)[:120])

    # ② 飞书
    fs = load_feishu(cfg, a.offline)

    if not agg and not (fs.get("总表")):
        log("!! 看板与飞书都没数据 → 不做判定，保留原 goods.json")
        return 3

    goods["_board"] = board_meta
    goods = build(goods, agg, fs, cfg, cfg_t)
    goods.pop("_board", None)
    goods["restockGeneratedAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    void_rows = ((goods.get("lowStock") or {}).get("restock") or {}).get("voidRows") or []    # 作废链接（货号含 ERROR）默认清理：用户 2026-10-12「直接清理，这是作废的链接」。
    # 想留着看就加 --keep-void。原始导出/抓取文件在任何情况下都不动。
    if not a.keep_void:
        goods = clean_void(goods)
    elif void_rows:
        log("[作废链接] 保留（--keep-void）：%s" % "、".join(void_rows))

    cnt = (goods.get("lowStock") or {}).get("restock", {}).get("verdictCounts", {})
    log("[判定] " + " · ".join("%s %s" % (k, v) for k, v in sorted(cnt.items())))
    top = ((goods.get("lowStock") or {}).get("restock") or {}).get("top") or []
    for t in top[:8]:
        log("   · %s %s-%s 现有%d 仓内%s 日均%s → 建议加 %s"
            % (t["store"], t["code"], t["size"], t["stock"], t["whStockRaw"],
               t.get("dailyUse"), t["suggestAdd"]))

    if a.dry_run:
        log("--dry-run：不写文件")
        return 0
    GOODS_JSON.write_text(json.dumps(goods, ensure_ascii=False, indent=1), encoding="utf-8")
    log("已更新 %s (%.0f KB)" % (GOODS_JSON, GOODS_JSON.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
