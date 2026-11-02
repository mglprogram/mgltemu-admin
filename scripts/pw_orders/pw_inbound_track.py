# -*- coding: utf-8 -*-
"""帕沃尔头程订单（order.php 列表）→ 与「货件跟踪」同一套行结构。

背景（2026-11-02 用户）
-----------------------
「14450 的领星被停用，现阶段可以把 14450 的单号去帕沃尔的
  https://www.powerwarehouse.net/order.php?act=list&pb=all 头程链接
  去抓取最近一个月参考单号以 14450 开头的明细抓下来」

为什么要单独一路：
  14450 的领星 OpenAPI **还在返回数据，但已经停止更新**
  （实测 68 张、最新 orderCreateTime 停在 2026-10-24，此后新单进不来）。
  而帕沃尔后台的头程订单一直在新增（2026-11-02 当天就有 12 单待入库），
  所以 14450 这半边改成**从帕沃尔抓**。

数据口径（全部实测确认，2026-11-02）
------------------------------------
列表：`order.php?act=query_order_list`（POST 表单，和页面里 listTable 走同一入口）
  · 必须用 **POST + act=query_order_list**，GET 带参数不生效。
  · `start_date/end_date` 生效（GET 不生效是因为没带 act）。
  · 列顺序：序号 / 入仓单号 / **参考单号** / 总箱数 / 已入箱数 / 重量(lb) /
    出货渠道 / 目的仓库 / 交货方式 / 目的类型 / 头程类型 / 状态 / 添加时间
  · 参考单号就是 14450 开头的那个号（144503079），也是这一票的业务主键。
  · ⚠ `waybill` 只支持**精确**匹配（传 "1445030" 查不到，传 "144503079" 才有），
    所以查前缀只能拉全量本地筛。

详情：`order.php?act=order_info&id=<出库单id>`（HTML，四个 table_0..3）
  · table_0 产品明细：Fnsku / 产品名称 / 平台SKU / **单箱数量**（每箱重复一行）
  · table_1 装箱信息：**海外仓入库时间** ← 这就是「上架」的时间点
  · table_2 **轨迹详情**：时间 / 中文轨迹（下单成功 → 系统审核通过 → 海外仓入库成功）
  · 表头区有「预计到货时间」（ETA）—— 帕沃尔没有独立的「到港」节点，
    所以预测基准用 ETA。
  · 产品明细是**按箱展开**的（14 行 = 14 箱），同一 SKU 会重复出现，
    必须按 (fnsku) 汇总数量，不能直接 sum 全部行（会把「单箱数量」加 14 次）。
    —— 实测 144503079：14 行 × 18 / 20，页面自报「总数量 268」，汇总后正好对上。
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta

try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# 只抓这个前缀的参考单号（用户指定：参考单号以 14450 开头）
REF_PREFIX = "14450"
ACCOUNT = "PW-14450"

# 帕沃尔头程状态 → 本页统一状态码（与 OMS 的 1/2/3/4/5 对齐）
#   「海外仓待入库」= 还没进仓 = OMS 的「待入库」
#   「海外仓已入库」= 已经进仓能发 = OMS 的「已上架」
PW_STATUS = {
    "海外仓待入库": (1, "待入库"),
    "头程在途": (1, "待入库"),
    "海外仓已入库": (4, "已上架"),
    "已取消": (5, "已取消"),
    "作废": (5, "已取消"),
}

SIZE_RE = re.compile(r"-(XXS|XS|S|M|L|XL|XXL|XXXL|[2-6]XL)$", re.I)
TR_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def _cell(html: str) -> list[str]:
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()
            for t in TD_RE.findall(html)]


def _table(html: str, idx: int) -> str:
    """取第 idx 个 table（0=产品 1=装箱 2=轨迹 3=汇总）。"""
    m = re.search(r'<table id="table_%d"(.*?)</table>' % idx, html, re.S)
    return m.group(1) if m else ""


def parse_list(content: str) -> list[dict]:
    """列表 AJAX 的 content → 头程订单行。"""
    out = []
    for m in re.finditer(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', content or "", re.S):
        oid, body = m.group(1), m.group(2)
        c = _cell(body)
        if len(c) < 13:
            continue
        out.append({
            "orderId": oid,
            "inboundNo": c[1],      # 帕沃尔「入仓单号」（内部流水）
            "refNo": c[2],          # 参考单号 = 14450xxxxx（业务主键）
            "boxes": c[3],
            "weightLb": c[5],
            "whName": c[7],
            "arrivalMode": c[8],
            "headType": c[10],
            "statusText": c[11],
            "addTime": c[12],
        })
    return out


def parse_detail(html: str) -> dict:
    """详情 HTML → {eta, products[], traces[], warehousedTime, statusText}。"""
    plain = re.sub(r"<[^>]+>", " ", html)
    m = re.search(r"预计到货时间：\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", plain)
    eta = m.group(1) if m else ""
    m = re.search(r"状态：\s*([^\s<]{2,12})", plain)
    status = m.group(1) if m else ""

    # 产品：按箱重复，先收集 (fnsku,name,qty) 再按 fnsku 汇总
    prods: dict[str, dict] = {}
    for r in TR_ROW_RE.finditer(_table(html, 0)):
        c = _cell(r.group(1))
        if len(c) < 8 or not c[0].isdigit():
            continue
        fnsku, name, qty = c[2], c[3], c[6]
        if not fnsku:
            continue
        p = prods.setdefault(fnsku, {"fnsku": fnsku, "name": name, "qty": 0})
        try:
            p["qty"] += int(float(qty or 0))
        except ValueError:
            pass

    # 装箱信息：拿「海外仓入库时间」（同一单所有箱一般同一时间，取第一个非空）
    warehoused = ""
    for r in TR_ROW_RE.finditer(_table(html, 1)):
        c = _cell(r.group(1))
        if len(c) >= 9 and re.match(r"^\d{4}-", c[8] or ""):
            warehoused = c[8]
            break

    # 轨迹：两列（中文 / 英文），按时间倒序给前端，和恒信口径一致
    traces = []
    for r in TR_ROW_RE.finditer(_table(html, 2)):
        c = _cell(r.group(1))
        if len(c) >= 3 and re.match(r"^\d{4}-", c[1] or ""):
            traces.append({"time": c[1], "info": c[2]})
    traces.sort(key=lambda t: t["time"], reverse=True)

    return {
        "eta": eta,
        "statusText": status,
        "products": list(prods.values()),
        "warehousedTime": warehoused,
        "traces": traces,
    }


def pdate(s: str) -> date | None:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return None


def fetch(days: int = 30, log=print) -> list[dict]:
    """抓最近 days 天的帕沃尔头程订单（只看参考单号 14450 开头），带详情。"""
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "pw_orders", "lib"))
    from pw_session import PW  # noqa: E402

    pw = PW()
    pw.login()

    end = date.today()
    start = end - timedelta(days=days)
    base = {
        "act": "query_order_list", "page": "1", "page_size": "200",
        "waybill": "", "order_no": "",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "pb": "all", "order_types": "", "jhfs": "", "mdck": "", "chqd": "",
        "is_hui": "0", "order_pb": "0", "sort_by": "hwc_order.id", "sort_order": "DESC",
        "is_ni": "", "user_name": "", "keyword": "", "md_types": "", "is_ti": "0",
    }

    rows: list[dict] = []
    page = 1
    while True:
        d = dict(base, page=str(page))
        r = pw.post("order.php", d, referer="order.php?act=list&pb=all", json_resp=True)
        content = r.get("content") or ""
        got = parse_list(content)
        rows += got
        pages = int(r.get("page_count") or 1)
        if page >= pages or not got:
            break
        page += 1
    log(f"    帕沃尔头程 {start} ~ {end}：{len(rows)} 单")

    mine = [x for x in rows if x["refNo"].startswith(REF_PREFIX)]
    log(f"    其中参考单号 {REF_PREFIX} 开头：{len(mine)} 单")

    out = []
    for x in mine:
        try:
            html = pw.get("order.php?act=order_info&id=%s" % x["orderId"])
        except Exception as e:  # noqa: BLE001
            log(f"    ! {x['refNo']} 详情取失败：{str(e)[:80]}")
            continue
        d = parse_detail(html)
        out.append({**x, **d})
    log(f"    详情取到 {len(out)} 单")
    return out
