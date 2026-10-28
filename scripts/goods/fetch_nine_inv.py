#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_nine_inv.py — 抓「九部（TTSA-9）库存管理看板」，产出九部库存跟踪数据。

用户需求（2026-10-28）
----------------------
每天 17:00 跟踪九部这 6 个款号 **每个尺码** 的库存：
    204203 / 204207 / 204004 / 103903 / 103902 / 103901
结果挂到运营后台 https://mgltemu.us.ci/#/temu 的「九部库存跟踪」页。

数据源
------
`https://cbec756b1bad42778134c59af1ab67ce.app.workbuddy.host/`
就是「TTSA-9库存管理看板」。它是静态页，但**数据内嵌**在 `const INV = {...}` 里
（不像 warehouse-probe 那样叫 SNAP）：

    INV.rows  588 行，每行 13 列：
        0 SKU（`款号-变体-尺码`，如 `204203-4SetA-L`；103902/103903 带 `T` 前缀）
        1 代发途中      2 代发库存      3 七天日均      4 海外仓库存
        5 可售天数      6 补货数量      7 未交货订单    8 订单+库存可售天数
        9 海外仓可售天数 10 库存状态     11 预计断货日（含在途）  12 不含在途断货日
    INV.whn   仓库名列表：['加州3仓','Kansas仓','新泽西2仓','HXCA仓库','其他仓']
    INV.whv   {SKU: [各仓代发库存]}      INV.bwv  {SKU: [各仓代发途中]}
    INV.gen   看板生成时间；INV.verify 看板自检结论

为什么读内嵌快照而不是渲染后的表格
--------------------------------
这页**没有分页**（一次性内嵌全部 588 行），且我们只要 6 个款号的 70 行；
`INV` 就是页面渲染用的同一份数据 ⇒ 直接解析快照既快（1 秒）又不会漏行。
（warehouse-probe 那页分页 50 行/页，所以那边必须读 DOM，口径不一样。）

产出
----
1. `temu-admin/public/data/nine-inv.json`   前端页面直接 fetch（含 90 天趋势）
2. `data/nine/history.json`                 每日快照（同一天重跑覆盖当天）
3. `out/nine-inv-raw.html`                  原始页面，出问题可复查

用法
----
    python scripts/goods/fetch_nine_inv.py                 # 抓一次并落盘
    python scripts/goods/fetch_nine_inv.py --wait-fresh    # 看板还没到今天就先等
    python scripts/goods/fetch_nine_inv.py --print         # 顺便把表格打到控制台

退出码
------
    0 成功；2 抓取失败（页面拉不到 / 找不到 INV）；3 解析到 0 行（口径变了）；
    4 落盘失败
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_JSON = ROOT / "temu-admin" / "public" / "data" / "nine-inv.json"
HISTORY = ROOT / "data" / "nine" / "history.json"
RAW_HTML = ROOT / "out" / "nine-inv-raw.html"

URL = "https://cbec756b1bad42778134c59af1ab67ce.app.workbuddy.host/"

#: 要跟踪的 6 个款号（顺序＝页面展示顺序＝用户给的顺序）
STYLES = ["204203", "204207", "204004", "103903", "103902", "103901"]

#: 每个款号下面可能出现的「变体」中文备注（只用于页面上显示得更像人话）
VARIANT_NOTE = {
    "4SetA": "4件套A",
    "4SetB": "4件套B",
    "5SetA": "5件套A",
    "5SetB": "5件套B",
    "5B": "5件套B",
    "2BBh": "2件套B(黑)",
    "2H": "2件套H",
    "2L": "2件套L",
    "5BBBBhBh": "5件套(黑黑黑白黑)",
}

COL_STOCK = 2       # C 代发库存
COL_TRANSIT = 1     # B 代发途中
COL_DAILY = 3       # D 七天日均
COL_OVERSEAS = 4    # E 海外仓库存
COL_DAYS = 5        # F 可售天数
COL_REFILL = 6      # G 补货数量
COL_OPEN = 7        # H 未交货订单
COL_ORDERDAYS = 8   # I 订单+库存可售天数
COL_WDAYS = 9       # J 海外仓可售天数
COL_STATUS = 10     # K 库存状态
COL_STOCKOUT = 11   # 预计断货日（含在途）
COL_STOCKOUT1 = 12  # 不含在途断货日

