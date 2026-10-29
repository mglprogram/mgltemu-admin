#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_stock_track.py — 「库存跟踪」：九部 + 十部，6 个款号逐尺码库存。

用户需求
--------
2026-10-28：每天 17:00 跟踪这 6 个款号**每个尺码**的库存
    204203 / 204207 / 204004 / 103903 / 103902 / 103901
挂到运营后台 → 「库存跟踪」页。
2026-10-28 追加：十部（warehouse-probe）也要跟踪同样这 6 个款。
2026-10-29 改期：**17:00 → 17:30**（用户要求）。两个看板仍在 17:00 自己重算，
    所以 17:30 开始跑时通常已经是当天数据；`--wait-fresh` 保留作兜底。

两个数据源
----------
① 九部（半托管 · TTSA-9）
   `https://cbec756b1bad42778134c59af1ab67ce.app.workbuddy.host/`
   静态页，数据内嵌在 `const INV`：
     INV.rows 588 行 × 13 列（SKU/代发途中/代发库存/七天日均/海外仓库存/
     可售天数/补货数量/未交货订单/…/库存状态/预计断货日×2）
     INV.whn / whv / bwv  仓库名 + 逐仓代发库存 / 逐仓代发途中
   这页**没有分页**，一次内嵌 588 行 ⇒ 直接解析快照（比读 DOM 稳且快）。

② 十部（帕沃尔 + 恒信）
   `https://warehouse-probe.app.workbuddy.host/`
   同样是静态页，数据在 `const SNAP`：
     SNAP.rows 2203 行，每行 = 「货源 × 店铺 × 仓库 × 小款号」，字段：
       货源 / 店铺 / 小款号 / 产品名称 / 仓库名称 / 货源仓
       代发途中 / 代发库存 / 10天销量 / 未交货订单数 / 近7天日均
     ⚠ 这一页**按货源×店铺×仓库拆成了多行**，同一 SKU 会有 3~6 行
       （如 H103903-2B-L 就有 BS/BH/BK 三店 × 多仓 共 6 行）。
     ⚠ `SNAP.key_level_fields = ['未交货订单数','近7天日均']` —— 这两个是
       **款级**值，在每一行上重复出现。把它们相加会把销量放大 6 倍
       （这是本页最容易犯的错，已实测确认）。所以：
         代发库存 / 代发途中 / 10天销量  → 求和
         未交货订单数 / 近7天日均        → 取一次（不求和）

两个源的字段差异
----------------
十部**没有**「可售天数 / 补货数量 / 库存状态」这三列，本脚本按九部口径自算，
并在 JSON 里用 `derived: true` 标出来（页面上也会注明"本页自算"），
免得把自算值当成看板原值。

产出
----
    temu-admin/public/data/stock-track.json   前端直接 fetch
    data/stock-track/history.json             两部的每日快照
    out/stock-track/raw-nine.html / raw-ten.html   原始页，出问题可复查

用法
----
    python scripts/goods/fetch_stock_track.py                # 抓两个源
    python scripts/goods/fetch_stock_track.py --source nine  # 只抓九部
    python scripts/goods/fetch_stock_track.py --print        # 顺便打表

退出码
------
    0 成功（至少一个源有数据）；2 两个源都抓失败；
    3 解析到 0 行（口径变了）；4 落盘失败
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_JSON = ROOT / "temu-admin" / "public" / "data" / "stock-track.json"
HISTORY = ROOT / "data" / "stock-track" / "history.json"
RAW_DIR = ROOT / "out" / "stock-track"

URL_NINE = "https://cbec756b1bad42778134c59af1ab67ce.app.workbuddy.host/"
URL_TEN = "https://warehouse-probe.app.workbuddy.host/"

#: 要跟踪的款号（顺序＝页面展示顺序）。
#: 204003 是 2026-10-28 用户追加的；其余 6 个是当天上午给的。
STYLES = ["204203", "204207", "204004", "204003", "103903", "103902", "103901"]

