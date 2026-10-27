# -*- coding: utf-8 -*-
"""解析 + 聚合：帕沃尔发货列表行 / 订单详情「收件地址」/ 州-市汇总。

三个纯函数族（全部离线可测，不碰网络）：
  parse_rows(html, shop_id)      发货列表 AJAX 返回的 content → 订单行（order_id / 参考单号）
  parse_order_info(html)         订单详情 HTML → (city, state, zip)
  split_city_state(text)         "DALLAS Texas" → ("DALLAS", "Texas")
  aggregate(records)             逐单记录 → 州/城市/月度三个维度的汇总

⚠ 地址格式（2026-10-14 实测多种订单抽样）——**同一站点有两种写法，必须都吃**：
  ① 州全名（多数）：`<div class="rows" style="padding-left:63px">  DALLAS Texas  </div>`
  ② 州两字母码（实测 4607/21244 ≈ 22%）：`<div class="rows" ...>  NATIONAL CITY CA  </div>`
  邮编永远在下一行：`<div class="rows">收件邮编：75217-2625</div>`
  被注释掉的那一行是「美国 <州> <城市> ******」（州在前），只作**兜底**，
  因为注释行的城市信息不如可见行全（可见行还带真实城市名，注释行在格式②下也可能被裁）。

⚠ 城市可能带 `*`（帕沃尔对部分订单脱敏，如 `Sp**** *****`），**州名/州码始终完整**。
   所以：**州是主口径，城市只作展示**。
"""
from __future__ import annotations

import collections
import re

# 50 州 + DC + PR（与 ECharts 美国地图 public/map/usa.json 的 feature name 完全一致）
STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois",
    "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
    "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota",
    "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Puerto Rico", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming",
]
# 州名按长度倒序，保证 "North Carolina" 先于 "Carolina" 命中
STATES_SORTED = sorted(STATES, key=len, reverse=True)

CODE = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Puerto Rico": "PR", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA",
    "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
NAME_BY_CODE = {v: k for k, v in CODE.items()}

# ZIP 前 3 位 → 州（**兜底**：两种文本写法都没命中时才用；另作质检对照）
ZIP3_RANGES = [
    ((6, 9), "Puerto Rico"), ((10, 27), "Massachusetts"), ((28, 29), "Rhode Island"),
    ((30, 38), "New Hampshire"), ((39, 49), "Maine"), ((50, 59), "Vermont"),
    ((60, 69), "Connecticut"), ((70, 89), "New Jersey"), ((100, 149), "New York"),
    ((150, 196), "Pennsylvania"), ((197, 199), "Delaware"), ((200, 205), "District of Columbia"),
    ((206, 219), "Maryland"), ((220, 246), "Virginia"), ((247, 268), "West Virginia"),
    ((270, 289), "North Carolina"), ((290, 299), "South Carolina"), ((300, 319), "Georgia"),
    ((320, 349), "Florida"), ((350, 369), "Alabama"), ((370, 385), "Tennessee"),
    ((386, 397), "Mississippi"), ((398, 399), "Georgia"), ((400, 427), "Kentucky"),
    ((430, 459), "Ohio"), ((460, 479), "Indiana"), ((480, 499), "Michigan"),
    ((500, 528), "Iowa"), ((530, 549), "Wisconsin"), ((550, 567), "Minnesota"),
    ((570, 577), "South Dakota"), ((580, 588), "North Dakota"), ((590, 599), "Montana"),
    ((600, 629), "Illinois"), ((630, 658), "Missouri"), ((660, 679), "Kansas"),
    ((680, 693), "Nebraska"), ((700, 714), "Louisiana"), ((716, 729), "Arkansas"),
    ((730, 749), "Oklahoma"), ((750, 799), "Texas"), ((800, 816), "Colorado"),
    ((820, 831), "Wyoming"), ((832, 838), "Idaho"), ((840, 847), "Utah"), ((850, 865), "Arizona"),
    ((870, 884), "New Mexico"), ((889, 898), "Nevada"), ((900, 961), "California"),
    ((967, 968), "Hawaii"), ((970, 979), "Oregon"), ((980, 994), "Washington"),
    ((995, 999), "Alaska"),
]