HISTORY_DAYS = 90
GAP_SIZES = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL", "5XL"]


# --------------------------------------------------------------------------
# 抓页面
# --------------------------------------------------------------------------
def fetch_html(url: str = URL, timeout: float = 60.0) -> str:
    """直连抓取（**绕开系统代理**）。

    本机系统代理 127.0.0.1:10808 会让这条链路的直连（Cloudflare Pages）偶发
    握手失败；`ProxyHandler({})` 显式空代理整条链实测 1.1 秒稳定。
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


def extract_json_object(html: str, marker: str = "const INV =") -> dict:
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
# 解析
# --------------------------------------------------------------------------
def split_sku(sku: str):
    """`204203-4SetA-L` → (204203, 4SetA, L)；`T103902-5B-S` → (103902, 5B, S)。

    款号可能带一个非数字前缀（103902/103903 在母表里写作 `T103902`），
    这里把前缀剥掉，页面统一按纯数字款号归组。三段式之外的一律丢弃。
    """
    parts = str(sku or "").split("-")
    if len(parts) != 3:
        return None
    head, variant, size = parts
    digits = "".join(ch for ch in head if ch.isdigit())
    if not digits:
        return None
    return digits, variant, size


def num(v, default=0):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return default


def build_rows(inv: dict) -> list[dict]:
    """INV.rows → 只保留 6 个款号的行，并拆成结构化字段。"""
    whn = inv.get("whn") or []
    whv = inv.get("whv") or {}
    bwv = inv.get("bwv") or {}
    out: list[dict] = []
    for r in inv.get("rows") or []:
        if not isinstance(r, list) or len(r) < 11:
            continue
        parsed = split_sku(r[0])
        if not parsed:
            continue
        code, variant, size = parsed
        if code not in STYLES:
            continue

        def wh_of(src):
            arr = src.get(r[0]) or []
            d = {}
            for i, name in enumerate(whn):
                val = num(arr[i]) if i < len(arr) else 0
                if val:
                    d[name] = val
            return d

        out.append({
            "sku": r[0],
            "code": code,
            "variant": variant,
            "size": size,
            "transit": num(r[COL_TRANSIT]),
            "stock": num(r[COL_STOCK]),
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
            "wh": wh_of(whv),
            "whTransit": wh_of(bwv),
        })
    return out


def size_key(size: str):
    s = str(size or "").upper()
    if s in GAP_SIZES:
        return (0, GAP_SIZES.index(s), s)
    return (1, 0, s)


def build_payload(rows: list[dict], inv: dict, history: dict,
                  fetched_at: str, day: str) -> dict:
    """把 70 行整理成「款号 → 变体 → 尺码」三级 + 汇总 + 趋势。

    `day` = 看板快照日期（不是运行日期），用来挑「上一次」快照做对比。
    """
    prev = previous_day_rows(history, day)
    by_style: dict[str, list[dict]] = {c: [] for c in STYLES}
    for row in rows:
        by_style.setdefault(row["code"], []).append(row)

    styles = []
    for code in STYLES:
        rows_c = sorted(by_style.get(code) or [],
                        key=lambda x: (x["variant"], size_key(x["size"])))
        if not rows_c:
            styles.append({"code": code, "variants": [], "totals": empty_totals()})
            continue

        variants: dict[str, list[dict]] = {}
        for row in rows_c:
            pv = prev.get(row["sku"]) or {}
            row["prevStock"] = pv.get("stock")
            row["prevOverseas"] = pv.get("overseas")
            variants.setdefault(row["variant"], []).append(row)

        vlist = []
        for name in sorted(variants, key=lambda v: size_key_of_variant(variants[v])):
            vrows = sorted(variants[name], key=lambda x: size_key(x["size"]))
            vlist.append({
                "variant": name,
                "note": VARIANT_NOTE.get(name, ""),
                "rows": vrows,
                "totals": sum_totals(vrows),
            })

        styles.append({
            "code": code,
            "variants": vlist,
            "totals": sum_totals(rows_c),
        })

    hist = history_view(history)
    return {
        "generatedAt": fetched_at,
        "board": {
            "gen": str(inv.get("gen") or ""),
            "url": URL,
            "rows": len(inv.get("rows") or []),
            "picked": len(rows),
            "verify": (inv.get("verify") or {}).get("verdict") or "",
            "stale": is_stale(inv.get("gen")),
        },
        "styles": styles,
        "totals": sum_totals(rows),
        "history": hist,
        "rule": {
            "note": "代发库存 = 恒信可用 + 帕沃尔代发库存；代发途中 = 恒信在途 + 帕沃尔代发途中；"
                    "海外仓库存 = 代发途中 + 代发库存；可售天数 = 海外仓库存 ÷ 七天日均。",
            "source": "九部半托管-库存管理表「总库存表」+ 恒信 OMS 产品库存 + 帕沃尔海外仓库存，"
                      "由 TTSA-9库存管理看板逐日 17:00 重算。",
        },
    }


def size_key_of_variant(vrows: list[dict]):
    sizes = [size_key(r["size"]) for r in vrows]
    return min(sizes) if sizes else (9, 0, "")


def empty_totals() -> dict:
    return {"stock": 0, "transit": 0, "overseas": 0, "openOrders": 0,
            "refill": 0, "daily": 0, "skus": 0}


def sum_totals(rows: list[dict]) -> dict:
    t = empty_totals()
    for r in rows:
        t["stock"] += num(r.get("stock"))
        t["transit"] += num(r.get("transit"))
        t["overseas"] += num(r.get("overseas"))
        t["openOrders"] += num(r.get("openOrders"))
        t["refill"] += num(r.get("refill"))
        t["daily"] += num(r.get("daily"))
        t["skus"] += 1
    t["urgent"] = sum(1 for r in rows if r.get("status") == "紧急补货")
    t["normal"] = sum(1 for r in rows if r.get("status") == "正常补货")
    t["ok"] = sum(1 for r in rows if r.get("status") == "无需补货")
    t["dead"] = sum(1 for r in rows if r.get("status") == "滞销库存")
    t["sizes"] = len({(r.get("code"), r.get("variant"), r.get("size")) for r in rows})
    return t


# --------------------------------------------------------------------------
# 历史（每日快照）
# --------------------------------------------------------------------------
def load_history() -> dict:
    if not HISTORY.exists():
        return {}
    try:
        data = json.loads(HISTORY.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        print("[nine-inv] 历史文件损坏，忽略并从今天重建：%s" % str(e)[:120])
        return {}


def board_day(gen: str) -> str:
    """看板快照所属的日期（`2026-10-27 18:38` → `2026-10-27`）。

    ⚠ 历史快照必须按**看板的日期**记，不能按「运行日期」记。
    反例（这是本脚本最容易犯、也最难发现的错）：17:00 跑，但看板还没重算，
    抓到的是昨天 18:38 那一版 —— 若此时按 today 落盘，趋势图就会凭空虚增
    一个"今天"的点，而且这个点**永远比真实值滞后一天**，后面每天都会错位，
    越看越像"库存突然掉了"。所以：看板是哪天的版本，就记在哪一天。
    """
    txt = str(gen or "").strip()
    if len(txt) >= 10 and txt[:4].isdigit():
        return txt[:10]
    return date.today().isoformat()


def save_history(history: dict, day: str, rows: list[dict], gen: str) -> None:
    """按 `day`（看板日期）写快照；同一天重跑覆盖，不新增点。"""
    history[day] = {
        "gen": gen,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rows": {r["sku"]: {"stock": r["stock"], "overseas": r["overseas"],
                            "transit": r["transit"]} for r in rows},
    }
    # 只留最近 N 天
    keep = sorted(history)[-HISTORY_DAYS:]
    for k in list(history):
        if k not in keep:
            history.pop(k, None)
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, sort_keys=True),
                       encoding="utf-8")


def previous_day_rows(history: dict, day: str) -> dict:
    """`day` 之前最近一次快照的行，用来算「比上次」的变化。

    用 `day`（看板日期）而不是 today：看板没刷新时，真正的"上一次"是
    上一个有快照的日期，而不是"今天之前的最后一天"（今天可能还没写进去）。
    """
    earlier = sorted(d for d in history if d < day)
    if not earlier:
        return {}
    return (history[earlier[-1]] or {}).get("rows") or {}


def history_view(history: dict) -> dict:
    """近 N 天按款号聚合的趋势（页面画折线）。"""
    days = sorted(history)[-HISTORY_DAYS:]
    out = {"dates": days, "styles": {}}
    for code in STYLES:
        series = {"overseas": [], "stock": [], "transit": []}
        for d in days:
            rows = ((history.get(d) or {}).get("rows")) or {}
            tot = {"overseas": 0, "stock": 0, "transit": 0}
            for sku, v in rows.items():
                parsed = split_sku(sku)
                if not parsed or parsed[0] != code:
                    continue
                for k in tot:
                    tot[k] += num((v or {}).get(k))
            for k in series:
                series[k].append(round(tot[k], 2))
        out["styles"][code] = series
    return out


def is_stale(gen: str) -> bool:
    """看板生成时间不是今天 ⇒ 数据没刷新（页面会顶一条黄条提示）。"""
    txt = str(gen or "").strip()
    if not txt:
        return True
    return not txt.startswith(date.today().isoformat())


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def grab(wait_fresh: bool, max_wait: int, retries: int = 4, log=print):
    """抓一次页面。两层重试，别混：

    ① **网络层**（`retries` 次，退避 5/15/30/60 秒）—— 与 `wait_fresh` 无关，
       永远要做。实测 2026-10-28 10:41 出现过一次 `WinError 10060`（连接超时），
       紧接着再抓就成功了；只抓一次会把整天的数据丢掉。
    ② **新鲜度层**（只有 `wait_fresh` 时）—— 看板自己也是 17:00 重算，
       实测它的快照时刻集中在 17:00 / 17:01（95 份历史里 17:00±1 分占 13 份），
       所以 17:00 整点抓到的很可能还是昨天的版本。等到 `max_wait` 秒仍不是今天，
       就用这一版并把 `stale` 标进 JSON（页面顶黄条），**绝不假装是新数据**。
    """
    deadline = time.time() + max_wait
    net_fail = 0
    waited_log = False
    while True:
        try:
            html = fetch_html()
            inv = extract_json_object(html)
        except Exception as e:  # noqa: BLE001
            net_fail += 1
            log("[nine-inv] 第 %d/%d 次抓取失败：%s" % (net_fail, retries, str(e)[:120]))
            if net_fail >= retries:
                raise
            time.sleep(min(60, 5 * (2 ** (net_fail - 1))))
            continue

        net_fail = 0
        gen = str(inv.get("gen") or "")
        log("[nine-inv] 抓取成功：看板生成 %s，rows=%d" % (gen or "(空)", len(inv.get("rows") or [])))

        if not wait_fresh or not is_stale(gen):
            return html, inv
        if time.time() >= deadline:
            log("[nine-inv] !! 等了 %d 秒看板仍是 %s（不是今天），先用这一版，页面会标黄"
                % (max_wait, gen or "(空)"))
            return html, inv
        if not waited_log:
            log("[nine-inv] 看板还是 %s（不是今天），每 60 秒重抓，直到 %s 或超时 %d 秒…"
                % (gen or "(空)", datetime.now().strftime("%H:%M"), max_wait))
            waited_log = True
        time.sleep(60)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="抓九部(TTSA-9)库存 → nine-inv.json")
    ap.add_argument("--wait-fresh", action="store_true",
                    help="看板还没更新到今天时，先等（最多 --max-wait 秒）")
    ap.add_argument("--max-wait", type=int, default=480, help="最多等多少秒（默认 480）")
    ap.add_argument("--wait-after", default="16:50",
                    help="只有当前时间 ≥ 这个时刻才启用 --wait-fresh（默认 16:50）")
    ap.add_argument("--print", dest="do_print", action="store_true", help="把表格打到控制台")
    args = ap.parse_args(argv)

    # ⚠ 只有「17:00 那一班」才值得等：看板自己也是 17:00 重算。
    #   其它时段的跑法（手动补跑、开机补跑、排障）都是「拿现在这一版就行」，
    #   若无条件等 8 分钟，只会让人以为脚本卡住了。所以加一个时刻闸门，
    #   过点之后仍然可以等（那时看板多半已经翻新，第一把就命中）。
    wait_fresh = args.wait_fresh
    if wait_fresh and args.wait_after:
        try:
            hh, mm = [int(x) for x in args.wait_after.split(":")]
            gate = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
            if datetime.now() < gate:
                print("[nine-inv] 现在是 %s，早于 --wait-after %s —— 不等待，直接用当前版本"
                      % (datetime.now().strftime("%H:%M"), args.wait_after))
                wait_fresh = False
        except ValueError:
            print("[nine-inv] --wait-after 格式不对（要 HH:MM），忽略这个闸门")

    try:
        html, inv = grab(wait_fresh, args.max_wait)
    except Exception as e:  # noqa: BLE001
        print("[nine-inv] 抓取失败：%s" % str(e)[:200])
        return 2

    RAW_HTML.parent.mkdir(parents=True, exist_ok=True)
    RAW_HTML.write_text(html, encoding="utf-8")

    rows = build_rows(inv)
    if not rows:
        print("[nine-inv] 解析到 0 行 —— 6 个款号在母表里一行都没匹配到，"
              "大概是款号口径变了（先看 %s）" % RAW_HTML)
        return 3

    gen = str(inv.get("gen") or "")
    # ⚠ 快照按**看板日期**记，不按运行日期：看板没重算时，
    #   按 today 记会凭空多出一个滞后的假数据点（见 board_day 的注释）。
    day = board_day(gen)
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    history = load_history()

    # ★ 必须**先**把本次快照写进 history，再 build_payload 取趋势。
    #   反过来的话，今天这个点永远进不了趋势图（当天看到的永远是"历史里没有今天"，
    #   要等到明天才补上）—— 实测 2026-10-28 就是这样：history 有 10-27 和 10-28 两天的文件，
    #   但 payload 里的 dates 是空的，图上一条线都没有。
    save_history(history, day, rows, gen)
    payload = build_payload(rows, inv, history, fetched_at, day)

    missing = [c for c in STYLES if not any(s["code"] == c and s["variants"] for s in payload["styles"])]
    if missing:
        print("[nine-inv] !! 这些款号在看板里没有行：%s" % "、".join(missing))

    try:
        PUBLIC_JSON.parent.mkdir(parents=True, exist_ok=True)
        PUBLIC_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("[nine-inv] 落盘失败：%s" % str(e)[:200])
        return 4

    print("[nine-inv] 已写 %s（%d 个尺码行，%d 个款号）"
          % (PUBLIC_JSON.relative_to(ROOT), len(rows), len(STYLES)))
    print("[nine-inv] 历史快照 %s（共 %d 天，本次记在 %s），看板生成 %s%s"
          % (HISTORY.relative_to(ROOT), len(history), day, gen or "(空)",
             "  ⚠ 不是今天" if payload["board"]["stale"] else ""))

    tot = payload["totals"]
    print("[nine-inv] 合计：海外仓库存 %s ｜ 代发库存 %s ｜ 代发途中 %s ｜ 未交货订单 %s"
          % (tot["overseas"], tot["stock"], tot["transit"], tot["openOrders"]))

    if args.do_print:
        for st in payload["styles"]:
            for v in st["variants"]:
                for r in v["rows"]:
                    print("  %-22s %-6s 代发 %6s 途中 %6s 海外仓 %6s 日均 %4s 可售 %5s天 %s"
                          % (r["sku"], r["size"], r["stock"], r["transit"],
                             r["overseas"], r["daily"], r["sellDays"], r["status"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
