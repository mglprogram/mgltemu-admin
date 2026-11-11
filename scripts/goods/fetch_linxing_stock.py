#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_linxing_stock.py — 领星 OMS 全仓库存明细（LM739 恒信，可查询）。

与 `fetch_stock_track.py` 里那段领星逻辑的区别
--------------------------------------------
`fetch_stock_track.py` 只把总库存**挂到 7 个款号的尺码行上**（聚合到「款+变体+尺码」）。
本脚本产出的是**全量明细**：账号 × 产品 × 尺码 × 仓库，一个都不丢，
给 `/temu/stock-track` 页新增的「领星总库存」视图用（支持搜索、按账号/仓库筛选）。

数据源
------
`POST /openapi/v1/integratedInventory/pageOpen`（领星 OMS v1）
    参数：{"page": n, "pageSize": 200}   ← 实测 200 可用
    凭证：LM739（恒信），见 scripts/oms/oms_accounts.local.json
          （被 .gitignore 的 `*local.json` 挡住，**绝不入库**）

⚠ 2026-11-02 起 **14450（帕沃尔）账号已被停用**，不再拉取。
  加州3 的库存改由 `scripts/goods/fetch_pw_stock.py` 从帕沃尔库存页更新
  （与 Kansas 同一个数据源，见 `pw-stock.json`）。

字段口径（重要）
----------------
`totalAmount` 是**总库存**（= 可用 + 锁定 + 在途 + 箱 + FBA 退货），
`productStockDtl` 里才有拆分：
    availableAmount  可用
    lockAmount       锁定（已下单未发）
    transportAmount  在途
`totalAmount` 不直接等于三者相加 —— 还有 boxStockDtl / fbaReturnStockDtl 两块，
所以本脚本**原样保留** totalAmount，同时把三项拆分单独给出，页面各自展示。

产出
----
    temu-admin/public/data/linxing-stock.json
    {
      "generatedAt": "...",
      "accounts": [{"tag","label","rows","ok"}],
      "warehouses": ["加州3仓", ...],
      "total": N,                       # 明细行数
      "items": [
        {"acct","acctLabel","wh","whCode","sku","productName",
         "code","variant","size","total","avail","lock","transit",
         "stockType","operateTime"},
        ...
      ]
    }

用法
----
    python scripts/goods/fetch_linxing_stock.py            # 全量拉一次
    python scripts/goods/fetch_linxing_stock.py --dry      # 只打印统计，不落盘
    python scripts/goods/fetch_linxing_stock.py --no-publish-hint

退出码：0 成功；2 账号拉取失败；4 落盘失败
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "temu-admin" / "public" / "data" / "linxing-stock.json"

sys.path.insert(0, str(ROOT / "scripts" / "oms"))
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

#: 领星 OMS 产品库存接口（v1）
OMS_PATH = "/openapi/v1/integratedInventory/pageOpen"

#: 只留恒信 LM739；14450（帕沃尔）2026-11-02 已停用，加州3 改从帕沃尔库存页取。
OMS_ACCOUNTS = [
    ("LM739", "恒信", "hx"),
]

PAGE_SIZE = 200


def num(v, default=0.0) -> float:
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return default


def split_sku(sku: str):
    """复用 fetch_stock_track 的解析器，保证两个视图口径完全一致。"""
    try:
        from fetch_stock_track import split_sku_any as _f  # noqa: PLC0415
        return _f(sku)
    except Exception:  # noqa: BLE001
        return None


def load_creds() -> dict:
    """{tag: (appKey, appSecret)}。环境变量优先，其次本地文件（不入库）。"""
    import os  # noqa: PLC0415

    out: dict[str, tuple[str, str]] = {}
    f = ROOT / "scripts" / "oms" / "oms_accounts.local.json"
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8-sig"))
            for a in d.get("accounts") or []:
                tag = str(a.get("tag") or "").strip()
                if tag and a.get("appKey") and a.get("appSecret"):
                    out[tag] = (a["appKey"], a["appSecret"])
        except Exception as e:  # noqa: BLE001
            print("[linxing] 读 oms_accounts.local.json 失败：%s" % str(e)[:120])
    for tag in ("LM739",):
        k = os.environ.get("OMS_APP_KEY_" + tag)
        s = os.environ.get("OMS_APP_SECRET_" + tag)
        if k and s:
            out[tag] = (k, s)
    return out


def probe_daily(force: bool = True) -> dict:
    """取 {`款号-套系-尺码`: 最近7天日均}。

    ★ 2026-11-11 用户定案：**改从十部半托管库存管理表读**（不再从 warehouse-probe 看板）。

        目录：D:\\together\\Inventory table\\（文件由人每天更新到这里）
        文件：十部半托管-库存管理表<日期>.xlsx —— 取最新的那份
        Sheet：总销售表
        列：   MSKU → 最近7天日均
        聚合：同一个「款号-套系-尺码」的各店铺行**相加**
              （TM-BH / TM-BEL / SHEIN-BS / TK-TSA … 共 10 个店铺）

    看板那条链路保留在 `linxing_daily.py` 里，但**不再被调用**；
    这里改用 `ledger_daily.py`。取不到返回空表（页面这一列显示 —）。
    """
    try:
        import sys as _sys  # noqa: PLC0415
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from ledger_daily import probe_daily_merged  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print("[linxing] ledger_daily 不可用：%s" % str(e)[:120])
        return {}
    return probe_daily_merged(log=print)