ROW_RE = re.compile(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', re.S)
REF_RE = re.compile(r'class="deliver_no_\d+"[^>]*>(.*?)</p>', re.S)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
ADDR_LINE_RE = re.compile(r'<div class="rows"[^>]*>(.*?)</div>', re.S)
ZIP_RE = re.compile(r"收件邮编：\s*([0-9]{5}(?:-[0-9]{4})?)")
CMT_ADDR_RE = re.compile(r"收件地址：美国([^*]*)")


def zip_state(zip_code: str) -> str:
    """ZIP → 州全名（兜底 + 质检用）。只按前 3 位粗判。"""
    m = re.match(r"^(\d{3})", zip_code or "")
    if not m:
        return ""
    p3 = int(m.group(1))
    for (lo, hi), name in ZIP3_RANGES:
        if lo <= p3 <= hi:
            return name
    return ""


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_rows(content: str, shop_id: str = "", shop: str = "") -> list[dict]:
    """发货列表 AJAX 的 content → 订单行。

    一个出库单可能有**多行**（多明细，靠 rowspan 合并）：同一 order_id 会出现多次，
    调用方按 order_id 去重即可。这里每行都返回，`ref_no` 取该行的「参考单号」。
    """
    out = []
    for m in ROW_RE.finditer(content or ""):
        oid, body = m.group(1), m.group(2)
        ref = REF_RE.search(body)
        out.append({
            "order_id": oid,
            "shop_id": shop_id,
            "shop": shop,
            "ref_no": re.sub(r"\s+", "", ref.group(1)) if ref else "",
        })
    return out


def split_city_state(text: str) -> tuple[str, str]:
    """`<城市> <州>` → (city, state 全名)。两种写法都吃：

        'DALLAS Texas'        → ('DALLAS', 'Texas')          ← 州全名
        'NATIONAL CITY CA'    → ('NATIONAL CITY', 'California')  ← 州两字母码（实测 ~22%）
        'Sp**** ***** Nevada' → ('Sp**** *****', 'Nevada')
        'DALLAS' / ''         → ('', '')
    """
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return "", ""
    # ① 州全名在结尾（先长后短，避免 "North" 抢 "North Carolina"）
    for st in STATES_SORTED:
        if t.endswith(st):
            return t[: -len(st)].strip(" ,"), st
    # ② 两字母州码在结尾：必须是**独立的最后一个词**，且确实是美国州/属地码
    m = re.match(r"^(.*?)[\s,]+([A-Za-z]{2})$", t)
    if m and m.group(2).upper() in NAME_BY_CODE:
        return m.group(1).strip(" ,"), NAME_BY_CODE[m.group(2).upper()]
    return "", ""


def state_in_text(text: str) -> tuple[str, int, int]:
    """在任意文本里找州（全名优先，其次两字母码）→ (state, start, end)。用于注释行兜底。"""
    for st in STATES_SORTED:
        i = text.find(st)
        if i >= 0:
            return st, i, i + len(st)
    m = re.search(r"\b([A-Z]{2})\b", text or "")
    if m and m.group(1) in NAME_BY_CODE:
        return NAME_BY_CODE[m.group(1)], m.start(1), m.end(1)
    return "", -1, -1


def parse_order_info(html: str) -> tuple[str, str, str]:
    """订单详情 HTML → (city, state 全名, zip)。三级兜底：可见地址行 → 注释地址行 → ZIP。"""
    raw = html or ""
    mz = ZIP_RE.search(raw)
    zip_code = mz.group(1) if mz else ""

    i = raw.find("交货信息")
    block = raw[i:raw.find("订单处理时效", i)] if i >= 0 else raw
    city = state = ""
    for cell in ADDR_LINE_RE.findall(block):
        txt = strip_tags(cell)
        if not txt or "收件" in txt:
            continue
        c, st = split_city_state(txt)
        if st:
            city, state = c, st
            break

    if not state:
        # 兜底 1：被注释掉的「收件地址：美国 <州> <城市> ******」（**州在前**，与可见行相反）。
        # 不能按空格切成两段——州名可能是两个词（North Carolina / New York / West Virginia）。
        for comment in COMMENT_RE.findall(raw):
            m = CMT_ADDR_RE.search(comment)
            if not m:
                continue
            st, _s, e = state_in_text(m.group(1))
            if st:
                state = st
                if not city:
                    city = m.group(1)[e:].strip(" ,*")
                break

    if not state:
        state = zip_state(zip_code)      # 兜底 2：ZIP 前三位
    return city, state, zip_code


ADDTIME_RE = re.compile(r"添加时间：\s*([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})")


def parse_addtime(html: str) -> str:
    """订单详情 HTML → 下单/添加时间 `YYYY-MM-DD HH:MM:SS`（抓不到就空串）。

    为什么加这个（2026-10-27）：增量抓的**新订单**不在「导出Excel」里，
    而 build 的 `addtime` 原来只来自导出表 ⇒ 新单没有日期，
    「近 7 天」这类滚动窗口会把它们漏掉。
    详情页本来就要抓（geo 步骤），顺手把「添加时间」一起解析掉，
    不必为了日期再单独跑一次官方导出。
    """
    m = ADDTIME_RE.search(html or "")
    return m.group(1) if m else ""


def _blank_state() -> dict:
    return {"orders": 0, "qty": 0, "cities": collections.Counter()}


# ------------------------------------------------------------------ 订单产品（详情弹窗）
DETAIL_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
DETAIL_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def parse_detail_products(html: str) -> list[dict]:
    """`deliver.php?act=order_info&type=view&id=<id>`（列表页 [详情] 弹窗）的产品表。

    表头：序号 / Fnsku / 产品名称 / 英文名称 / 平台SKU / 数量 / 货值 / 图片

    ⚠ 三个坑（2026-10-14 实测）：
      1. 数据行是**裸 `<tr>`**，没有发货列表那种 `tr_html_<id>` class —— 别复用那边的正则；
      2. 必须从表头 `Fnsku` 之后开始找，否则会把页面其它表格的行也吞进来；
      3. **一单可能挂多个产品**（抽样 200 单里 19 单是 2 个产品，约 9.5%）。
         而帕沃尔自带「导出Excel」**一单只出一行、只带第一个产品**，
         所以产品维度的口径**必须**用这个详情页，不能用导出表。
      4. 这些 TEMU 单的「平台SKU」列**实测全为空**，别指望它。
    """
    raw = html or ""
    i = raw.find("Fnsku")
    blk = raw[i:] if i >= 0 else raw
    out = []
    for tr in DETAIL_ROW_RE.findall(blk):
        tds = [strip_tags(x) for x in DETAIL_TD_RE.findall(tr)]
        if len(tds) >= 7 and tds[0].isdigit():
            out.append({
                "fnsku": tds[1],
                "product": tds[2],
                "en": tds[3],
                "platform_sku": tds[4],
                "qty": int(tds[5]) if tds[5].isdigit() else 0,
                "value": tds[6],
            })
    return out


def aggregate(records) -> dict:
    """逐单记录 → {states, topCities, months, orders, qty}。

    records 每项需含：state（**必填**，只传已解析出州的记录）、可选 city / qty / addtime。
    「一单」= 一个出库单；qty/addtime 来自帕沃尔自带导出 Excel（按参考单号 join）。
    """
    st: dict[str, dict] = collections.defaultdict(_blank_state)
    city: collections.Counter = collections.Counter()
    months: dict[str, dict] = collections.defaultdict(
        lambda: {"orders": 0, "qty": 0, "states": collections.Counter()})
    total = qty_total = 0

    for r in records:
        s = r.get("state") or ""
        if s not in CODE:
            continue
        q = int(r.get("qty") or 0)
        total += 1
        qty_total += q
        st[s]["orders"] += 1
        st[s]["qty"] += q
        c = (r.get("city") or "").strip()
        if c:
            st[s]["cities"][c] += 1
            city[(s, c)] += 1
        mk = (r.get("addtime") or "")[:7]
        if re.match(r"^\d{4}-\d{2}$", mk):
            months[mk]["orders"] += 1
            months[mk]["qty"] += q
            months[mk]["states"][s] += 1

    states = [{
        "name": name,
        "code": CODE.get(name, ""),
        "orders": v["orders"],
        "qty": v["qty"],
        "share": round(v["orders"] / total, 6) if total else 0.0,
        "cities": [{"name": c, "orders": n} for c, n in v["cities"].most_common(8)],
    } for name, v in sorted(st.items(), key=lambda kv: (-kv[1]["orders"], kv[0]))]

    top_cities = [{"name": c, "state": s, "code": CODE.get(s, ""), "orders": n}
                  for (s, c), n in city.most_common(200)]

    month_list = [{
        "month": m,
        "orders": months[m]["orders"],
        "topStates": [{"name": s, "orders": n}
                      for s, n in months[m]["states"].most_common(5)],
    } for m in sorted(months)]

    return {"states": states, "topCities": top_cities, "months": month_list,
            "orders": total, "qty": qty_total}