#: 十部（warehouse-probe）里要丢掉的「脏行」：{款号: {变体, 店铺}}。
#:
#: 为什么需要：十部那页是「帕沃尔 + 恒信」两个源按「货源 × 店铺 × 仓库」拼出来的，
#: 源表里残留了零星的脏 SKU。实测 2026-10-28：
#:   `T103903-2BBh-M` 只有一行、店铺 BLA、只有一个 M 码，
#:   库存 / 在途 / 10天销量 / 未交货订单 / 日均 **全是 0**。
#: 它与真正的 `103903-2B*`（H 前缀、BH/BK/BS 三店、5 个码全）不是同一款，
#: 留着只会让「103903」下面多出一个只有 0 的空列 —— 用户 2026-10-28 要求删除。
#:
#: ⚠ 只对**十部**生效。九部的 `2BBh` 是完整 5 个码（有真实在途 104 件），不是脏数据，
#:   绝不能一起删掉。
TEN_DROP_ROWS = {
    "103903": [{"variant": "2BBh", "shops": {"BLA"}}],
}

VARIANT_NOTE = {
    "4SetA": "4件套A", "4SetB": "4件套B", "4SetC": "4件套C",
    "5SetA": "5件套A", "5SetB": "5件套B", "5SetC": "5件套C",
    "5B": "5件套B", "1B": "1件套B",
    "2BBh": "2件套B(黑)", "2BH": "2件套B(黑)", "2H": "2件套H", "2L": "2件套L",
    "2B": "2件套B",
    "4B": "4件套B",
    "5BBBBhBh": "5件套(黑黑黑白黑)",
    "5BBBBhBh_merged": "5件套(黑黑黑白黑)",
}

SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL", "5XL"]
HISTORY_DAYS = 90
GAP_SIZES = SIZE_ORDER


