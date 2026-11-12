#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hx_ship_api.py — 恒信（HX）领星 OMS 发货 **走 OpenAPI**（替代 2026-11-11 那套页面自动化）。

用户 2026-11-12：「把昨天去领星发货的 skill 替代成 api 接口」。

为什么换 API（昨天的真机结论）
------------------------------
页面那条路（`hx_oms_ship.py`）在 11-12 凌晨连炸 4 轮：OMS 列表页改版后
输入框/每页条数/列顺序全变，无头会话里「分配仓库&物流」按钮还点不开。
而 OpenAPI 直接就能建出库单：

    POST /openapi/v1/outboundOrder/create
    data = [ {thirdOrderNo, orderNo, whCode, logisticsChannel,
              productList:[{sku, quantity}]} ]
    → 逐单返回 {thirdOrderNo, success, msg}

链路（等价 UI 的「分配仓库&物流 → 确定并审核」）
------------------------------------------------
    ① sku_map  拉库存清单，建「平台产品名 → 系统SKU」映射（只读，落 data/oms_sku_map.json）
    ② prepare  本批单号 → 解析系统SKU；已有出库单的跳过；配不到 SKU 的标「未配对」
    ③ create   分批（≤50）建出库单 → 逐单回执
    ④ verify   按 thirdOrderNo 回读 outboundOrder：status 2 仓库处理中 / 3 已出库 = 成功，
               顺带取 logisticsTrackNo
    ⑤ summary  成功/失败/未配对 三类清单落 json

用法
----
    python scripts/oms/hx_ship_api.py --split <split.json> --out <dir>            # 预览
    python scripts/oms/hx_ship_api.py --split <split.json> --out <dir> --apply    # 真建单
    python scripts/oms/hx_ship_api.py --pos-file <txt> --apply                    # 只给单号

退出码：0=全部有终态；42=有失败/未配对/未回读到；2=参数问题。

⚠ 硬前提：**产品必须已在 OMS 建好并审核通过**（`productList[].sku` 是系统SKU，
  不是平台SKU）。没配上的会回「传入产品sku有误或产品未通过审核」，本脚本把它归到
  「未配对」交人工 —— 不要反复重试，那是主数据问题。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from oms_client import OmsClient, OmsError, PAGE_SIZE_MAX  # noqa: E402

CFG = os.path.join(HERE, "oms_accounts.local.json")
MAP_FILE = os.path.join(HERE, "data", "oms_sku_map.json")

#: 恒信仓（skill 定稿：ZOO WAREHOUSE ⑥美西）+ 上传物流面单渠道
WH_CODE = "USW01"
CHANNEL = "Upload_Shipping_Label"

#: 出库单状态：2 仓库处理中 / 3 已出库 = 成功
STATUS_TEXT = {0: "草稿", 1: "待处理", 2: "仓库处理中", 3: "已出库",
               4: "已取消", 5: "异常"}
OK_STATUS = (2, 3)


def log(s: str) -> None:
    print(str(s), flush=True)


def client() -> OmsClient:
    acc = json.load(io.open(CFG, encoding="utf-8"))["accounts"]
    lm = next(a for a in acc if a["tag"] == "LM739")
    return OmsClient(lm["appKey"], lm["appSecret"], verbose=False)


# ---------------------------------------------------------------- ① SKU 映射

def _norm(s: str) -> str:
    """统一大写 + 去空格（平台SKU 与 productName 的对码口径）。"""
    return re.sub(r"\s+", "", str(s or "")).upper()


def _tail(key: str) -> str:
    """去掉品牌/店名前缀（BS/BS2/BH/BK/BAN/BD/BHY/BEL…）→ `款号-套数-尺码`。

    ★ 2026-11-12 用户当场指出「这都是有产品和产品配对的啊」：同一个实物在 OMS 里
      叫 `BS2-N204003-2B-S` / `BH-H103903-2B-L`，平台订单里叫 `BS-N204003-2B-S`
      ⇒ 只按整串精确匹配必然报「未配对」。前缀是**品牌/店铺**，不是产品，必须忽略
      （与 `bel_split_by_stock.brand_tail()` 同一口径）。
    """
    return re.sub(r"^(?:BAN|BHY|BEL|BEGL|BLA|BS2|BS|BH2|BH|BK|BD)-", "", _norm(key))


