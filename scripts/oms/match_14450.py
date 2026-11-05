# -*- coding: utf-8 -*-
"""14450：把「帕沃尔头程（参考单号 14450…）」和「领星 OMS 入库单（IB003…）」对上。

用户 2026-11-05：
    「货件跟踪之前我记得我让你抓取了 14450 领星的入库单号，
      帮我回溯成帕沃尔领星的货件列」

背景
----
2026-11-02 起 14450 那一行在面板上换成了帕沃尔参考单号（144503080），
因为 14450 的领星 OpenAPI 已停更（最新 orderCreateTime 冻在 2026-10-24）。
但停更前抓到的 IB003… 入库单号还在，用户要的是把两边**对上**，
让帕沃尔的行能显示它对应的领星入库单号（回溯列）。

匹配口径
--------
两套系统没有共享主键：帕沃尔的「转单号」实测为空，领星里也没有参考单号。
能对上的只有三条弱线索，按可信度叠加打分：

  1. **SKU 集合**（最强）：领星明细有 inboundSkuVOList[].sku，帕沃尔详情有 fnsku。
     14450 的 SKU 就是 D14450… 这套，两边同一个码。
  2. **数量**：同一票的总件数应一致（帕沃尔按箱展开后聚合过）。
  3. **时间**：帕沃尔 addTime ≈ 领星 orderCreateTime（同一票相差通常几十秒到几分钟）。

先取**完全相同的 SKU 集合 + 数量**做一对一配对，再对剩余单按「SKU 重合度高 + 时间近」兜底，
最后把没配上的两边都列出来（不硬凑）。

用法
----
    python scripts/oms/match_14450.py              # 打印配对结果
    python scripts/oms/match_14450.py --dump out/oms/match_14450.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "oms" / "lib"))
sys.path.insert(0, str(ROOT / "scripts" / "pw_orders"))
sys.path.insert(0, str(ROOT / "scripts" / "pw_orders" / "lib"))

ACCOUNT = "14450"
REF_PREFIX = "14450"


def log(msg: str) -> None:
    print(msg, flush=True)


def oms_client():
    from oms_client import OmsClient  # noqa: E402

    p = ROOT / "scripts" / "oms" / "oms_accounts.local.json"
    acc = json.loads(p.read_text(encoding="utf-8-sig"))
    a = next(x for x in acc["accounts"] if x.get("tag") == ACCOUNT)
    return OmsClient(a["appKey"], a["appSecret"], verbose=False)


def oms_detail(c, batch: list[str], depth: int = 0) -> list[dict]:
    """取一批详情；失败就把批次对半拆开重试。

    ⚠ 实测：`inboundOrder/detail` 一次传 50 个会**整批 500**（网络异常），
      而且重试同一批还是 500 —— 不是偶发抖动，是这批太大/太杂。
      对半拆开后小批次能过，所以这里递归到单个单号为止。
    """
    if not batch:
        return []
    for attempt in range(3):
        try:
            return c.post("/openapi/v1/inboundOrder/detail",
                          {"inboundOrderNoList": batch})["data"] or []
        except Exception as e:  # noqa: BLE001
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if len(batch) == 1:
                log(f"   ! {batch[0]} 详情取不到：{str(e)[:60]}")
                return []
            mid = len(batch) // 2
            log(f"   · 批次 {len(batch)} 报 500，拆半重试")
            return (oms_detail(c, batch[:mid], depth + 1)
                    + oms_detail(c, batch[mid:], depth + 1))
    return []


def oms_inbound(retries: int = 4) -> list[dict]:
    """14450 的入库单（列表 + 详情）。接口偶发 500，必须重试。"""
    c = oms_client()
    nos: list[str] = []
    page = 1
    while True:
        for attempt in range(retries):
            try:
                d = c.post("/openapi/v1/inboundOrder/pageList",
                           {"page": page, "pageSize": 50})["data"]
                break
            except Exception as e:  # noqa: BLE001
                if attempt == retries - 1:
                    raise
                log(f"   ! 列表第 {page} 页 500，重试：{str(e)[:60]}")
                time.sleep(2 * (attempt + 1))
        recs = d.get("records") or []
        nos += [r["inboundOrderNo"] for r in recs if r.get("inboundOrderNo")]
        if not recs or len(nos) >= int(d.get("total") or 0):
            break
        page += 1
    log(f"① 14450 领星入库单：{len(nos)} 张")

    out: list[dict] = []
    for i in range(0, len(nos), 20):
        out += oms_detail(c, nos[i:i + 20])
    log(f"   详情 {len(out)} 张")
    return out


def pw_rows(days: int = 60) -> list[dict]:
    from pw_inbound_track import fetch  # noqa: E402

    return fetch(days, log=log)


def oms_shape(d: dict) -> dict:
    skus = {str(s.get("sku") or ""): int(s.get("quantity") or 0)
            for s in (d.get("inboundSkuVOList") or []) if s.get("sku")}
    return {
        "omsNo": str(d.get("inboundOrderNo") or ""),
        "createTime": str(d.get("orderCreateTime") or ""),
        "status": d.get("status"),
        "whName": str(d.get("whName") or ""),
        "skus": skus,
        "qty": sum(skus.values()),
        "skuSet": frozenset(skus),
    }


def pw_shape(d: dict) -> dict:
    skus: dict[str, int] = {}
    for p in d.get("products") or []:
        k = str(p.get("fnsku") or "")
        if k:
            skus[k] = skus.get(k, 0) + int(p.get("qty") or 0)
    return {
        "refNo": str(d.get("refNo") or ""),
        "orderId": str(d.get("orderId") or ""),
        "addTime": str(d.get("addTime") or ""),
        "statusText": str(d.get("statusText") or ""),
        "whName": str(d.get("whName") or ""),
        "skus": skus,
        "qty": sum(skus.values()),
        "skuSet": frozenset(skus),
    }


def minutes_between(a: str, b: str) -> float:
    try:
        ta = datetime.strptime(a[:19], "%Y-%m-%d %H:%M:%S")
        tb = datetime.strptime(b[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return 1e9
    return abs((ta - tb).total_seconds()) / 60.0


def match(oms: list[dict], pw: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """一对一配对：先精确（SKU 集合 + 数量），再按重合度 + 时间兜底。"""
    pairs: list[dict] = []
    used_o: set[str] = set()
    used_p: set[str] = set()

    # ---- 第 1 轮：SKU 集合与数量完全一致，且时间最接近（同集合可能多票，按时间配）
    for p in pw:
        cands = [o for o in oms
                 if o["omsNo"] not in used_o and o["skuSet"] == p["skuSet"]
                 and o["qty"] == p["qty"]]
        if not cands:
            continue
        best = min(cands, key=lambda o: minutes_between(o["createTime"], p["addTime"]))
        pairs.append({"refNo": p["refNo"], "omsNo": best["omsNo"], "how": "SKU集合+数量一致",
                      "gapMinutes": round(minutes_between(best["createTime"], p["addTime"]), 1),
                      "qty": p["qty"], "skuCount": len(p["skuSet"])})
        used_o.add(best["omsNo"])
        used_p.add(p["refNo"])

    # ---- 第 2 轮：SKU 有交集 + 时间接近（±3 天），取重合度最高
    for p in pw:
        if p["refNo"] in used_p:
            continue
        best, best_key = None, None
        for o in oms:
            if o["omsNo"] in used_o:
                continue
            inter = len(p["skuSet"] & o["skuSet"])
            if not inter:
                continue
            gap = minutes_between(o["createTime"], p["addTime"])
            if gap > 4320:                     # 超过 3 天基本不是同一票
                continue
            key = (-inter, gap)                # 重合 SKU 数优先，其次时间
            if best_key is None or key < best_key:
                best, best_key = o, key
        if not best:
            continue
        inter = len(p["skuSet"] & best["skuSet"])
        pairs.append({"refNo": p["refNo"], "omsNo": best["omsNo"],
                      "how": f"SKU重合 {inter}/{len(p['skuSet'])}",
                      "gapMinutes": round(minutes_between(best["createTime"], p["addTime"]), 1),
                      "qty": p["qty"], "skuCount": len(p["skuSet"])})
        used_o.add(best["omsNo"])
        used_p.add(p["refNo"])

    left_pw = [p for p in pw if p["refNo"] not in used_p]
    left_oms = [o for o in oms if o["omsNo"] not in used_o]
    return pairs, left_pw, left_oms


def main() -> int:
    ap = argparse.ArgumentParser(description="14450 帕沃尔头程 ↔ 领星入库单 配对")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--dump", default="")
    a = ap.parse_args()

    oms = [oms_shape(d) for d in oms_inbound()]
    pw = [pw_shape(d) for d in pw_rows(a.days)]
    log(f"② 帕沃尔头程 {len(pw)} 单（参考单号 {REF_PREFIX} 开头，近 {a.days} 天）")

    pairs, left_pw, left_oms = match(oms, pw)
    log(f"③ 配上 {len(pairs)} 对；帕沃尔单剩 {len(left_pw)}，领星单剩 {len(left_oms)}")
    for x in sorted(pairs, key=lambda z: z["refNo"], reverse=True)[:20]:
        log(f"   {x['refNo']:<12} ← {x['omsNo']:<16} {x['how']:<18} 差 {x['gapMinutes']} 分")
    if left_pw:
        log("   帕沃尔未配： " + ", ".join(f"{p['refNo']}({p['qty']}件/{len(p['skuSet'])}SKU)"
                                        for p in left_pw[:10]))
    if left_oms:
        log("   领星未配： " + ", ".join(f"{o['omsNo']}({o['qty']}件)" for o in left_oms[:10]))

    if a.dump:
        p = Path(a.dump)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pairs": pairs,
            "unmatchedPw": [{"refNo": p["refNo"], "qty": p["qty"],
                             "skuCount": len(p["skuSet"]), "addTime": p["addTime"]}
                            for p in left_pw],
            "unmatchedOms": [{"omsNo": o["omsNo"], "qty": o["qty"],
                              "createTime": o["createTime"], "status": o["status"]}
                             for o in left_oms],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"   → {a.dump}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