def probe_daily_file():
    """这一轮用的是哪个台账文件（写进 JSON 便于追溯）。取不到返回 None。"""
    try:
        import sys as _sys  # noqa: PLC0415
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from ledger_daily import latest_file  # noqa: PLC0415
        p = latest_file(log=lambda *_: None)
        return p.name if p else None
    except Exception:  # noqa: BLE001
        return None


def _existing_daily() -> dict:
    """读现有 linxing-stock.json 的日均，按 `款-套系-尺码` → {daily3, daily, daily14} 建表。

    用于「刷新库存但不重算日均」这条路径（用户 2026-11-11：日均按人给的台账，
    不该被每天的库存刷新覆盖掉）。三列任一有值就收进来。
    """
    p = ROOT / "temu-admin" / "public" / "data" / "linxing-stock.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict] = {}
    for it in d.get("items") or []:
        vals = {k: it.get(k) for k in ("daily3", "daily", "daily14")}
        if all(v is None for v in vals.values()):
            continue
        k = "-".join(x for x in (it.get("code"), it.get("variant"), it.get("size")) if x)
        if k:
            out[str(k).lower()] = vals
    return out


def fetch_one(client, tag: str, label: str, short: str) -> list[dict]:
    """拉一个账号的全部库存行（逐页）。"""
    items: list[dict] = []
    page = 1
    while True:
        d = client.post(OMS_PATH, {"page": page, "pageSize": PAGE_SIZE}).get("data") or {}
        recs = d.get("records") or []
        for r in recs:
            name = str(r.get("productName") or "").strip()
            p = split_sku(name)
            dtl = r.get("productStockDtl") or {}
            items.append({
                "acct": tag,
                "acctLabel": label,
                "acctShort": short,
                "wh": str(r.get("whName") or "").strip(),
                "whCode": str(r.get("whCode") or "").strip(),
                "sku": str(r.get("sku") or "").strip(),
                "productName": name,
                # 解析出来的三段（解析不出就留空，页面上仍按原始 productName 显示）
                "code": p[0] if p else "",
                "variant": p[1] if p else "",
                "size": p[2] if p else "",
                "total": round(num(r.get("totalAmount")), 2),
                "avail": round(num(dtl.get("availableAmount")), 2),
                "lock": round(num(dtl.get("lockAmount")), 2),
                "transit": round(num(dtl.get("transportAmount")), 2),
                "box": round(num(r.get("boxTotalAmount")), 2),
                "fbaReturn": round(num(r.get("fbaReturnTotalAmount")), 2),
                "stockType": r.get("stockType"),
                # 7天日均（看板口径，件/天）；主流程拉完后统一补齐
                "daily": None,
                # 3天 / 14天日均（2026-11-11 追加，页面左右两侧各一列、默认隐藏）
                "daily3": None,
                "daily14": None,
                # 明细行自带「近7天日均」的件级参考（只在看板能定位到该店时才有）
                "dailyRow": None,
                "operateTime": str(r.get("operateTime") or ""),
            })
        if len(recs) < PAGE_SIZE:
            break
        page += 1
    return items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="领星 OMS 全仓库存明细（LM739 恒信）")
    ap.add_argument("--dry", action="store_true", help="只打印统计，不写文件")
    ap.add_argument("--only", default="", help="只拉某个账号（目前只有 LM739）")
    ap.add_argument("--refresh-daily", action="store_true",
                    help="按库存管理表重算 7天日均（默认沿用现有 daily，只更新可用/在途）")
    args = ap.parse_args(argv)

    try:
        from lib.oms_client import OmsClient, OmsError  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print("[linxing] 领星客户端不可用：%s" % str(e)[:150])
        return 2

    creds = load_creds()
    accounts: list[dict] = []
    all_items: list[dict] = []
    ok_count = 0

    for tag, label, short in OMS_ACCOUNTS:
        if args.only and args.only != tag:
            continue
        if tag not in creds:
            print("[linxing] %s(%s) 缺凭证，跳过" % (tag, label))
            accounts.append({"tag": tag, "label": label, "short": short,
                             "rows": 0, "ok": False, "error": "缺凭证"})
            continue
        k, s = creds[tag]
        t0 = time.time()
        try:
            c = OmsClient(k, s, timeout=60, verbose=False)
            items = fetch_one(c, tag, label, short)
            all_items += items
            ok_count += 1
            print("[linxing] %s(%s)：%d 行，%.1fs"
                  % (tag, label, len(items), time.time() - t0))
            accounts.append({"tag": tag, "label": label, "short": short,
                             "rows": len(items), "ok": True, "error": ""})
        except OmsError as e:
            print("[linxing] %s(%s) 失败：%s" % (tag, label, str(e)[:160]))
            accounts.append({"tag": tag, "label": label, "short": short,
                             "rows": 0, "ok": False, "error": str(e)[:200]})
        except Exception as e:  # noqa: BLE001
            print("[linxing] %s(%s) 异常：%s" % (tag, label, str(e)[:160]))
            accounts.append({"tag": tag, "label": label, "short": short,
                             "rows": 0, "ok": False, "error": str(e)[:200]})

    if ok_count == 0:
        print("[linxing] 一个账号都没拉到数据，退出")
        return 2

    # ★ 日均：来源 = 十部半托管库存管理表「总销售表」的 最近3天/7天/14天日均（见 ledger_daily.py）
    #   用户 2026-11-11 定案：刷新时**只更新可用库存 / 在途库存**，日均沿用已有的那份，
    #   不要每次重算 —— 需要重算时才加 --refresh-daily。
    #   2026-11-11 追加：三列一起带（daily3 / daily / daily14），页面两侧各一列且默认隐藏。
    daily_hit = hit3 = hit14 = 0
    if args.refresh_daily:
        daily_map = probe_daily(force=True)
        if daily_map:
            from ledger_daily import daily_for  # noqa: PLC0415
            for it in all_items:
                v = daily_for(daily_map, it.get("code"), it.get("variant"), it.get("size"))
                if not v:
                    continue
                if v.get("daily") is not None:
                    it["daily"] = v["daily"]
                    daily_hit += 1
                if v.get("daily3") is not None:
                    it["daily3"] = v["daily3"]
                    hit3 += 1
                if v.get("daily14") is not None:
                    it["daily14"] = v["daily14"]
                    hit14 += 1
        print("[linxing] 日均：按库存管理表重算，%d 个款，7天命中 %d / %d 行（3天 %d、14天 %d）"
              % (len(daily_map), daily_hit, len(all_items), hit3, hit14))
    else:
        old_daily = _existing_daily()
        if old_daily:
            for it in all_items:
                k = "-".join(x for x in (it.get("code"), it.get("variant"), it.get("size")) if x)
                v = old_daily.get(str(k).lower())
                if not v:
                    continue
                if v.get("daily") is not None:
                    it["daily"] = v["daily"]
                    daily_hit += 1
                if v.get("daily3") is not None:
                    it["daily3"] = v["daily3"]
                    hit3 += 1
                if v.get("daily14") is not None:
                    it["daily14"] = v["daily14"]
                    hit14 += 1
        print("[linxing] 日均：沿用现有值（不重算），7天命中 %d / %d 行（3天 %d、14天 %d）%s"
              % (daily_hit, len(all_items), hit3, hit14,
                 "" if daily_hit else " —— 现有文件里没有日均，可加 --refresh-daily"))

    whs = sorted({i["wh"] for i in all_items if i["wh"]})

    # 按「账号 × 产品名」合并同一 SKU 的多仓行，页面上先看账号级明细，
    # 需要逐仓时再展开（页面按 wh 分组展示，这里只是给个汇总便于对账）。
    payload = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "accounts": accounts,
        "warehouses": whs,
        "total": len(all_items),
        "totalQty": round(sum(i["total"] for i in all_items), 2),
        "availQty": round(sum(i["avail"] for i in all_items), 2),
        "items": all_items,
        "dailyHit": daily_hit,
        "dailySource": ("十部半托管库存管理表 → 总销售表 → 「最近7天日均」，"
                        "按「款号-套系-尺码」把各店铺（TM-BH/TM-BEL/SHEIN-BS/TK-TSA…）相加；"
                        "文件取自 D:\\together\\Inventory table 里最新的那份"),
        "dailyFile": str(probe_daily_file() or ""),
        "rule": {
            "note": "总库存 = 领星 totalAmount（= 可用 + 锁定 + 在途 + 箱 + FBA 退货）。"
                    "可用 / 锁定 / 在途 来自 productStockDtl，三项相加不等于总库存，"
                    "差额在箱库存与 FBA 退货里。",
            "source": "领星 OMS /openapi/v1/integratedInventory/pageOpen（LM739 恒信；"
                      "14450 帕沃尔已于 2026-11-02 停用，加州3 改由帕沃尔库存页提供）",
        },
    }

    if args.dry:
        print(json.dumps({k: v for k, v in payload.items() if k != "items"},
                         ensure_ascii=False, indent=2))
        return 0

    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("[linxing] 落盘失败：%s" % str(e)[:200])
        return 4

    print("[linxing] 已写 %s（%d 行，%.1f KB，总库存 %s 件）"
          % (OUT.relative_to(ROOT), len(all_items), OUT.stat().st_size / 1024,
             int(payload["totalQty"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