def _relax(key: str) -> str:
    """再去掉变体名**末尾数字**（`5SetB1.1`→`5SETB`、`2B1.1`→`2B`）。

    平台上架给变体加过 1 / 2 / 1.1 后缀、库存里没有（`bel_split_by_stock.relax_sku` 同口径）。
    """
    t = _tail(key)
    parts = t.split("-")
    if len(parts) >= 3:
        parts[-2] = re.sub(r"[\d.]+$", "", parts[-2])
    return "-".join(parts)


def _alias_keys(name: str) -> list:
    """一个产品名 → 它应该被哪些键命中（精确 → 去前缀 → 去尾数字）。"""
    keys = [_norm(name)]
    for f in (_tail, _relax):
        k = f(name)
        if k and k not in keys:
            keys.append(k)
    return keys


def _scan_inventory(c: OmsClient) -> dict:
    """库存清单 `integratedInventory/pageOpen` → {productName: 系统SKU}。

    快（~7 次请求），但**只含有库存行的产品** —— 所以必须有下面那段历史补全，
    否则没库存的款全被判「后台没产品」。
    """
    out, page = {}, 1
    while page <= 60:
        r = c.post("/openapi/v1/integratedInventory/pageOpen",
                   {"pageNo": page, "pageSize": 50})
        d = r.get("data") or {}
        recs = d.get("records") or d.get("list") or []
        if not recs:
            break
        for x in recs:
            name = str(x.get("productName") or "").strip()
            sku = str(x.get("sku") or "").strip()
            if name and sku:
                out[name] = sku
        if page * 50 >= (d.get("total") or 0):
            break
        page += 1
    return out


def _scan_history(c: OmsClient, days: int = 14, max_pages: int = 240) -> dict:
    """**历史出库单**里捞 productName → 系统SKU（这是产品主数据的真实来源）。

    ★ 2026-11-12 定位：66 个「未配对」里只有 9 个是前缀写法不同、其余在**库存接口里
      根本不存在**（库存快照只有 50 个产品 / 350 行）；但它们的出库单详情里
      `productList[].productName` + `.sku` 是成对的 —— 实测 10 天窗口就能拿到 175 条映射，
      66 个未配对里 63 个立刻可解。所以映射必须「库存 + 历史」双来源。
    成本：列表 50/页 + 详情 50/批；10 天 ≈ 3000 单 ≈ 4 分钟。故配 `--map-max-age`
    缓存（默认 12h），一天只重建一次。
    """
    end = time.strftime("%Y-%m-%d")
    start = time.strftime("%Y-%m-%d", time.localtime(time.time() - max(1, days) * 86400))
    nos, page = [], 1
    while page <= max_pages:
        d = c.list_page(page, PAGE_SIZE_MAX, start, end)
        recs = d.get("records") or d.get("list") or []
        if not recs:
            break
        nos += [str(x.get("outboundOrderNo") or "") for x in recs]
        if page * PAGE_SIZE_MAX >= (d.get("total") or 0):
            break
        page += 1
    nos = [n for n in nos if n]
    out = {}
    for i in range(0, len(nos), 50):
        for det in c.details(nos[i:i + 50]):
            for p in (det.get("productList") or []):
                name = str(p.get("productName") or "").strip()
                sku = str(p.get("sku") or "").strip()
                if name and sku:
                    out[name] = sku
    return out


def _map_is_fresh(max_age_h: float) -> bool:
    try:
        age = time.time() - os.path.getmtime(MAP_FILE)
    except OSError:
        return False
    return max_age_h > 0 and age < max_age_h * 3600


