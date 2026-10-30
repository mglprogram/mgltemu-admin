# -*- coding: utf-8 -*-
"""fetch_shipment_track.py —— 「货件跟踪」：货件追踪表 × 恒信轨迹 × 领星OMS入库明细。

用户需求（2026-10-30）
----------------------
「加一栏：货件跟踪，把他更新到我的系统」
  · 数据源：博盈通豪一件代发 货件追踪表（微信收到的 xlsx，每个 sheet 一个月）
  · 优先查到港日期有值的；**没到港的单也要跟踪**（都要有最新物流节点）
  · 每票还要看「是什么产品 / 尺码 / 数量」→ 走领星 OMS 入库单明细

三条数据流
----------
① 货件追踪表（Excel）
   列：序号/运单号/其他单号/渠道/发FBA仓/数量(件)/发货品名/计费重/船名船次/
       入仓日期/开船日期/到港日期/签收日期/现轨迹
   **其他单号（IB...）= 货代给的入库单号**，后面两条流都用它做 key。

② 恒信轨迹（http://hengxe.nextsls.com/tracking/app）
   实测接口是 GET + 查询串，**不需要登录**、一次只能一个单号：
       /tracking/app?inajax=1&tracking_number=<其他单号>
   返回 data.shipment.{status, parcel_count, traces[{time,info}]}，
   traces 按时间倒序，[0] 就是最新节点。
   ⚠ 该接口偶发返回 status:0（"操作失败"）或空 data —— 必须重试，
     直接当"没轨迹"会误判成查不到。

③ 领星 OMS 入库单（OpenAPI v1，账号见 scripts/oms/oms_accounts.local.json）
   · /openapi/v1/inboundOrder/pageList   分页（pageSize ≤ 50，>100 报 10002）
   · /openapi/v1/inboundOrder/detail     批量 ≤ 50 个入库单号
   明细里 inboundSkuVOList[] 给 sku / productName / quantity —— 这就是
   「什么产品·什么尺码·多少件」。productName 形如 BS2-203802-4SetB-L，
   末段就是尺码，前面是款号+套系。

产物
----
    temu-admin/public/data/shipment-track.json   前端直接 fetch
    out/shipment-track/last.json                 同一份（排障用，带原始 traces）

用法
----
    python scripts/goods/fetch_shipment_track.py                 # 自动挑最新一份追踪表
    python scripts/goods/fetch_shipment_track.py --file <xlsx>   # 指定文件
    python scripts/goods/fetch_shipment_track.py --no-oms        # 只跑物流，不查 OMS
    python scripts/goods/fetch_shipment_track.py --print         # 顺便打一张表

退出码
------
    0 成功；2 找不到追踪表；3 解析到 0 票；4 落盘失败
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_JSON = ROOT / "temu-admin" / "public" / "data" / "shipment-track.json"
OUT_DIR = ROOT / "out" / "shipment-track"
OMS_LIB = ROOT / "scripts" / "oms" / "lib"

try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# 微信缓存里「博盈通豪一件代发 货件追踪表-*.xlsx」，按修改时间取最新一份。
# 用户 2026-10-30 明确：「我给你的是最新的货件追踪表」——所以默认取最新，
# 但允许 --file 覆盖（历史版本复算、或换别家货代表）。
WECHAT_GLOB_DIRS = [
    Path(r"D:\微信\缓存\xwechat_files"),
    Path.home() / "Documents" / "WeChat Files",
]
WORKBOOK_PATTERN = "*货件追踪表*.xlsx"
WORKBOOK_PREFER = "博盈通豪"

TRACK_API = "http://hengxe.nextsls.com/tracking/app"

# OMS 入库单状态（接口只给数字）
OMS_STATUS = {
    0: "草稿", 1: "待入库", 2: "收货中", 3: "已收货",
    4: "已上架", 5: "已取消", 6: "待审核", 7: "驳回",
}
# 恒信物流状态
LOG_STATUS = {
    "ready": "已下单", "in_transit": "转运中", "delivered": "已签收",
    "received": "已收货", "exception": "异常", "returned": "退件",
}

# 最新节点里出现这些词 → 当前就是异常/延迟，要单独喊出来
ABNORMAL_WORDS = ("延迟", "异常", "转船", "管控", "查验", "甩柜", "退关")

SIZE_RE = re.compile(r"-(XXS|XS|S|M|L|XL|XXL|XXXL|[2-6]XL)$", re.I)

# 表头 → 字段（表里列名带换行，先去掉空白再匹配）
COLUMNS = {
    "序号": "seq",
    "运单号": "trackingNo",
    "其他单号": "otherNo",
    "渠道": "channel",
    "发FBA仓": "fbaWarehouse",
    "数量(件)": "qty",
    "发货品名": "goods",
    "计费重(KG)": "weightKg",
    "船名船次": "vessel",
    "入仓日期": "inboundDate",
    "开船日期": "etdDate",
    "到港日期": "arriveDate",
    "签收日期": "signDate",
    "现轨迹": "excelTrace",
}


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- 文件定位
def find_workbook() -> Path | None:
    """按修改时间挑最新一份货件追踪表；同一天优先「博盈通豪」。"""
    hits: list[Path] = []
    for base in WECHAT_GLOB_DIRS:
        if not base.exists():
            continue
        try:
            hits.extend(p for p in base.rglob(WORKBOOK_PATTERN) if p.is_file())
        except OSError:
            continue
    if not hits:
        return None
    # 先按 mtime 倒序，取最新那天的；若同一天还有别家，优先博盈通豪
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    newest_day = datetime.fromtimestamp(hits[0].stat().st_mtime).strftime("%Y-%m-%d")
    same_day = [p for p in hits
                if datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d") == newest_day]
    for p in same_day:
        if WORKBOOK_PREFER in p.name:
            return p
    return hits[0]


# --------------------------------------------------------------------------- Excel
def cell_date(v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return s


def read_workbook(path: Path) -> tuple[list[dict], list[str]]:
    """读所有 sheet。表头固定在第 2 行（第 1 行是「货物追踪表」大标题）。"""
    import openpyxl  # 延迟导入：--help 不需要它

    wb = openpyxl.load_workbook(path, data_only=True)
    rows: list[dict] = []
    sheets: list[str] = []
    for ws in wb.worksheets:
        head = {}
        for c in range(1, ws.max_column + 1):
            raw = ws.cell(2, c).value
            if raw is None:
                continue
            key = re.sub(r"\s+", "", str(raw))
            if key in COLUMNS:
                head[COLUMNS[key]] = c
        if "otherNo" not in head or "trackingNo" not in head:
            continue                      # 不是货件表（比如汇总页）
        sheets.append(ws.title)
        for r in range(3, ws.max_row + 1):
            def g(field: str):
                c = head.get(field)
                return ws.cell(r, c).value if c else None

            tracking, other = g("trackingNo"), g("otherNo")
            if not tracking and not other:
                continue
            # 合并单元格的尾巴会带出「None」字面量，统一当空
            def clean(v) -> str:
                s = "" if v is None else str(v).strip()
                return "" if s.lower() in ("none", "nan", "-") else s

            rows.append({
                "sheet": ws.title,
                "seq": clean(g("seq")),
                "trackingNo": clean(tracking),
                "otherNo": clean(other),
                "channel": clean(g("channel")),
                "fbaWarehouse": clean(g("fbaWarehouse")),
                "qty": g("qty") if isinstance(g("qty"), (int, float)) else clean(g("qty")),
                "goods": clean(g("goods")),
                "weightKg": g("weightKg") if isinstance(g("weightKg"), (int, float)) else None,
                "vessel": clean(g("vessel")),
                "inboundDate": cell_date(g("inboundDate")),
                "etdDate": cell_date(g("etdDate")),
                "arriveDate": cell_date(g("arriveDate")),
                "signDate": cell_date(g("signDate")),
                "excelTrace": clean(g("excelTrace")),
            })
    return rows, sheets


# --------------------------------------------------------------------------- 恒信轨迹
def http_json(url: str, timeout: int = 25, retries: int = 3) -> dict | None:
    last = None
    for n in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"),
                "Accept": "application/json, text/plain, */*",
                "Referer": TRACK_API.replace("?inajax=1", ""),
            })
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            last = e
            if n + 1 < retries:
                # 恒信偶发 status:0 / 空 data，退避一下再打
                import time
                time.sleep(0.6 * (n + 1))
    log(f"    ! 轨迹查询失败 {url[-24:]}：{last}")
    return None


def _query_track(no: str) -> dict:
    """按单号打一次恒信接口，返回 shipment 字典（查不到给 {}）。"""
    if not no:
        return {}
    url = f"{TRACK_API}?inajax=1&tracking_number={urllib.parse.quote(no)}"
    j = http_json(url)
    return ((j or {}).get("data") or {}).get("shipment") or {}


def fetch_track(no: str, alt: str = "") -> dict:
    """一票的物流。返回 {status,statusText,parcels,traces[],ok}

    ⚠ 恒信系统里这票登记的「单号」不一定是追踪表的「其他单号」：
      2026-08 那两票（HX00031RT/HX00031RV）在恒信是按**运单号**登记的，
      用其他单号 IB106260731RT 查只会拿到 status:0「操作失败」。
      所以其他单号查不到时，退回用运单号再查一次。
    """
    if not no:
        return {"ok": False, "status": "", "statusText": "", "parcels": None, "traces": []}
    sh = _query_track(no)
    matchedBy = "otherNo"
    if not sh and alt and alt != no:
        sh = _query_track(alt)
        matchedBy = "trackingNo" if sh else "otherNo"
    traces = [{"time": str(t.get("time") or ""), "info": str(t.get("info") or "").strip()}
              for t in (sh.get("traces") or [])]
    st = str(sh.get("status") or "")
    return {
        "ok": bool(sh),
        "matchedBy": matchedBy if sh else "",
        "shipmentId": str(sh.get("shipment_id") or ""),
        "status": st,
        "statusText": LOG_STATUS.get(st, st),
        "country": str(sh.get("country") or ""),
        "postcode": str(sh.get("postcode") or ""),
        "parcels": sh.get("parcel_count"),
        "traces": traces,
    }


# --------------------------------------------------------------------------- 领星 OMS
def fetch_oms(numbers: list[str], chunk: int = 50) -> dict[str, dict]:
    """批量取入库单明细。两个账号都试（单据按店铺分在两个账号里）。"""
    sys.path.insert(0, str(OMS_LIB))
    from oms_client import OmsClient  # noqa: E402

    accounts = []
    acc_file = ROOT / "scripts" / "oms" / "oms_accounts.local.json"
    if acc_file.exists():
        for a in (json.loads(acc_file.read_text(encoding="utf-8")).get("accounts") or []):
            if a.get("appKey") and a.get("appSecret"):
                accounts.append((a.get("tag") or "?", a["appKey"], a["appSecret"]))
    if not accounts:
        log("    ! 没有 OMS 凭证（scripts/oms/oms_accounts.local.json），跳过 OMS")
        return {}

    want = [n for n in dict.fromkeys(numbers) if n]
    out: dict[str, dict] = {}
    for tag, key, secret in accounts:
        client = OmsClient(key, secret)
        todo = [n for n in want if n not in out]
        if not todo:
            break
        for i in range(0, len(todo), chunk):
            batch = todo[i:i + chunk]
            try:
                res = client.post("/openapi/v1/inboundOrder/detail",
                                  {"inboundOrderNoList": batch})
            except Exception as e:  # noqa: BLE001
                log(f"    ! OMS[{tag}] 批次 {i // chunk + 1} 失败：{str(e)[:110]}")
                continue
            for d in (res.get("data") or []):
                no = str(d.get("inboundOrderNo") or "")
                if not no:
                    continue
                st = d.get("status")
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
                    })
                out[no] = {
                    "found": True,
                    "account": tag,
                    "status": st,
                    "statusText": OMS_STATUS.get(st, str(st or "")),
                    "whName": str(d.get("whName") or ""),
                    "whCode": str(d.get("whCode") or ""),
                    "expectedDate": cell_date(d.get("expectedDate")),
                    "orderCreateTime": str(d.get("orderCreateTime") or ""),
                    "receivedEndTime": str(d.get("receivedEndTime") or ""),
                    "shelfEndTime": str(d.get("shelfEndTime") or ""),
                    "canceledTime": str(d.get("canceledTime") or ""),
                    "remark": str(d.get("remark") or ""),
                    "trackingNo": str(d.get("trackingNo") or ""),
                    "referOrderNo": str(d.get("referOrderNo") or ""),
                    "skus": skus,
                    "skuCount": len(skus),
                    "totalQty": sum(int(s.get("qty") or 0) for s in skus),
                }
    for n in want:
        out.setdefault(n, {"found": False, "statusText": "未找到", "skus": [], "skuCount": 0,
                           "totalQty": 0, "whName": "", "expectedDate": ""})
    return out


# --------------------------------------------------------------------------- 拼装
def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def build_row(row: dict, track: dict, oms: dict) -> dict:
    traces = track.get("traces") or []
    latest = traces[0] if traces else {"time": "", "info": ""}
    abn = next((t["info"] for t in traces if any(w in t["info"] for w in ABNORMAL_WORDS)), "")

    # 表内「现轨迹」是否落后于恒信最新节点：两边都归一化后互相包含就算一致
    ex, ln = norm(row.get("excelTrace")), norm(latest["info"])
    lag = bool(ln and ex and ex[:16] not in ln[:40] and ln[:16] not in ex[:40])

    arrived = bool(row.get("arriveDate"))
    oms_st = oms.get("status")
    issues = []
    if oms_st == 5:
        issues.append("OMS已取消但物流在途" if not track.get("status") == "delivered" else "OMS已取消")
    if arrived and oms_st == 1:
        issues.append("已到港未上架")
    if any(w in latest["info"] for w in ABNORMAL_WORDS):
        issues.append("物流当前异常")
    if not arrived and track.get("status") == "delivered":
        issues.append("表内无到港但已签收")
    if lag:
        issues.append("表内轨迹落后")
    if not oms.get("found"):
        issues.append("OMS无此入库单")

    return {
        **row,
        "arrived": arrived,
        "logistics": {
            "ok": track.get("ok", False),
            "matchedBy": track.get("matchedBy", ""),
            "status": track.get("status", ""),
            "statusText": track.get("statusText", ""),
            "shipmentId": track.get("shipmentId", ""),
            "country": track.get("country", ""),
            "postcode": track.get("postcode", ""),
            "parcels": track.get("parcels"),
            "latest": latest,
            "latestAbnormal": abn,
            "currentAbnormal": any(w in latest["info"] for w in ABNORMAL_WORDS),
            "lag": lag,
            "traceCount": len(traces),
            "traces": traces,
        },
        "oms": {k: v for k, v in oms.items() if k != "found"} | {"found": oms.get("found", False)},
        "issues": issues,
    }


def build_payload(rows: list[dict], sheets: list[str], src: Path, generated_at: str) -> dict:
    arrived = [r for r in rows if r["arrived"]]
    open_rows = [r for r in rows if not r["arrived"]]
    by_status: dict[str, int] = {}
    by_oms: dict[str, int] = {}
    for r in rows:
        by_status[r["logistics"]["statusText"] or "无轨迹"] = by_status.get(
            r["logistics"]["statusText"] or "无轨迹", 0) + 1
        by_oms[r["oms"]["statusText"] or "—"] = by_oms.get(r["oms"]["statusText"] or "—", 0) + 1
    issue_rows = [r for r in rows if r["issues"]]
    return {
        "generatedAt": generated_at,
        "source": {
            "workbook": src.name,
            "workbookPath": str(src),
            "workbookModified": datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "sheets": sheets,
            "tracking": "恒信 http://hengxe.nextsls.com/tracking/app（按其他单号查）",
            "oms": "领星 OMS OpenAPI v1 · inboundOrder（入库单明细：产品/尺码/数量）",
        },
        "summary": {
            "total": len(rows),
            "arrived": len(arrived),
            "notArrived": len(open_rows),
            "delivered": sum(1 for r in rows if r["logistics"]["status"] == "delivered"),
            "inTransit": sum(1 for r in rows if r["logistics"]["status"] == "in_transit"),
            "abnormal": sum(1 for r in rows if r["logistics"]["currentAbnormal"]),
            "lag": sum(1 for r in rows if r["logistics"]["lag"]),
            "omsOnShelf": sum(1 for r in rows if r["oms"]["status"] == 4),
            "omsPending": sum(1 for r in rows if r["oms"]["status"] == 1),
            "omsCanceled": sum(1 for r in rows if r["oms"]["status"] == 5),
            "omsMissing": sum(1 for r in rows if not r["oms"]["found"]),
            "issues": len(issue_rows),
            "totalQty": sum(int(r["qty"]) for r in rows if isinstance(r["qty"], (int, float))),
            "skuQty": sum(int(r["oms"]["totalQty"] or 0) for r in rows),
            "byLogisticsStatus": by_status,
            "byOmsStatus": by_oms,
        },
        "sheets": sheets,
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="货件跟踪：追踪表 × 恒信轨迹 × 领星OMS入库明细")
    ap.add_argument("--file", help="指定货件追踪表 xlsx（默认取最新一份）")
    ap.add_argument("--no-oms", action="store_true", help="不查 OMS（只跑物流）")
    ap.add_argument("--no-track", action="store_true", help="不查恒信（只跑 OMS）")
    ap.add_argument("--print", dest="do_print", action="store_true", help="打一张表出来看")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 票（调试）")
    a = ap.parse_args()

    src = Path(a.file) if a.file else find_workbook()
    if not src or not src.exists():
        log(f"找不到货件追踪表（模式 {WORKBOOK_PATTERN}）")
        return 2
    log(f"追踪表：{src.name}")

    rows, sheets = read_workbook(src)
    log(f"  读到 {len(rows)} 票，sheet：{' / '.join(sheets)}")
    if not rows:
        log("解析到 0 票，表头可能变了（见 COLUMNS）")
        return 3
    if a.limit:
        rows = rows[:a.limit]

    numbers = [r["otherNo"] for r in rows]

    # ① 恒信轨迹：一次一票，并发打（接口没有批量）
    tracks: dict[str, dict] = {}
    if not a.no_track:
        log(f"  查恒信轨迹（{len(set(numbers))} 个单号）…")
        pairs = [(r["otherNo"], r["trackingNo"]) for r in rows]
        done = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            for (no, _alt), tr in zip(pairs, pool.map(lambda p: fetch_track(p[0], p[1]), pairs)):
                tracks[no] = tr
                done += 1
                if done % 40 == 0:
                    log(f"    {done}/{len(pairs)}")
        ok = sum(1 for t in tracks.values() if t.get("ok"))
        alt_hit = [n for n, t in tracks.items() if t.get("matchedBy") == "trackingNo"]
        log(f"  轨迹查到 {ok}/{len(set(numbers))}"
            + (f"（其中 {len(alt_hit)} 票是按运单号匹配：{', '.join(alt_hit)}）" if alt_hit else ""))
        miss = sorted({n for n, t in tracks.items() if not t.get("ok")})
        if miss:
            log(f"  ! 恒信查不到：{', '.join(miss)}")
    else:
        tracks = {n: {"ok": False, "status": "", "statusText": "", "traces": []} for n in numbers}

    # ② OMS 入库明细
    if a.no_oms:
        oms = {n: {"found": False, "statusText": "未查", "skus": [], "skuCount": 0, "totalQty": 0}
               for n in numbers}
    else:
        log("  查领星 OMS 入库明细…")
        oms = fetch_oms(numbers)
        log(f"  命中 {sum(1 for v in oms.values() if v.get('found'))}/{len(set(numbers))}")

    built = [build_row(r, tracks.get(r["otherNo"], {}), oms.get(r["otherNo"], {})) for r in rows]
    payload = build_payload(built, sheets, src,
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    try:
        PUBLIC_JSON.parent.mkdir(parents=True, exist_ok=True)
        PUBLIC_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "last.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log(f"落盘失败：{e}")
        return 4

    s = payload["summary"]
    log(f"  → {PUBLIC_JSON.relative_to(ROOT)}  {PUBLIC_JSON.stat().st_size / 1024:.1f} KB")
    log(f"  合计 {s['total']} 票｜已到港 {s['arrived']}｜未到港 {s['notArrived']}｜"
        f"已签收 {s['delivered']}｜异常 {s['abnormal']}｜表内落后 {s['lag']}｜"
        f"OMS已上架 {s['omsOnShelf']}｜待入库 {s['omsPending']}｜需关注 {s['issues']}")

    if a.do_print:
        for r in built:
            print(f"{r['otherNo']:<16} {'到港' if r['arrived'] else '在途'} "
                  f"{r['logistics']['statusText']:<5} {r['oms']['statusText']:<5} "
                  f"{r['logistics']['latest']['info'][:44]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
