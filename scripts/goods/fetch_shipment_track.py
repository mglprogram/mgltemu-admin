# -*- coding: utf-8 -*-
"""fetch_shipment_track.py —— 「货件跟踪」：以**领星入库单号**为主键的全程跟踪 + 到港→上架预测。

用户需求（2026-11-02 改版）
---------------------------
「其他单号改成入库单号，因为可以通过领星查，所以直接从领星每天抓取入库单号跟踪数据更全」
「根据到港日期和签收日期帮我预测到港之后，平均几天能签收送仓上架」

⇒ 数据源彻底换向：**领星 OMS 入库单是主表**（不再是货代的 Excel）。
   Excel（D:\\together\\head_mile\\货件跟踪\\*.xlsx）只用来做**到港→签收的样本标定**。

三条数据流
----------
① 领星 OMS 入库单（主表，OpenAPI v1，两个账号都拉）
     · /openapi/v1/inboundOrder/pageList   pageSize ≤ 50（>100 报 10002）
     · /openapi/v1/inboundOrder/detail     批量 ≤ 50 个入库单号
   入库单号形如 IB106261031S0 / IB003261024RZ，前缀区分账号：
     LM739（ZOO WAREHOUSE ⑥美西）→ IB106…
     14450（帕沃尔 加州3仓）      → IB003…
   明细给 inboundSkuVOList[]（sku / productName / quantity）+ 状态时间
   （orderCreateTime / receivedEndTime / shelfEndTime / canceledTime）。

② 恒信轨迹（http://hengxe.nextsls.com/tracking/app，免登录，一次一个单号）
     /tracking/app?inajax=1&tracking_number=<入库单号>
   只用来拿**【到港】节点**和最新节点；恒信没有【到港】节点时回落到 Excel 的到港日期。
   ⚠ 帕沃尔（14450 / IB003…）的单在恒信查不到 —— 那批只用 OMS 状态，不编造轨迹。

③ 货件追踪表（D:\\together\\head_mile\\货件跟踪\\*.xlsx，历史留档）
   只取「到港日期 / 签收日期」两列做**标定样本**，不参与逐票展示。

预测口径
--------
    到港 → 送仓上架 的间隔，按**渠道**分别统计（中位数为主，均值/P80 备查）：
        美森   ≈ 4~5 天
        以星   ≈ 8~9 天
        盐田   ≈ 10~11 天
        未知   ≈ 全体中位数
    预测上架日 = 到港日 + 该渠道中位天数
    超期     = 今天 > 预测上架日，且 OMS 还没到「已上架」

渠道怎么认：OMS 入库单的 remark 字段（以星 / 盐田 / 美森），空则「未知」。
（实测 remark 与 Excel「渠道」列一一对应：以星→ZIMKP，盐田→TRK，美森→MLLKP。）

产物
----
    temu-admin/public/data/shipment-track.json   前端直接 fetch
    out/shipment-track/last.json                 同一份（排障用）

用法
----
    python scripts/goods/fetch_shipment_track.py              # 全量（两个账号）
    python scripts/goods/fetch_shipment_track.py --no-track   # 不查恒信（只看 OMS）
    python scripts/goods/fetch_shipment_track.py --account LM739
    python scripts/goods/fetch_shipment_track.py --print

退出码
------
    0 成功；2 两个账号都拉不到；3 解析到 0 张入库单；4 落盘失败
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_JSON = ROOT / "temu-admin" / "public" / "data" / "shipment-track.json"
OUT_DIR = ROOT / "out" / "shipment-track"
OMS_LIB = ROOT / "scripts" / "oms" / "lib"

# 历史货件追踪表留档目录（只用来标定「到港→签收」的天数）
EXCEL_DIRS = [Path(r"D:\together\head_mile\货件跟踪")]

try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

TRACK_API = "http://hengxe.nextsls.com/tracking/app"

# 恒信只覆盖 LM739 这家（ZOO WAREHOUSE ⑥美西）。
# 帕沃尔（14450 / IB003…，加州3仓）的单在恒信查不到，那是正常的，不算问题。
TRACK_ACCOUNT = "LM739"

# 14450 的领星 OpenAPI **已停用**（用户 2026-11-02：还在回数据但不再更新，
# 实测最新 orderCreateTime 停在 2026-10-24，此后新单进不来）。
# 所以这一路整体切换成「帕沃尔头程」抓，OMS 那边直接跳过 —— 否则会留一堆
# 永远不会变的僵尸行，把「待入库 228」这类数字撑大。
PW_DISABLED_ACCOUNT = "14450"

# 每个账号对应的**货代查件页**（用户 2026-11-02 给出 14450 的链接）。
# 恒信 API 拿不到的，就让人点这个链接自己去看 —— 比在页面上写「不覆盖」有用。
# 两边都支持 `numbers=` 预填并自动查询（实测 numbers= 比 number= 有效）。
TRACK_PAGE = {
    "LM739": "http://hengxe.nextsls.com/tracking/app#/tracking?numbers={no}",
    "14450": "https://tracking.nextsls.com/trace?app=62a9924e69c1d62f7a575c5b&numbers={no}",
}

# OMS 入库单状态
OMS_STATUS = {0: "草稿", 1: "待入库", 2: "收货中", 3: "已收货", 4: "已上架",
              5: "已取消", 6: "待审核", 7: "驳回"}

# 恒信物流状态
LOG_STATUS = {"ready": "已下单", "picked": "已提柜", "in_transit": "转运中",
              "delivered": "已签收", "received": "已收货", "exception": "异常"}

# 最新节点里出现这些词 → 当前就是异常/延迟
ABNORMAL_WORDS = ("延迟", "延误", "异常", "转船", "管控", "查验", "甩柜", "退关")

# 渠道（OMS remark）→ 展示名。remark 空 ⇒ 未知
CHANNEL_LABEL = {"以星": "以星 ZIMKP", "盐田": "盐田 TRK", "美森": "美森 MLLKP", "": "未知"}

# 到港→上架 的兜底中位数（样本不足时用；实测全体 7~9 天）
FALLBACK_LEAD = 8

# ★ 帕沃尔（14450）的「发货 → 上架」总天数，按渠道给（用户 2026-11-05 指定）：
#     以星 35 天 / 美森 25 天 / 盐田 45 天
#
# 为什么不能用领星那套「到港→上架」：帕沃尔侧**没有到港节点**，只有
# 「预计到货时间（ETA）」和「海外仓入库时间」。ETA 实测是「到港/到仓」而不是
# 「可发」，美森那批 ETA 一律 20 天、实际入库 22~28 天（中位 26.5），
# 所以 ETA 直接当上架日会系统性偏早。
# 用户给的是**从下单日（帕沃尔 addTime）起算**到能上架的整段，直接用它最省事，
# 也跟库存跟踪页悬浮里显示的渠道对得上。
PW_CHANNEL_DAYS = {"以星": 35, "美森": 25, "盐田": 45}
#: 渠道没标注时按三者中位推（并明确标出来，不装作知道）
PW_UNKNOWN_DAYS = 35

SIZE_RE = re.compile(r"-(XXS|XS|S|M|L|XL|XXL|XXXL|[2-6]XL)$", re.I)
ARRIVE_RE = re.compile(r"【到港】")
# 轨迹正文里的「已于10/21抵达」—— 这个 10/21 才是真正到港日。
# 节点自己的 time 是**录入时间**，实测会比真实到港晚一天
# （10-22 09:53 录入「已于10/21抵达USLSA」）。不解析它，到港日会系统性偏晚。
MMDD_RE = re.compile(r"已[于在]\s*([0-9]{1,2})/([0-9]{1,2})")


def arrive_from_trace(info: str, node_time: str) -> date | None:
    """从【到港】节点取真实到港日：优先正文里的 MM/DD，取不到才用节点时间。

    跨年按「节点时间的年份」定；MM/DD 比节点日期晚超过 180 天视为跨年。
    """
    node = pdate(node_time)
    m = MMDD_RE.search(info or "")
    if not m:
        return node
    try:
        mm, dd = int(m.group(1)), int(m.group(2))
        if not (1 <= mm <= 12 and 1 <= dd <= 31):
            return node
        base_year = node.year if node else date.today().year
        d = date(base_year, mm, dd)
        if node and (d - node).days > 180:
            d = date(base_year - 1, mm, dd)
        return d
    except ValueError:
        return node


def log(msg: str) -> None:
    print(msg, flush=True)


def pdate(s) -> date | None:
    """'2026-10-21 09:53:12' / datetime / '2026/10/21' → date"""
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", str(s).strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# --------------------------------------------------------------------------- ① 领星 OMS
def oms_accounts() -> list[tuple[str, str, str]]:
    acc_file = ROOT / "scripts" / "oms" / "oms_accounts.local.json"
    if not acc_file.exists():
        return []
    data = json.loads(acc_file.read_text(encoding="utf-8-sig"))
    return [(a.get("tag") or "?", a["appKey"], a["appSecret"])
            for a in (data.get("accounts") or [])
            if a.get("appKey") and a.get("appSecret")]


def fetch_inbound(only: str = "") -> list[dict]:
    """把两个账号的入库单全量拉下来（含 SKU 明细）。"""
    sys.path.insert(0, str(OMS_LIB))
    from oms_client import OmsClient  # noqa: E402

    out: list[dict] = []
    for tag, key, secret in oms_accounts():
        if only and tag != only:
            continue
        client = OmsClient(key, secret)
        nos: list[str] = []
        page = 1
        while True:
            try:
                d = client.post("/openapi/v1/inboundOrder/pageList",
                                {"page": page, "pageSize": 50})
            except Exception as e:  # noqa: BLE001
                log(f"    ! OMS[{tag}] 列表第 {page} 页失败：{str(e)[:110]}")
                break
            data = d.get("data") or {}
            recs = data.get("records") or []
            nos += [r["inboundOrderNo"] for r in recs if r.get("inboundOrderNo")]
            total = data.get("total") or 0
            if not recs or len(nos) >= total:
                break
            page += 1
        log(f"    OMS[{tag}] 入库单 {len(nos)} 张")

        # 详情批量（≤50 一批）
        for i in range(0, len(nos), 50):
            batch = nos[i:i + 50]
            try:
                r = client.post("/openapi/v1/inboundOrder/detail",
                                {"inboundOrderNoList": batch})
            except Exception as e:  # noqa: BLE001
                log(f"    ! OMS[{tag}] 详情第 {i // 50 + 1} 批失败：{str(e)[:110]}")
                continue
            for d in (r.get("data") or []):
                d["_account"] = tag
                out.append(d)

    # ⚠ 去重：pageList 翻页时若有新单插进来，同一张单会被翻到两次（实测 393 → 392）。
    #   按入库单号去重，后到的覆盖先到的（同样的单，内容一致）。
    seen: dict[str, dict] = {}
    for d in out:
        no = str(d.get("inboundOrderNo") or "")
        if no:
            seen[no] = d
    return list(seen.values())


# --------------------------------------------------------------------------- ② 恒信
#: 直连 opener。**必须显式清空代理**：
#: `urllib` 默认读 `HTTP(S)_PROXY` 环境变量，而本机那个值可能指向已经关掉的端口
#: （2026-11-05 实测：环境变量还写着 10808，代理软件已换到 7897 ⇒ 每个请求
#: ConnectionRefusedError，恒信轨迹 0/358 全灭）。恒信是境内域名，直连即可。
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url: str, timeout: int = 25, retries: int = 3) -> dict | None:
    last = None
    for n in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"),
                "Accept": "application/json, text/plain, */*",
            })
            with _DIRECT.open(req, timeout=timeout) as res:
                return json.loads(res.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            last = e
            if n + 1 < retries:
                import time
                time.sleep(0.5 * (n + 1))
    return None


def fetch_track(no: str) -> dict:
    if not no:
        return {"ok": False, "status": "", "statusText": "", "traces": []}
    url = f"{TRACK_API}?inajax=1&tracking_number={urllib.parse.quote(no)}"
    j = http_json(url)
    sh = ((j or {}).get("data") or {}).get("shipment") or {}
    traces = [{"time": str(t.get("time") or ""), "info": str(t.get("info") or "").strip()}
              for t in (sh.get("traces") or [])]
    st = str(sh.get("status") or "")
    return {
        "ok": bool(sh),
        "shipmentId": str(sh.get("shipment_id") or ""),
        "status": st,
        "statusText": LOG_STATUS.get(st, st),
        "parcels": sh.get("parcel_count"),
        "traces": traces,
    }


# --------------------------------------------------------------------------- ③ Excel 标定样本
def excel_samples() -> dict[str, dict]:
    """从历史货件追踪表取「到港日期 / 签收日期」，按入库单号索引。"""
    out: dict[str, dict] = {}
    for base in EXCEL_DIRS:
        if not base.exists():
            continue
        for p in sorted(base.glob("*.xlsx")):
            if p.name.startswith("~$"):
                continue
            try:
                import openpyxl
                wb = openpyxl.load_workbook(p, data_only=True)
            except Exception:  # noqa: BLE001
                continue
            for ws in wb.worksheets:
                head = {}
                for c in range(1, 16):
                    v = ws.cell(2, c).value
                    if v:
                        head[re.sub(r"\s+", "", str(v))] = c
                if "到港日期" not in head or "其他单号" not in head:
                    continue
                for r in range(3, ws.max_row + 1):
                    no = str(ws.cell(r, head["其他单号"]).value or "").strip()
                    if not no:
                        continue
                    arr = pdate(ws.cell(r, head["到港日期"]).value)
                    sig = pdate(ws.cell(r, head["签收日期"]).value) if "签收日期" in head else None
                    ch = ""
                    if "渠道" in head:
                        ch = str(ws.cell(r, head["渠道"]).value or "")
                    old = out.get(no)
                    # 优先保留「有签收日期」的那条；同样有则保留文件更新的
                    if old is None or (old["sign"] is None and sig is not None):
                        out[no] = {"arrive": arr, "sign": sig, "channelText": ch}
    return out


# --------------------------------------------------------------------------- 拼装
def channel_of(remark: str) -> str:
    r = (remark or "").strip()
    return r if r in ("以星", "盐田", "美森") else ""


def build_lead_stats(rows: list[dict], excel: dict[str, dict]) -> dict:
    """统计「到港 → 送仓上架」的天数，按渠道分组。

    样本两路合起来：
      · Excel：到港日期 → 签收日期（历史 7 份表，111 条有签收）
      · OMS ：恒信【到港】节点 → shelfEndTime（更实时，且覆盖新单）
    同一张单两路都有时只取 Excel（那是货代确认过的签收）。
    """
    by_ch: dict[str, list[int]] = defaultdict(list)
    allv: list[int] = []
    for r in rows:
        a = r.get("_arrive")
        if not a:
            continue
        shelf = pdate(r.get("shelfEndTime"))
        if not shelf:
            continue
        d = (shelf - a).days
        if d < 0 or d > 60:
            continue
        ch = channel_of(r.get("remark") or "")
        by_ch[ch].append(d)
        allv.append(d)

    # Excel 的历史样本（单号在 OMS 里也可能有，但 Excel 的签收是人工确认过的）
    for no, s in excel.items():
        if not s.get("arrive") or not s.get("sign"):
            continue
        d = (s["sign"] - s["arrive"]).days
        if d < 0 or d > 60:
            continue
        ch = ""
        txt = s.get("channelText") or ""
        for k in ("以星", "盐田", "美森"):
            if k in txt:
                ch = k
                break
        by_ch[ch].append(d)
        allv.append(d)

    def stat(v: list[int]) -> dict:
        if not v:
            return {"n": 0, "mean": None, "median": FALLBACK_LEAD,
                    "p80": None, "min": None, "max": None}
        v = sorted(v)
        return {
            "n": len(v),
            "mean": round(statistics.mean(v), 1),
            "median": int(statistics.median(v)),
            "p80": v[min(len(v) - 1, int(len(v) * 0.8))],
            "min": v[0],
            "max": v[-1],
        }

    overall = stat(allv)
    channels = {k: stat(v) for k, v in by_ch.items()}
    return {
        "overall": overall,
        "channels": channels,
        # 未知渠道用全体中位数兜底
        "fallback": overall["median"] or FALLBACK_LEAD,
        "note": "样本 = 历史货件追踪表（到港→签收）+ OMS（恒信到港→上架时间）；同一张单优先用表里人工确认的签收日期",
    }


def build_row(d: dict, track: dict, excel_row: dict | None, lead: dict) -> dict:
    no = str(d.get("inboundOrderNo") or "")
    st = d.get("status")
    remark = str(d.get("remark") or "").strip()
    ch = channel_of(remark)
    account = d.get("_account") or ""

    skus = []
    for s in (d.get("inboundSkuVOList") or []):
        name = str(s.get("productName") or "")
        m = SIZE_RE.search(name)
        skus.append({
            "sku": str(s.get("sku") or ""),
            "productName": name,
            "style": name[:m.start()] if m else name,
            "size": m.group(1).upper() if m else "",
            "qty": s.get("quantity") or 0,
            "receivedQty": s.get("receivedQuantity") or 0,
        })

    traces = track.get("traces") or []
    latest = traces[0] if traces else {"time": "", "info": ""}

    # 到港日期：恒信【到港】节点优先，没有则回落到 Excel 的到港日期
    arrive, arrive_src = None, ""
    for t in traces:
        if ARRIVE_RE.search(t["info"]):
            arrive = arrive_from_trace(t["info"], t["time"])
            if arrive:
                arrive_src = "hengxe"
                break
    if not arrive and excel_row and excel_row.get("arrive"):
        arrive = excel_row["arrive"]
        arrive_src = "excel"

    shelf = pdate(d.get("shelfEndTime"))
    recv = pdate(d.get("receivedEndTime"))
    exp_arrive = pdate(d.get("expectedDate"))

    # 预测：渠道中位数；没有到港日期就用 OMS 预计到货日推算
    ch_stat = lead["channels"].get(ch) or {}
    lead_days = ch_stat.get("median") or lead["fallback"]
    base = arrive or exp_arrive
    predicted = None
    if base:
        from datetime import timedelta
        predicted = base + timedelta(days=lead_days)

    today = date.today()
    wait_days = (today - arrive).days if arrive else None

    # 超期只对「真的到港了」判：没到港的单拿 OMS 预计到货日去推，是另一回事，
    # 用 issues 里的「超预计到货日未到港」表达，别混成一个数。
    overdue = None
    if arrive and predicted and st not in (3, 4, 5):
        overdue = (today - predicted).days  # >0 即超期

    issues = []
    if st == 5:
        issues.append("OMS已取消")
    # 「超期未上架」已经隐含了「已到港未上架」，两个标签并排会把格子撑爆
    # （2026-11-02 用户截图反馈：需关注列的标签溢出、盖住右侧固定列）。
    # 所以超期时只留超期这条，没超期才提示「已到港未上架」。
    if arrive and st in (1, 2) and overdue is not None and overdue > 0:
        issues.append(f"超期 +{overdue}天")
    elif arrive and st in (1, 2):
        issues.append("已到港未上架")
    if any(w in latest["info"] for w in ABNORMAL_WORDS):
        issues.append("物流当前异常")
    # 恒信只服务 LM739（ZOO 美西）。帕沃尔（14450 / IB003…）本来就不在恒信里，
    # 拿"无轨迹"当问题是误报 —— 那批只用 OMS 状态看。
    if account == TRACK_ACCOUNT and not track.get("ok"):
        issues.append("恒信无轨迹")
    if st in (1, 2) and exp_arrive and not arrive and today > exp_arrive:
        issues.append("超预计到货日未到港")

    return {
        # ---- 主键
        "inboundNo": no,
        "account": d.get("_account") or "",
        # ---- 归属
        "whCode": str(d.get("whCode") or ""),
        "whName": str(d.get("whName") or ""),
        "channel": ch,
        "channelText": CHANNEL_LABEL.get(ch, ch or "未知"),
        "remark": remark,
        # ---- OMS 状态与时间
        "status": st,
        "statusText": OMS_STATUS.get(st, str(st or "")),
        "createTime": str(d.get("orderCreateTime") or ""),
        "expectedDate": str(d.get("expectedDate") or ""),
        "receivedTime": str(d.get("receivedEndTime") or ""),
        "shelfTime": str(d.get("shelfEndTime") or ""),
        "canceledTime": str(d.get("canceledTime") or ""),
        # ---- 到港
        "arriveDate": arrive.isoformat() if arrive else "",
        "arriveSource": arrive_src,
        # ---- 产品
        "skus": skus,
        "skuCount": len(skus),
        "totalQty": sum(int(s["qty"] or 0) for s in skus),
        "receivedQty": sum(int(s["receivedQty"] or 0) for s in skus),
        # ---- 物流
        "logistics": {
            "ok": track.get("ok", False),
            "status": track.get("status", ""),
            "statusText": track.get("statusText", ""),
            "shipmentId": track.get("shipmentId", ""),
            "parcels": track.get("parcels"),
            "latest": latest,
            "currentAbnormal": any(w in latest["info"] for w in ABNORMAL_WORDS),
            "traceCount": len(traces),
            "traces": traces,
        },
        # 这一票所属账号的货代查件直链（恒信查不到时，页面上直接给入口）
        "trackUrl": TRACK_PAGE.get(account, "").replace("{no}", urllib.parse.quote(no)) if account else "",
        # ---- 预测
        "leadDays": lead_days,
        "predictedShelf": predicted.isoformat() if predicted else "",
        "predictedBasis": "到港日" if arrive else ("OMS预计到货" if exp_arrive else ""),
        "waitDays": wait_days,
        "overdueDays": overdue if (overdue is not None and overdue > 0) else 0,
        "issues": issues,
    }


def build_payload(rows: list[dict], lead: dict, accounts: list[str], generated: str) -> dict:
    def cnt(f):
        return sum(1 for r in rows if f(r))

    by_status: dict[str, int] = defaultdict(int)
    by_channel: dict[str, int] = defaultdict(int)
    for r in rows:
        by_status[r["statusText"] or "—"] += 1
        by_channel[r["channelText"]] += 1

    waiting = [r for r in rows if r["status"] in (1, 2)]
    overdue = [r for r in waiting if r["overdueDays"] > 0]
    arrived_waiting = [r for r in waiting if r["arriveDate"]]

    return {
        "generatedAt": generated,
        "source": {
            "primary": "领星 OMS 入库单（inboundOrder）· 两个账号全量",
            "accounts": accounts,
            "tracking": "恒信 http://hengxe.nextsls.com/tracking/app（按入库单号查【到港】与最新节点）",
            "calibration": "D:\\together\\head_mile\\货件跟踪\\*.xlsx 的「到港日期 / 签收日期」",
        },
        "leadTime": lead,
        "summary": {
            "total": len(rows),
            "waiting": len(waiting),
            "onShelf": cnt(lambda r: r["status"] == 4),
            "receiving": cnt(lambda r: r["status"] == 2),
            "received": cnt(lambda r: r["status"] == 3),
            "canceled": cnt(lambda r: r["status"] == 5),
            "arrived": cnt(lambda r: bool(r["arriveDate"])),
            "notArrived": cnt(lambda r: not r["arriveDate"]),
            "arrivedWaiting": len(arrived_waiting),
            "overdue": len(overdue),
            "abnormal": cnt(lambda r: r["logistics"]["currentAbnormal"]),
            "noTrace": cnt(lambda r: not r["logistics"]["ok"]),
            "issues": cnt(lambda r: bool(r["issues"])),
            "totalQty": sum(r["totalQty"] for r in rows),
            "waitingQty": sum(r["totalQty"] for r in waiting),
            "byStatus": dict(by_status),
            "byChannel": dict(by_channel),
        },
        "rows": rows,
    }


def pw_rows(lead: dict, log=print) -> list[dict]:
    """14450 改从**帕沃尔头程**抓（领星 14450 已停用，见 2026-11-02 用户说明）。

    帕沃尔这一路没有「到港」节点，只有「预计到货时间（ETA）」和
    「海外仓入库时间」。所以：
      · 渠道来自金山发货申请表（ref-channel.json，库存跟踪页悬浮用的同一份）
      · 上架日 = **帕沃尔下单日 + 该渠道总天数**（用户 2026-11-05 给定：
        以星 35 / 美森 25 / 盐田 45），不再拿 ETA 当基准 —— ETA 实测是
        「到港」不是「能上架」，美森 ETA 20 天而实际入库 22~28 天。
      · shelfTime 用「海外仓入库时间」
      · arriveDate 留空（不编造到港日期）
    """
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "pw_orders"))
        from pw_inbound_track import fetch as pw_fetch  # noqa: E402
    except Exception as e:  # noqa: BLE001
        log(f"    ! 帕沃尔模块加载失败：{str(e)[:90]}")
        return []

    try:
        raw = pw_fetch(30, log=log)
    except Exception as e:  # noqa: BLE001
        log(f"    ! 帕沃尔抓取失败：{str(e)[:110]}")
        return []

    from pw_inbound_track import PW_STATUS  # noqa: E402

    # 参考单号 → 渠道（美森/以星/盐田）。帕沃尔系统没有渠道，这份来自
    # 金山「2026发货申请表」，由 scripts/goods/fetch_ref_channel.py 每天生成。
    chan: dict[str, str] = {}
    chan_file = PUBLIC_JSON.parent / "ref-channel.json"
    try:
        chan = json.loads(chan_file.read_text(encoding="utf-8")).get("map") or {}
    except Exception as e:  # noqa: BLE001
        log(f"    ! 渠道表读不到（预测退回未知渠道天数）：{str(e)[:70]}")

    out: list[dict] = []
    today = date.today()
    fallback = lead["fallback"]
    hit_ch = 0
    full = part = none = 0
    for d in raw:
        # ★ 「上架」判据（用户 2026-11-05 定的口径，比状态文字准）：
        #   已入箱数 == 总箱数 ⇒ 已上架；== 0 ⇒ 未上架；中间 ⇒ 部分入库。
        #   帕沃尔的「状态」有时还写「海外仓待入库」，但箱数已经在动了，
        #   所以以箱数为准；「已取消/作废」仍按状态走（那些单箱数没意义）。
        raw_state = str(d.get("statusText") or "")
        if raw_state in ("已取消", "作废"):
            st, st_text = PW_STATUS.get(raw_state, (5, raw_state))
            shelf_state = "已取消"
        else:
            shelf_state = str(d.get("shelfState") or "未上架")
            if shelf_state == "已上架":
                st, st_text = 4, "已上架"
            elif shelf_state == "部分入库":
                st, st_text = 2, "收货中"
            else:
                st, st_text = 1, "待入库"
        total_b = int(d.get("totalBoxes") or 0)
        in_b = int(d.get("inBoxCount") or 0)
        if shelf_state == "已上架":
            full += 1
        elif shelf_state == "部分入库":
            part += 1
        else:
            none += 1

        skus = []
        for p in d.get("products") or []:
            name = str(p.get("name") or "")
            m = SIZE_RE.search(name)
            skus.append({
                "sku": str(p.get("fnsku") or ""),
                "productName": name,
                "style": name[:m.start()] if m else name,
                "size": m.group(1).upper() if m else "",
                "qty": int(p.get("qty") or 0),
                # 已收货数量按**箱数比例**给（全入 = 全收，部分入库按比例）
                "receivedQty": (int(p.get("qty") or 0)
                                if (st == 4 or (st == 2 and total_b and in_b >= total_b))
                                else (round(int(p.get("qty") or 0) * in_b / total_b)
                                      if (st == 2 and total_b) else 0)),
            })
        traces = d.get("traces") or []
        latest = traces[0] if traces else {"time": "", "info": ""}
        eta = pdate(d.get("eta"))
        shelf = pdate(d.get("warehousedTime"))
        added = pdate(d.get("addTime"))

        # 渠道 → 整段天数（下单 → 能上架）。没标注的按中位推，并在 basis 里写明。
        ch = chan.get(str(d["refNo"]), "")
        ch_label = CHANNEL_LABEL.get(ch, "未知") if ch else "未知"
        days = PW_CHANNEL_DAYS.get(ch, PW_UNKNOWN_DAYS)
        if ch:
            hit_ch += 1
        # 已入库的按实际入库日算基线，没入库的按下单日 + 整段天数
        predicted = shelf if shelf else ((added + timedelta(days=days)) if added else None)
        # 页面上会渲染成「{basis} + {leadDays} 天」，所以这里只给基准名
        basis = (f"帕沃尔下单·{ch}" if ch else "帕沃尔下单·渠道未知")

        issues = []
        if st == 5:
            issues.append("已取消")
        elif st == 2:
            # 部分入库：货到了一部分，是最该盯的中间态
            issues.append(f"部分入库 {in_b}/{total_b} 箱")
        elif st == 1 and not ch:
            issues.append("渠道未标注")
        elif st == 1 and predicted and today > predicted:
            issues.append(f"超期 +{(today - predicted).days}天")
        elif st == 1 and eta and today > eta:
            issues.append(f"超预计到货 +{(today - eta).days}天")

        out.append({
            "inboundNo": d["refNo"],
            "account": "14450",
            "source": "帕沃尔头程",
            "pwOrderId": d.get("orderId", ""),
            "whCode": "powerCA3",
            "whName": d.get("whName") or "加州3仓",
            "channel": ch,
            "channelText": ch_label,
            "remark": "",
            "status": st,
            "statusText": st_text,
            # 按箱数判的上架进度（用户口径）：已上架 / 部分入库 / 未上架 / 已取消
            "pwShelfState": shelf_state,
            "boxes": total_b,
            "inBoxes": in_b,
            "createTime": d.get("addTime", ""),
            "expectedDate": d.get("eta", ""),
            "receivedTime": d.get("warehousedTime", ""),
            "shelfTime": d.get("warehousedTime", ""),
            "canceledTime": "",
            "arriveDate": "",
            "arriveSource": "",
            "skus": skus,
            "skuCount": len(skus),
            "totalQty": sum(s["qty"] for s in skus),
            "receivedQty": sum(s["receivedQty"] for s in skus),
            "logistics": {
                "ok": bool(traces),
                "status": "in_transit" if st == 1 else ("delivered" if st == 4 else ""),
                "statusText": st_text,
                "shipmentId": d.get("inboundNo", ""),
                "parcels": int(d["boxes"]) if str(d.get("boxes", "")).isdigit() else None,
                "latest": latest,
                "currentAbnormal": False,
                "traceCount": len(traces),
                "traces": traces,
            },
            "trackUrl": ("https://www.powerwarehouse.net/order.php?act=order_info&id=%s"
                         % d.get("orderId", "")) if d.get("orderId") else "",
            "leadDays": days,
            "predictedShelf": predicted.isoformat() if predicted else "",
            "predictedBasis": basis,
            "waitDays": (today - added).days if added else None,
            "overdueDays": ((today - predicted).days
                            if (st == 1 and predicted and today > predicted) else 0),
            "issues": issues,
        })
    if out:
        log(f"    渠道命中 {hit_ch}/{len(out)} 张（以星 {PW_CHANNEL_DAYS['以星']} 天 / "
            f"美森 {PW_CHANNEL_DAYS['美森']} 天 / 盐田 {PW_CHANNEL_DAYS['盐田']} 天）")
    return out


def prune_rows(rows: list[dict], keep_days: int = 3, log=print) -> list[dict]:
    """清掉已经没用的行（用户 2026-11-05：「超过三天上架的单号和 OMS 已取消的删除」）。

    两条规则：
      · **已上架满 keep_days 天**（上架日距今 ≥ 3 天）→ 删。货已经在仓里能卖，
        留在「货件跟踪」里只会稀释待催的单；要看历史去「已上架」视图或帕沃尔后台。
      · **已取消** → 删。OMS/帕沃尔取消了就不是货件，留着只是噪音。
        以前它还被算进「需关注」，导致那格数字虚高。

    ⚠ 拿不到 shelfTime 的「已上架」**不删** —— 分不清是刚上架还是缺字段，
      宁可留着让人看见（删了就再也找不回来）。帕沃尔那批的 shelfTime 取
      「海外仓入库时间」，箱数判为已上架但没有入库时间的，同样留着。
    ⚠ 用「≥ keep_days」而不是 "> keep_days"：今天上架、昨天上架、前天上架都保留
      （前天上架距今天正好 2 天），第 3 天那次抓取才清掉。
    """
    today = date.today()
    kept: list[dict] = []
    drop_shelved = drop_canceled = 0
    for r in rows:
        if r.get("status") == 5:
            drop_canceled += 1
            continue
        if r.get("status") == 4:
            s = pdate(r.get("shelfTime"))
            if s and (today - s).days >= keep_days:
                drop_shelved += 1
                continue
        kept.append(r)
    if drop_shelved or drop_canceled:
        log(f"⑤ 清理：已上架满 {keep_days} 天 {drop_shelved} 张｜已取消 {drop_canceled} 张"
            f" → 剩 {len(kept)} 张")
    return kept


def main() -> int:
    ap = argparse.ArgumentParser(description="货件跟踪：领星入库单 × 恒信轨迹 × 到港→上架预测")
    ap.add_argument("--account", default="", help="只跑某个 OMS 账号（LM739 / 14450）")
    ap.add_argument("--no-track", action="store_true", help="不查恒信轨迹")
    ap.add_argument("--print", dest="do_print", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 张（调试）")
    ap.add_argument("--keep-shelved-days", type=int, default=3,
                    help="已上架满 N 天后从跟踪表里清掉（用户 2026-11-05 定 3 天）")
    a = ap.parse_args()

    log("① 拉领星 OMS 入库单（主表）…")
    raw = fetch_inbound(a.account)
    log(f"  共 {len(raw)} 张入库单")
    if not raw:
        log("两个账号都拉不到入库单")
        return 2
    if a.limit:
        raw = raw[:a.limit]

    log("② 查恒信轨迹（拿【到港】节点与最新节点）…")
    tracks: dict[str, dict] = {}
    if a.no_track:
        tracks = {str(d["inboundOrderNo"]): {"ok": False, "status": "", "statusText": "", "traces": []}
                  for d in raw}
    else:
        # 恒信只覆盖 LM739（ZOO 美西）；帕沃尔那批查了必然全空，白打 68 次请求
        nos = [str(d["inboundOrderNo"]) for d in raw
               if (d.get("_account") or TRACK_ACCOUNT) == TRACK_ACCOUNT]
        skipped = len(raw) - len(nos)
        done = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            for no, tr in zip(nos, pool.map(fetch_track, nos)):
                tracks[no] = tr
                done += 1
                if done % 50 == 0:
                    log(f"    {done}/{len(nos)}")
        ok = sum(1 for t in tracks.values() if t.get("ok"))
        log(f"  轨迹查到 {ok}/{len(set(nos))}"
            + (f"（另有 {skipped} 张属于帕沃尔，恒信不覆盖，按设计跳过）" if skipped else ""))

    log("③ 读历史追踪表做「到港→签收」标定…")
    excel = excel_samples()
    log(f"  历史样本 {len(excel)} 张单（其中 {sum(1 for v in excel.values() if v.get('sign'))} 张有签收日期）")

    # 先算标定（要用到 rows 的 OMS 时间），再拼行
    provisional = []
    for d in raw:
        no = str(d.get("inboundOrderNo") or "")
        provisional.append({**d, "_arrive": None})
    for r in provisional:
        no = r["inboundOrderNo"]
        tr = tracks.get(str(no)) or {}
        arr = None
        for t in (tr.get("traces") or []):
            if ARRIVE_RE.search(t["info"]):
                arr = arrive_from_trace(t["info"], t["time"])
                if arr:
                    break
        if not arr:
            e = excel.get(str(no))
            arr = e.get("arrive") if e else None
        r["_arrive"] = arr
    lead = build_lead_stats(provisional, excel)
    log(f"  到港→上架：全体 n={lead['overall']['n']} 中位 {lead['overall']['median']} 天；"
        + " ｜ ".join(f"{CHANNEL_LABEL.get(k, k or '未知')} {v['median']}天(n={v['n']})"
                      for k, v in lead["channels"].items()))

    rows = [build_row(d, tracks.get(str(d.get("inboundOrderNo"))) or {},
                      excel.get(str(d.get("inboundOrderNo"))), lead)
            for d in raw
            # 14450 已停用领星，改由帕沃尔头程提供（见 ④），这里剔除避免重复
            if (d.get("_account") or "") != PW_DISABLED_ACCOUNT]

    log("④ 抓帕沃尔头程（14450 的领星已停用，改从这里取）…")
    pw = pw_rows(lead, log=log)
    if pw:
        log(f"  帕沃尔头程 {len(pw)} 单（参考单号 14450 开头，近 30 天）")
        rows = rows + pw

    # ⑤ 清理：已上架满 3 天、已取消的都不再留在跟踪表里
    rows = prune_rows(rows, keep_days=a.keep_shelved_days, log=log)

    accounts = sorted({r["account"] for r in rows if r["account"]})
    payload = build_payload(rows, lead, accounts, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    try:
        PUBLIC_JSON.parent.mkdir(parents=True, exist_ok=True)
        PUBLIC_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "last.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    except OSError as e:
        log(f"落盘失败：{e}")
        return 4

    s = payload["summary"]
    log(f"  → {PUBLIC_JSON.relative_to(ROOT)}  {PUBLIC_JSON.stat().st_size / 1024:.1f} KB")
    log(f"  合计 {s['total']} 张｜待入库 {s['waiting']}｜已上架 {s['onShelf']}｜已取消 {s['canceled']}｜"
        f"已到港未上架 {s['arrivedWaiting']}｜超期 {s['overdue']}｜物流异常 {s['abnormal']}")

    if a.do_print:
        for r in sorted(rows, key=lambda x: (-x["overdueDays"], x["inboundNo"]))[:40]:
            print(f"{r['inboundNo']:<16} {r['statusText']:<5} {r['channelText']:<10} "
                  f"到港{r['arriveDate'] or '—':<11} 预测{r['predictedShelf'] or '—':<11} "
                  f"超期{r['overdueDays']:<3} {r['issues']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