def refresh_sku_map(c: OmsClient, force: bool = False, max_age_h: float = 12.0,
                    history_days: int = 14, log=print) -> dict:
    """建「任何写法 → {sku, productName}」映射（库存 + 历史出库单），带缓存与别名。

    返回的 dict 同时登记三种键：精确名、去品牌前缀、去尾数字，查的时候按同顺序试。
    `max_age_h` 内已建好就直接用（每天只重建一次，避免 5 个店各扫一遍历史）。
    """
    if not force and _map_is_fresh(max_age_h):
        mp = load_sku_map()
        if mp:
            log("   SKU 映射：复用缓存 %d 条（%s，%.1f 小时内）"
                % (len(mp), MAP_FILE, max_age_h))
            return mp
    inv = _scan_inventory(c)
    hist = {}
    if history_days > 0:
        try:
            hist = _scan_history(c, days=history_days)
        except Exception as ex:                            # noqa: BLE001
            log("   ⚠ 历史出库单扫描失败（继续用库存那份）：%s" % str(ex)[:120])
    merged = {}
    merged.update(hist)            # 历史优先（产品主数据更全）
    merged.update(inv)             # 库存覆盖最近的（同一产品名以库存为准）
    mp: dict = {}
    for name, sku in merged.items():
        for key in _alias_keys(name):
            mp.setdefault(key, {"sku": sku, "productName": name})
    os.makedirs(os.path.dirname(MAP_FILE), exist_ok=True)
    io.open(MAP_FILE, "w", encoding="utf-8").write(
        json.dumps(mp, ensure_ascii=False, indent=1))
    log("   SKU 映射：%d 条别名（产品 %d：库存 %d + 历史 %d 天 %d）→ %s"
        % (len(mp), len(merged), len(inv), history_days, len(hist), MAP_FILE))
    return mp