# --------------------------------------------------------------------------
# 抓页面（两个源共用）
# --------------------------------------------------------------------------
def fetch_html(url: str, timeout: float = 60.0) -> str:
    """直连抓取（**绕开系统代理**）。

    本机系统代理 127.0.0.1:10808 会让这条链路偶发 `WinError 10060`
    （2026-10-28 实测撞到一次，紧接着再抓就成功）。
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ctx),
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def extract_json_object(html: str, marker: str) -> dict:
    """抠出 `const XXX = {...}` 里那个大对象（括号配平 + 跳过字符串）。"""
    i = html.find(marker)
    if i < 0:
        raise ValueError("页面里找不到 `%s`（看板结构变了？）" % marker)
    i += len(marker)
    while i < len(html) and html[i] in " \r\n\t":
        i += 1
    if i >= len(html) or html[i] != "{":
        raise ValueError("`%s` 后面不是 JSON 对象（实际 %r）" % (marker, html[i:i + 20]))
    depth, in_str, esc = 0, False, False
    for j in range(i, len(html)):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(html[i:j + 1])
    raise ValueError("`%s` 的括号没闭合（页面被截断？）" % marker)


# --------------------------------------------------------------------------
# SKU 解析（两个源的命名规则不一样，这里做归一）
# --------------------------------------------------------------------------
#: 款号按长度从长到短匹配，避免 `103901` 抢了更长款号的前缀
_STYLES_BY_LEN = sorted(STYLES, key=len, reverse=True)


def split_sku(sku: str):
    """把 `款号-变体-尺码` 拆成 (纯数字款号, 变体, 尺码)；拆不出返回 None。

    两个源的 SKU 写法不一样，都要能吃：

        九部 `204203-4SetA-L`        → (204203, 4SetA, L)
        九部 `T103903-2BBh-M`        → (103903, 2BBh, M)    ← 带字母前缀
        十部 `H103902-4B-L`          → (103902, 4B, L)      ← 带字母前缀
        十部 `1039015BBBBhBh-XXL`    → (103901, 5BBBBhBh, XXL)
             ⚠ 这个 SKU **款号和变体之间没有连字符**（源表就是这么写的），
               按 `-` 只能拆成两段，而且 `5BBBBhBh` 里的 `5` 也是数字，
               所以「取全部数字当款号」会得到 `1039015`（7 位）—— 错的。
               正确做法：**用已知款号去前缀匹配**（见下）。
    """
    parts = str(sku or "").split("-")
    if len(parts) not in (2, 3):
        return None
    size = parts[-1]
    head = "-".join(parts[:-1])
    # 剥掉字母前缀（T/H/BV/BT…），但保留后面的数字与变体
    core = head.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
    for code in _STYLES_BY_LEN:
        if core.startswith(code):
            variant = core[len(code):].lstrip("-_ ")
            if not variant:
                return None
            return code, variant, size
    return None


def style_of(sku: str) -> str:
    p = split_sku(sku)
    return p[0] if p else ""


def num(v, default=0.0) -> float:
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return default


def size_key(size: str):
    s = str(size or "").upper()
    if s in GAP_SIZES:
        return (0, GAP_SIZES.index(s), s)
    return (1, 0, s)


# --------------------------------------------------------------------------
# ① 九部
# --------------------------------------------------------------------------
COL_STOCK, COL_TRANSIT, COL_DAILY = 2, 1, 3
COL_OVERSEAS, COL_DAYS, COL_REFILL = 4, 5, 6
COL_OPEN, COL_ORDERDAYS, COL_WDAYS = 7, 8, 9
COL_STATUS, COL_STOCKOUT, COL_STOCKOUT1 = 10, 11, 12


def parse_nine(html: str) -> dict:
    inv = extract_json_object(html, "const INV =")
    whn = inv.get("whn") or []
    whv = inv.get("whv") or {}
    bwv = inv.get("bwv") or {}
    rows = []
    for r in inv.get("rows") or []:
        if not isinstance(r, list) or len(r) < 11:
            continue
        p = split_sku(r[0])
        if not p or p[0] not in STYLES:
            continue
        code, variant, size = p

        def wh_of(src):
            arr = src.get(r[0]) or []
            d = {}
            for i, name in enumerate(whn):
                v = num(arr[i]) if i < len(arr) else 0
                if v:
                    d[name] = v
            return d

        rows.append({
            "sku": r[0], "code": code, "variant": variant, "size": size,
            "stock": num(r[COL_STOCK]),
            "transit": num(r[COL_TRANSIT]),
            "overseas": num(r[COL_OVERSEAS]),
            "daily": num(r[COL_DAILY]),
            "sellDays": num(r[COL_DAYS]),
            "refill": num(r[COL_REFILL]),
            "openOrders": num(r[COL_OPEN]),
            "orderDays": num(r[COL_ORDERDAYS]),
            "whDays": num(r[COL_WDAYS]),
            "status": str(r[COL_STATUS] or ""),
            "stockout": str(r[COL_STOCKOUT] or ""),
            "stockoutNoTransit": str(r[COL_STOCKOUT1] or ""),
            "sales10": None,
            "wh": wh_of(whv),
            "whTransit": wh_of(bwv),
            "derived": False,
        })
    return {
        "key": "nine",
        "label": "九部（半托管 · TTSA-9）",
        "short": "九部",
        "url": URL_NINE,
        "gen": str(inv.get("gen") or ""),
        "totalRows": len(inv.get("rows") or []),
        "verify": (inv.get("verify") or {}).get("verdict") or "",
        "rows": rows,
        "note": "代发库存 = 恒信可用 + 帕沃尔代发库存；"
                "海外仓库存 = 代发途中 + 代发库存；可售天数 / 补货数量 / 库存状态取自看板原值。",
    }


# --------------------------------------------------------------------------
# ② 十部
# --------------------------------------------------------------------------
def parse_ten(html: str) -> dict:
    snap = extract_json_object(html, "const SNAP =")
    key_level = set(snap.get("key_level_fields") or ["未交货订单数", "近7天日均"])
    agg: dict[str, dict] = {}
    dropped: list[str] = []
    for r in snap.get("rows") or []:
        sku = str(r.get("小款号") or "").strip()
        if not sku:
            continue
        p = split_sku(sku)
        if not p or p[0] not in STYLES:
            continue
        code, variant, size = p
        # 丢脏行（见 TEN_DROP_ROWS 的说明）：按「款号 + 变体 + 店铺」判
        drop_rules = TEN_DROP_ROWS.get(code) or []
        shop_key = str(r.get("店铺") or "").strip()
        if any(rule["variant"] == variant and shop_key in rule["shops"] for rule in drop_rules):
            if sku not in dropped:
                dropped.append(sku)
            continue
        a = agg.get(sku)
        if a is None:
            a = agg[sku] = {
                "sku": sku, "code": code, "variant": variant, "size": size,
                "stock": 0.0, "transit": 0.0, "sales10": 0.0,
                "openOrders": 0.0, "daily": 0.0,
                "wh": {}, "whTransit": {}, "shops": set(), "sources": set(),
                "rawRows": 0,
            }
        a["rawRows"] += 1
        # 逐行**相加**的字段
        a["stock"] += num(r.get("代发库存"))
        a["transit"] += num(r.get("代发途中"))
        a["sales10"] += num(r.get("10天销量"))
        # ⚠ 款级字段：只取一次，绝不累加（否则销量会被放大成 6 倍）
        if "未交货订单数" in key_level:
            a["openOrders"] = max(a["openOrders"], num(r.get("未交货订单数")))
        else:
            a["openOrders"] += num(r.get("未交货订单数"))
        if "近7天日均" in key_level:
            a["daily"] = max(a["daily"], num(r.get("近7天日均")))
        else:
            a["daily"] += num(r.get("近7天日均"))
        wh = str(r.get("仓库名称") or "").strip()
        if wh:
            a["wh"][wh] = a["wh"].get(wh, 0.0) + num(r.get("代发库存"))
            tr = num(r.get("代发途中"))
            if tr:
                a["whTransit"][wh] = a["whTransit"].get(wh, 0.0) + tr
        shop = str(r.get("店铺") or "").strip()
        if shop:
            a["shops"].add(shop)
        src = str(r.get("货源") or "").strip()
        if src:
            a["sources"].add(src)

    rows = []
    for a in agg.values():
        overseas = a["stock"] + a["transit"]
        daily = a["daily"]
        # 十部没有这三列 ⇒ 本页按九部口径自算，并标 derived=True
        sell_days = round(overseas / daily, 1) if daily > 0 else 0
        wh_days = round(a["stock"] / daily, 1) if daily > 0 else 0
        refill = max(0.0, round((60 - sell_days) * daily)) if daily > 0 else 0
        if daily <= 0:
            status = "滞销库存"
        elif a["stock"] <= 0:
            # 现货发不出去 —— 比"紧急补货"更该被看见（与九部页口径一致）
            status = "现货已断"
        elif sell_days < 30:
            status = "紧急补货"
        elif sell_days < 60:
            status = "正常补货"
        elif sell_days <= 80:
            status = "无需补货"
        else:
            status = "滞销库存"
        rows.append({
            "sku": a["sku"], "code": a["code"], "variant": a["variant"], "size": a["size"],
            "stock": a["stock"], "transit": a["transit"], "overseas": overseas,
            "daily": daily, "sellDays": sell_days, "refill": refill,
            "openOrders": a["openOrders"], "orderDays": 0, "whDays": wh_days,
            "status": status, "stockout": "", "stockoutNoTransit": "",
            "sales10": a["sales10"],
            "wh": {k: v for k, v in a["wh"].items() if v},
            "whTransit": a["whTransit"],
            "shops": sorted(a["shops"]),
            "sources": sorted(a["sources"]),
            "rawRows": a["rawRows"],
            "derived": True,
        })
    rows.sort(key=lambda r: (r["variant"], size_key(r["size"])))
    return {
        "key": "ten",
        "label": "十部（帕沃尔 + 恒信）",
        "short": "十部",
        "url": URL_TEN,
        "gen": str(snap.get("built_at") or ""),
        "totalRows": len(snap.get("rows") or []),
        "verify": "",
        "rows": rows,
        "dropped": dropped,
        "note": "代发库存 / 代发途中 / 10天销量 按「货源×店铺×仓库」逐行相加；"
                "未交货订单数 / 近7天日均 是款级值，只取一次（相加会翻好几倍）。"
                "可售天数 / 补货数量 / 库存状态 看板没有，由本页按九部同口径自算。",
    }


# --------------------------------------------------------------------------
# 汇总与趋势
# --------------------------------------------------------------------------
def empty_totals() -> dict:
    return {"stock": 0, "transit": 0, "overseas": 0, "openOrders": 0,
            "refill": 0, "daily": 0, "sales10": 0, "skus": 0, "sizes": 0,
            "urgent": 0, "normal": 0, "ok": 0, "dead": 0, "zero": 0}


def sum_totals(rows: list[dict]) -> dict:
    t = empty_totals()
    for r in rows:
        for k in ("stock", "transit", "overseas", "openOrders", "refill", "daily", "sales10"):
            t[k] += num(r.get(k))
        t["skus"] += 1
    t["urgent"] = sum(1 for r in rows if r.get("status") == "紧急补货")
    t["normal"] = sum(1 for r in rows if r.get("status") == "正常补货")
    t["ok"] = sum(1 for r in rows if r.get("status") == "无需补货")
    t["dead"] = sum(1 for r in rows if r.get("status") == "滞销库存")
    t["zero"] = sum(1 for r in rows if num(r.get("stock")) <= 0)
    t["sizes"] = len({(r.get("code"), r.get("variant"), r.get("size")) for r in rows})
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in t.items()}


def build_styles(rows: list[dict], prev: dict) -> list[dict]:
    by_style: dict[str, list[dict]] = {}
    for r in rows:
        by_style.setdefault(r["code"], []).append(r)
    out = []
    for code in STYLES:
        rows_c = by_style.get(code) or []
        if not rows_c:
            out.append({"code": code, "variants": [], "totals": empty_totals()})
            continue
        for r in rows_c:
            pv = prev.get(r["sku"]) or {}
            r["prevStock"] = pv.get("stock")
            r["prevOverseas"] = pv.get("overseas")
        variants: dict[str, list[dict]] = {}
        for r in rows_c:
            variants.setdefault(r["variant"], []).append(r)
        vlist = []
        for name in sorted(variants, key=lambda v: (size_key(min(variants[v], key=lambda x: size_key(x["size"]))["size"]), v)):
            vrows = sorted(variants[name], key=lambda x: size_key(x["size"]))
            vlist.append({
                "variant": name,
                "note": VARIANT_NOTE.get(name, ""),
                "rows": vrows,
                "totals": sum_totals(vrows),
            })
        out.append({"code": code, "variants": vlist, "totals": sum_totals(rows_c)})
    return out


def load_history() -> dict:
    if not HISTORY.exists():
        return {}
    try:
        d = json.loads(HISTORY.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        print("[stock-track] 历史文件损坏，忽略并重建：%s" % str(e)[:120])
        return {}


def board_day(gen: str) -> str:
    """快照按**看板生成时间**的日期归档，不按运行日期。

    反例（也是本脚本最容易犯的错）：17:00 跑、看板还没重算，抓到的是昨天那版；
    若按 today 记，趋势图会凭空虚增一个"今天"的点，且永远滞后一天。
    """
    txt = str(gen or "").strip()
    if len(txt) >= 10 and txt[:4].isdigit():
        return txt[:10]
    return date.today().isoformat()


def save_history(history: dict, source: str, day: str, rows: list[dict], gen: str) -> None:
    hist = history.setdefault(source, {})
    hist[day] = {
        "gen": gen,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rows": {r["sku"]: {"stock": r["stock"], "overseas": r["overseas"],
                            "transit": r["transit"]} for r in rows},
    }
    keep = sorted(hist)[-HISTORY_DAYS:]
    for k in list(hist):
        if k not in keep:
            hist.pop(k, None)
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def prev_rows(history: dict, source: str, day: str) -> dict:
    hist = history.get(source) or {}
    earlier = sorted(d for d in hist if d < day)
    if not earlier:
        return {}
    return (hist[earlier[-1]] or {}).get("rows") or {}


def history_view(history: dict, source: str) -> dict:
    hist = history.get(source) or {}
    days = sorted(hist)[-HISTORY_DAYS:]
    out = {"dates": days, "styles": {}}
    for code in STYLES:
        series = {"overseas": [], "stock": [], "transit": []}
        for d in days:
            rows = (hist.get(d) or {}).get("rows") or {}
            tot = {"overseas": 0.0, "stock": 0.0, "transit": 0.0}
            for sku, v in rows.items():
                if style_of(sku) != code:
                    continue
                for k in tot:
                    tot[k] += num((v or {}).get(k))
            for k in series:
                series[k].append(round(tot[k], 2))
        out["styles"][code] = series
    return out


def is_stale(gen: str) -> bool:
    txt = str(gen or "").strip()
    if not txt:
        return True
    return not txt.startswith(date.today().isoformat())


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def grab(url: str, marker: str, wait_fresh: bool, max_wait: int,
         retries: int = 4, log=print):
    """抓 + 解析。两层重试别混：

    ① 网络层（`retries` 次，退避 5/15/30/60 秒）：永远要做。实测 2026-10-28
       出现过 `WinError 10060`，紧接着再抓就成功；只抓一次会把整天数据丢掉。
    ② 新鲜度层（只有 wait_fresh）：看板自己 17:00 才重算，17:00 整点抓到的
       很可能还是昨天版本。等不到就标 stale，**绝不假装是新数据**。
       （2026-10-29 起任务改到 17:30 跑，通常已在重算之后，这一层基本空转。）
    """
    deadline = time.time() + max_wait
    net_fail = 0
    waited_log = False
    while True:
        try:
            html = fetch_html(url)
            data = extract_json_object(html, marker)
        except Exception as e:  # noqa: BLE001
            net_fail += 1
            log("    第 %d/%d 次失败：%s" % (net_fail, retries, str(e)[:110]))
            if net_fail >= retries:
                raise
            time.sleep(min(60, 5 * (2 ** (net_fail - 1))))
            continue
        net_fail = 0
        gen = str(data.get("gen") or data.get("built_at") or "")
        log("    抓取成功：生成 %s，rows=%d" % (gen or "(空)", len(data.get("rows") or [])))
        if not wait_fresh or not is_stale(gen):
            return html, data
        if time.time() >= deadline:
            log("    !! 等了 %d 秒仍是 %s（不是今天），先用这版并标 stale"
                % (max_wait, gen or "(空)"))
            return html, data
        if not waited_log:
            log("    还是 %s（不是今天），每 60 秒重抓，最多等 %d 秒…" % (gen or "(空)", max_wait))
            waited_log = True
        time.sleep(60)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="库存跟踪：九部 + 十部，6 款逐尺码")
    ap.add_argument("--source", choices=["nine", "ten", "all"], default="all")
    ap.add_argument("--wait-fresh", action="store_true")
    ap.add_argument("--max-wait", type=int, default=480)
    ap.add_argument("--wait-after", default="16:50",
                    help="只有当前时间 ≥ 这个时刻才启用 --wait-fresh")
    ap.add_argument("--print", dest="do_print", action="store_true")
    args = ap.parse_args(argv)

    # 只有傍晚那一班（≥16:50）才值得等；手动补跑 / 排障都只想拿当前版本。
    # 2026-10-29 起该班次是 17:30（原 17:00），看板 17:00 已重算 ⇒ 一般不等。
    wait_fresh = args.wait_fresh
    if wait_fresh and args.wait_after:
        try:
            hh, mm = [int(x) for x in args.wait_after.split(":")]
            gate = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
            if datetime.now() < gate:
                print("[stock-track] 现在 %s，早于 --wait-after %s —— 不等待"
                      % (datetime.now().strftime("%H:%M"), args.wait_after))
                wait_fresh = False
        except ValueError:
            print("[stock-track] --wait-after 格式不对（要 HH:MM），忽略闸门")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    history = load_history()
    wanted = {"nine": ["nine"], "ten": ["ten"], "all": ["nine", "ten"]}[args.source]
    specs = {
        "nine": (URL_NINE, "const INV =", parse_nine),
        "ten": (URL_TEN, "const SNAP =", parse_ten),
    }

    sources: dict[str, dict] = {}
    failures: list[str] = []
    for key in wanted:
        url, marker, parser = specs[key]
        print("[stock-track] 抓 %s：%s" % (key, url))
        try:
            html, data = grab(url, marker, wait_fresh, args.max_wait)
        except Exception as e:  # noqa: BLE001
            print("  !! %s 抓取失败：%s" % (key, str(e)[:180]))
            failures.append(key)
            continue
        (RAW_DIR / ("raw-%s.html" % key)).write_text(html, encoding="utf-8")
        src = parser(html)
        if not src["rows"]:
            print("  !! %s 解析到 0 行 —— 6 个款号一行都没匹配到，先看 %s"
                  % (key, RAW_DIR / ("raw-%s.html" % key)))
            failures.append(key)
            continue
        day = board_day(src["gen"])
        # ★ 先写历史再取趋势：反过来的话今天这个点永远进不了趋势图
        save_history(history, key, day, src["rows"], src["gen"])
        prev = prev_rows(history, key, day)
        src["day"] = day
        src["styles"] = build_styles(src["rows"], prev)
        src["totals"] = sum_totals(src["rows"])
        src["history"] = history_view(history, key)
        src["stale"] = is_stale(src["gen"])
        src.pop("rows", None)  # 行都在 styles 里，别重复存一份
        sources[key] = src
        print("  → %d 个尺码行，%d 个款号，记在 %s%s"
              % (sum(len(v["rows"]) for st in src["styles"] for v in st["variants"]),
                 len([st for st in src["styles"] if st["variants"]]), day,
                 "  ⚠ 不是今天" if src["stale"] else ""))

    if not sources:
        print("[stock-track] 两个源都没抓到数据，退出")
        return 2 if failures else 3

    payload = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "styles": STYLES,
        "sources": sources,
        "failed": failures,
        "rule": {
            "note": "实际库存（本页主口径）= 代发库存：已经在海外仓、可以直接发的件数。"
                    "代发途中是还在路上的，不算可用。海外仓库存 = 实际库存 + 代发途中，只作参考。",
            "source": "九部取 TTSA-9 库存管理看板；十部取帕沃尔海外仓 + 恒信 OMS 的库存看板。",
        },
    }

    try:
        PUBLIC_JSON.parent.mkdir(parents=True, exist_ok=True)
        PUBLIC_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("[stock-track] 落盘失败：%s" % str(e)[:200])
        return 4

    print("[stock-track] 已写 %s" % PUBLIC_JSON.relative_to(ROOT))
    for key, src in sources.items():
        t = src["totals"]
        print("  %-6s 实际库存 %-8s 途中 %-8s 海外仓 %-8s 未交货 %-8s 尺码 %d"
              % (src["short"], int(t["stock"]), int(t["transit"]), int(t["overseas"]),
                 int(t["openOrders"]), t["skus"]))
    if failures:
        print("  ⚠ 失败的源：%s（其余照常发布）" % "、".join(failures))

    if args.do_print:
        for key, src in sources.items():
            print("\n=== %s ===" % src["label"])
            for st in src["styles"]:
                for v in st["variants"]:
                    for r in v["rows"]:
                        print("  %-22s %-5s 实际 %6s 途中 %6s 日均 %5s 可售 %5s天 %s"
                              % (r["sku"], r["size"], int(r["stock"]), int(r["transit"]),
                                 r["daily"], r["sellDays"], r["status"]))
    return 0 if not failures else 0  # 部分失败仍按 0 退出：另一半已正常发布


if __name__ == "__main__":
    sys.exit(main())