def load_sku_map() -> dict:
    try:
        return json.load(io.open(MAP_FILE, encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return {}


def lookup_sku(mp: dict, platform_sku: str) -> dict:
    """按「精确 → 去品牌前缀 → 去尾数字」查系统SKU（纯函数）。查不到返回 {}。"""
    for key in _alias_keys(platform_sku):
        hit = mp.get(key)
        if hit and hit.get("sku"):
            return hit
    return {}


# ---------------------------------------------------------------- ② 准备

def load_orders(split: str, pos_file: str) -> dict:
    """→ {po: [{sku, qty}]}；split 走 groups.HX，pos-file 只有单号（SKU 未知）。"""
    agg: dict = {}
    if split and os.path.exists(split):
        d = json.load(io.open(split, encoding="utf-8"))
        for r in ((d.get("groups") or {}).get("HX") or []):
            po = str(r.get("ord") or "").strip()
            if not po:
                continue
            agg.setdefault(po, []).append({"sku": str(r.get("sku") or "").strip(),
                                           "qty": int(r.get("qty") or 1)})
    elif pos_file and os.path.exists(pos_file):
        for ln in io.open(pos_file, encoding="utf-8"):
            po = ln.strip()
            if po:
                agg.setdefault(po, [])
    return agg


def existing_orders(c: OmsClient, days: int = 7) -> dict:
    """近 N 天已有出库单 → {thirdOrderNo: status}（防重复建单）。"""
    start = time.strftime("%Y-%m-%d 00:00:00", time.localtime(time.time() - 86400 * days))
    end = time.strftime("%Y-%m-%d 23:59:59")
    out, page = {}, 1
    while page <= 200:
        r = c.post("/openapi/v1/outboundOrder/pageList", {
            "timeType": "orderCreateTime", "startTime": start, "endTime": end,
            "pageNo": page, "pageSize": 50})
        d = r.get("data") or {}
        recs = d.get("records") or d.get("list") or []
        if not recs:
            break
        for x in recs:
            po = str(x.get("thirdOrderNo") or "").strip()
            if po:
                out[po] = {"status": int(x.get("status") or 0),
                           "outboundOrderNo": x.get("outboundOrderNo"),
                           "track": x.get("logisticsTrackNo") or ""}
        if page * 50 >= (d.get("total") or 0):
            break
        page += 1
    return out


#: 国家名 → ISO2（导出表里是 `United States` 这种全称；OMS 的 `countryRegionCode` 要 `US`）
_COUNTRY = {
    "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US", "USA": "US", "US": "US",
    "CANADA": "CA", "MEXICO": "MX", "UNITED KINGDOM": "GB", "AUSTRALIA": "AU",
    "GERMANY": "DE", "FRANCE": "FR", "SPAIN": "ES", "ITALY": "IT", "JAPAN": "JP",
}
#: 导出表里「收件国家」可能叫这些列名（TEMU / SHEIN 两套导出都认）
_COUNTRY_COLS = ("收件人国家", "收件国家", "国家", "country", "countryName",
                 "receiverCountry", "shippingCountry")

#: 没有国家信息时的兜底（这几家店只发美国；告警会打出来，不静默）
DEFAULT_COUNTRY = "US"


def _country_code(name: str) -> str:
    s = re.sub(r"\s+", " ", str(name or "")).strip().upper()
    if not s:
        return ""
    if re.fullmatch(r"[A-Z]{2}", s):
        return s
    return _COUNTRY.get(s, "")


def load_countries(xlsx: str, log=print) -> dict:
    """导出表 → {平台单号: ISO2 国家码}（纯读）。

    ★ 2026-11-12 真机：`outboundOrder/create` 不带国家码时逐单回
      「无对应的国家代码: 」⇒ 32 单全部建单失败（映射明明已经修好了）。
      加上 `countryRegionCode` 后一次就成功 —— 国家码是**必填**项，
      而 split.json 里没有地址列，只能回导出表取。
    """
    out: dict = {}
    if not xlsx or not os.path.exists(xlsx):
        return out
    try:
        import openpyxl  # noqa: PLC0415
        wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        hdr = [str(x or "").strip() for x in next(rows)]
        i_po = hdr.index("订单号") if "订单号" in hdr else 0
        i_cn = next((hdr.index(c) for c in _COUNTRY_COLS if c in hdr), None)
        if i_cn is None:
            log("   ⚠ 导出表里找不到国家列（%s）→ 国家码一律按 %s"
                % (xlsx, DEFAULT_COUNTRY))
            return out
        for r in rows:
            po = str(r[i_po] or "").strip()
            code = _country_code(r[i_cn])
            if po and code:
                out[po] = code
        wb.close()
    except Exception as ex:                                # noqa: BLE001
        log("   ⚠ 读导出表取国家失败（%s）→ 国家码按 %s" % (str(ex)[:100], DEFAULT_COUNTRY))
    return out


def prepare(agg: dict, mp: dict, exist: dict, countries: dict | None = None) -> tuple:
    """→ (可建单 recs, 已存在[, 未配对[, 单号无SKU]])。

    `countries`：{平台单号: ISO2}；缺的按 `DEFAULT_COUNTRY` 兜底（会打告警）。
    """
    countries = countries or {}
    ready, dup, unmapped, nosku = [], [], [], []
    for po, items in agg.items():
        if po in exist and int(exist[po].get("status") or 0) != 4:
            dup.append({"po": po, "status": exist[po].get("status"),
                        "outboundOrderNo": exist[po].get("outboundOrderNo")})
            continue
        if not items:
            nosku.append({"po": po, "why": "只给了单号，没有 SKU（给 --split 才能自动建单）"})
            continue
        pl, bad = [], []
        for it in items:
            hit = lookup_sku(mp, it["sku"])
            if not hit:
                bad.append(it["sku"])
                continue
            pl.append({"sku": hit["sku"], "quantity": int(it["qty"] or 1)})
        if bad:
            unmapped.append({"po": po, "skus": bad,
                             "why": "OMS 里没有该系统SKU/产品未审核通过"})
            continue
        ready.append({"thirdOrderNo": po, "orderNo": po, "whCode": WH_CODE,
                      "logisticsChannel": CHANNEL,
                      "countryRegionCode": countries.get(po) or DEFAULT_COUNTRY,
                      "productList": pl})
    return ready, dup, unmapped, nosku


# ---------------------------------------------------------------- ③ 建单

def create(c: OmsClient, recs: list, allow_draft: bool = False) -> list:
    """分批（≤50）建出库单，返回逐单结果。

    ★★ 2026-11-12 用户当场叫停（**默认禁止**）：
      `outboundOrder/create` 建出来的是**草稿（status=0）**，公开 API 里没有提交/审核接口
      ⇒ 这批草稿既发不了货，又会在「出库单」里堆成「异常」，只能人工取消。
      当天我建了 12 张，用户原话：「把你新建在草稿下的订单全部取消，这不是瞎搞吗」。
      真发货必须走内部网关 `scripts/oms/hx_ship_gw.js`
      （`platform/order/update` + `batchApprove`，实测 86 单到「处理中」）。
      这里保留 `allow_draft=True` 仅供显式调试，默认一律拒绝。
    """
    if not allow_draft:
        raise RuntimeError(
            "拒绝创建草稿出库单：公开 API 只能出 status=0 草稿，没有审核接口。\n"
            "  真发货请用：node scripts/oms/hx_ship_gw.js ship --plan <plan.json> "
            "--carriers <carriers.json> --apply\n"
            "  确要建草稿调试请显式传 allow_draft=True（不要接进整链）。")
    out = []
    for i in range(0, len(recs), 50):
        batch = recs[i:i + 50]
        try:
            r = c.post("/openapi/v1/outboundOrder/create", batch)
        except OmsError as ex:
            # 客户端对非 200 code 抛错，但逐单 msg 在 body 里 —— 这里退回逐单重试
            msg = str(ex)
            for rec in batch:
                try:
                    r1 = c.post("/openapi/v1/outboundOrder/create", [rec])
                    out += _rows(r1) or [{"po": rec["thirdOrderNo"], "ok": False,
                                          "msg": str(r1.get("msg"))[:120]}]
                except OmsError as ex1:
                    out.append({"po": rec["thirdOrderNo"], "ok": False,
                                "msg": str(ex1)[:160]})
            log("   ⚠ 整批建单失败，已逐单重试（%s）" % msg[:100])
            continue
        rows = _rows(r)
        if rows:
            out += rows
        else:
            out += [{"po": x["thirdOrderNo"], "ok": True, "msg": ""} for x in batch]
    return out


def _rows(r: dict) -> list:
    d = r.get("data")
    if not isinstance(d, list):
        return []
    out = []
    for x in d:
        out.append({"po": str(x.get("thirdOrderNo") or "").strip(),
                    "ok": bool(x.get("success")),
                    "msg": str(x.get("msg") or "")[:160]})
    return out


# ---------------------------------------------------------------- ④ 回读

def verify(c: OmsClient, pos: list, days: int = 3) -> dict:
    """按 thirdOrderNo 回读状态：2/3 = 成功，并取运单号。"""
    exist = existing_orders(c, days=days)
    res = {}
    for po in pos:
        e = exist.get(po)
        if not e:
            res[po] = {"status": None, "ok": False, "why": "OMS 里查不到该单"}
            continue
        st = int(e.get("status") or 0)
        res[po] = {"status": st, "statusText": STATUS_TEXT.get(st, str(st)),
                   "ok": st in OK_STATUS, "track": e.get("track") or "",
                   "outboundOrderNo": e.get("outboundOrderNo")}
    return res


# ---------------------------------------------------------------- 主流程

def run(split: str, pos_file: str, out_dir: str, apply: bool,
        map_max_age: float = 12.0, history_days: int = 14,
        refresh_map: bool = False, orders_xlsx: str = "") -> dict:
    c = client()
    log("[恒信OMS-API] 开始%s" % ("（真建单）" if apply else "（预览，不写入）"))
    mp = refresh_sku_map(c, force=refresh_map, max_age_h=map_max_age,
                         history_days=history_days, log=log)
    agg = load_orders(split, pos_file)
    if not agg:
        raise SystemExit("[FAIL] 没有单号：给 --split 或 --pos-file")
    log("   本批 %d 单" % len(agg))
    exist = existing_orders(c, days=7)
    countries = load_countries(orders_xlsx, log=log)
    if countries:
        log("   国家码：导出表命中 %d 单（其余按 %s）" % (len(countries), DEFAULT_COUNTRY))
    ready, dup, unmapped, nosku = prepare(agg, mp, exist, countries)
    log("   可建单 %d｜已存在出库单 %d｜SKU 未配对 %d｜无 SKU %d"
        % (len(ready), len(dup), len(unmapped), len(nosku)))
    for u in unmapped[:10]:
        log("     ✗ 未配对 %-22s %s" % (u["po"], ",".join(u["skus"])[:60]))

    created = []
    if apply and ready:
        # 公开 API 只能建草稿 ⇒ 默认拒绝（见 create() 的说明），别再堆异常单
        try:
            created = create(c, ready)
        except RuntimeError as ex:
            log("   ✗ %s" % str(ex).replace("\n", " ")[:220])
            created = []
        okn = len([x for x in created if x.get("ok")])
        log("   建单回执：成功 %d / 失败 %d" % (okn, len(created) - okn))
        for x in created[:10]:
            if not x.get("ok"):
                log("     ✗ %-22s %s" % (x["po"], x.get("msg")))

    v = verify(c, list(agg.keys())) if apply else {}
    ok = [p for p, x in v.items() if x.get("ok")]
    bad = [{"po": p, **x} for p, x in v.items() if not x.get("ok")]
    rep = {"store": "HX", "mode": "api", "total": len(agg), "applied": bool(apply),
           "ready": len(ready), "existing": dup, "unmapped": unmapped,
           "no_sku": nosku, "created": created,
           "verify_ok": ok, "verify_bad": bad}
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "hx_oms_ship.json")
    io.open(p, "w", encoding="utf-8").write(json.dumps(rep, ensure_ascii=False, indent=1))
    if apply:
        log("   [回读] 处理中/已出库 %d｜未成 %d → %s" % (len(ok), len(bad), p))
    else:
        log("   [预览] 未写入任何平台状态 → %s" % p)
    return rep


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                        # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="恒信 领星OMS 发货（OpenAPI）")
    ap.add_argument("--split", default="", help="拆仓 split.json（取 groups.HX）")
    ap.add_argument("--pos-file", default="", help="单号 txt（无 SKU，只能回读）")
    ap.add_argument("--out", default="", help="产物目录")
    ap.add_argument("--apply", action="store_true", help="真建出库单")
    ap.add_argument("--map-max-age", type=float, default=12.0,
                    help="SKU 映射缓存时效（小时；默认 12，0=每次都重建）")
    ap.add_argument("--history-days", type=int, default=14,
                    help="从历史出库单补映射的回看天数（默认 14，0=不扫历史）")
    ap.add_argument("--refresh-map", action="store_true",
                    help="强制重建 SKU 映射（不信任缓存）")
    ap.add_argument("--orders-xlsx", default="",
                    help="导出订单表（取收件人国家 → create 的 countryRegionCode，必填项）")
    a = ap.parse_args()
    out = a.out or os.path.join(HERE, "out", time.strftime("%Y-%m-%d"), "hx_api")
    rep = run(a.split, a.pos_file, out, a.apply, map_max_age=a.map_max_age,
              history_days=a.history_days, refresh_map=a.refresh_map,
              orders_xlsx=a.orders_xlsx)
    if not a.apply:
        return 0
    bad = len(rep.get("verify_bad") or []) + len(rep.get("unmapped") or []) \
        + len(rep.get("no_sku") or [])
    return 0 if bad == 0 else 42


if __name__ == "__main__":
    raise SystemExit(main())
