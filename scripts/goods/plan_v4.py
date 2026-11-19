# -*- coding: utf-8 -*-
"""v4 规划：用户 2026-11-19 新口径（可售 ≤3 天 + 上架 ≤6 天 + 最新节点在「提柜/待预约送仓」）。

口径（按用户原话，只有三条同时满足才开预售）：
    ① 可售天数 D0 = (恒信可用 + 帕沃尔加州3 + Kansas) ÷ 7天日均 ≤ 3 天
    ② 预测上架时间 − 今天 ≤ 6 天
    ③ 该尺码**最新物流节点**是：
        · 帕沃尔（乐航 14450）：`提柜`
        · 恒信（领星 LM739）：`提重柜` 或 `待预约送仓`
   满 ①③ 但 ② 不满足 → 不开预售，进「等上架」；
   满 ① 但 ③ 不满足（还在海上/查验/到港未提等）→ **不开**（用户新口径，比 v3 严）；
     D0 ≤ 3 但**没有在途** → 只清零。
3.1 给多少在途量：`min(在途, max(5, ceil(7天日均 × 2)))`（用户「7天日均*2/3」的 2 倍口径；
    要改成 3 倍传 `mult=3`）。
3.2 **填在哪一仓**（用户 2026-11-19 更正）：「填在恒信仓那一栏」—— 弹窗里发货仓是
    `恒信加州3` 的那一行填数量，帕沃尔/kansas 那一行填 0。
    （2026-11-13 的旧口径是「填加州3仓、其余 0」；2026-11-19 后台实读也显示数量确实在
    `恒信加州3` 行、`加州三仓` 行是 0，故以恒信为准。）

只算数、不碰浏览器。
用法：
    python scripts/goods/plan_v4.py                 # 打印今天该开的清单
    python scripts/goods/plan_v4.py --mult 3        # 用日均×3
    python scripts/goods/plan_v4.py --json out.json # 落文件给下游/面板用
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "temu-admin" / "public" / "data"

D0_MAX = 3.0          # 可售天数上限（用户：≤3 天）
SHELF_MAX = 6.0       # 上架距离上限（用户：≤6 天）
QTY_MULT = 2.0        # 给多少在途预售＝日均 × 这个倍数（用户「×2/3」→ 取 2 起）
QTY_MIN = 5

#: 各货代「可以开预售」的最新节点关键词（按用户原话）
OK_NODE = {
    "帕沃尔": ("提柜",),                 # 乐航 14450 轨迹
    "恒信": ("提重柜", "待预约送仓"),     # 领星 LM739 轨迹
}


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _account_side(account: str) -> str:
    """轨迹账号 → 货代侧：14450=帕沃尔（乐航），LM739=恒信。"""
    a = str(account or "").strip().upper()
    if a in ("14450", "PAWOER", "LH"):
        return "帕沃尔"
    return "恒信"


def node_ok(account: str, info: str) -> bool:
    """最新节点是否属于「可以开预售」的状态（纯函数，便于单测）。"""
    side = _account_side(account)
    keys = OK_NODE.get(side, ())
    t = str(info or "")
    return any(k in t for k in keys)


def transit_index() -> dict:
    """(款号-套系大写, 尺码大写) → 最早一票「节点允许+6天内上架」的在途。

    结构：{"shelf": date, "node": 节点文本, "account": 账号, "channel": 渠道,
           "transit": 领星在途件数, "any": 最早任何在途(仅参考)}
    没有合格票的也留一条（node 为最早的、any 给最早日期），供「等上架」判断。
    """
    today = date.today()
    out: dict = {}
    for acc_cache in (_load(DATA / "linxing-stock.json").get("items") or []):
        code = str(acc_cache.get("code") or "").strip()
        var = str(acc_cache.get("variant") or "").strip()
        if not code:
            continue
        k = ("%s-%s" % (code, var)).upper()
        size = str(acc_cache.get("size") or "").upper()
        prev = out.setdefault((k, size), {"transit": 0.0})
        prev["transit"] = max(prev.get("transit") or 0, float(acc_cache.get("transit") or 0))
    for row in (_load(DATA / "shipment-track.json").get("rows") or []):
        shelf = str(row.get("predictedShelf") or "")[:10]
        if not shelf:
            continue
        try:
            d = datetime.strptime(shelf, "%Y-%m-%d").date()
        except ValueError:
            continue
        acc = str(row.get("account") or "")
        info = str(((row.get("logistics") or {}).get("latest") or {}).get("info") or "")
        ok = node_ok(acc, info)
        for sk in row.get("skus") or []:
            name = str(sk.get("productName") or sk.get("style") or "")
            for pre in ("BS2-", "BH2-", "BH-", "BS-", "BEL-", "BHY-", "BK-", "BLA-"):
                if name.upper().startswith(pre):
                    name = name[len(pre):]
                    break
            if "-" not in name:
                continue
            base, size = name.rsplit("-", 1)
            key = (base.upper(), size.upper())
            cur = out.get(key) or {"transit": 0.0}
            cand = {"shelf": d, "node": info[-60:], "account": acc,
                    "channel": row.get("channel") or "", "ok": ok}
            if d < today:
                continue
            anyd = cur.get("any")
            if anyd is None or d < anyd:
                cur["any"] = d
            if ok and (cur.get("okShelf") is None or d < cur["okShelf"]):
                cur["okShelf"] = d
                cur["okNode"] = cand["node"]
                cur["okAccount"] = acc
                cur["okChannel"] = cand["channel"]
            if not cur.get("node") or (ok and not cur.get("ok")):
                cur["node"] = cand["node"]
                cur["account"] = acc
                cur["channel"] = cand["channel"]
                cur["ok"] = ok
            out[key] = cur
    return out


def _avail_daily() -> tuple[dict, dict]:
    """(款-套系大写, 尺码) → 可用件数 / 7天日均。

    口径（与 presale_plan v3 一致，用户 2026-11-12 定）：
        可用 = 恒信领星可用 + 帕沃尔现货；日均 = 领星 7 天日均
    goods.json 里没有 avail/daily 字段（只有平台库存），所以必须从这两份数据算。
    """
    avail: dict = {}
    daily: dict = {}
    for x in (_load(DATA / "linxing-stock.json").get("items") or []):
        code = str(x.get("code") or "").strip()
        var = str(x.get("variant") or "").strip()
        size = str(x.get("size") or "").upper()
        if not code or not size:
            continue
        k = (("%s-%s" % (code, var)).upper(), size)
        avail[k] = avail.get(k, 0.0) + float(x.get("avail") or 0)
        daily[k] = max(daily.get(k, 0.0), float(x.get("daily") or 0))
    for x in (_load(DATA / "pw-stock.json").get("items") or []):
        code = str(x.get("code") or "").strip()
        var = str(x.get("variant") or "").strip()
        size = str(x.get("size") or "").upper()
        if not code or not size:
            continue
        k = (("%s-%s" % (code, var)).upper(), size)
        avail[k] = avail.get(k, 0.0) + float(x.get("stock") or 0)
    return avail, daily


def build(stores=("BEL", "BHY", "KinglamanSport"), mult: float = QTY_MULT) -> list:
    goods = _load(DATA / "goods.json")
    tmap = transit_index()
    av_map, dl_map = _avail_daily()
    out = []
    today = date.today()
    for st in stores:
        for it in ((goods.get("stores") or {}).get(st) or {}).get("items") or []:
            if it.get("status") != "在售中":
                continue
            code = str(it.get("code") or "")
            base = code.split("-", 1)[1] if "-" in code else code
            for sku in it.get("skus") or []:
                size = str(sku.get("size") or "").upper()
                k = (base.upper(), size)
                t = tmap.get(k) or {}
                avail = float(av_map.get(k) or 0)
                daily = float(dl_map.get(k) or 0)
                if daily <= 0:
                    continue
                d0 = avail / daily
                if d0 > D0_MAX:
                    continue
                shelf = t.get("okShelf") or t.get("any")
                days = (shelf - today).days if shelf else None
                transit = float(t.get("transit") or 0)
                node_go = bool(t.get("okShelf"))
                if d0 <= D0_MAX and node_go and days is not None and days <= SHELF_MAX:
                    action = "开预售"
                elif d0 <= D0_MAX and transit > 0:
                    action = "只清零"
                elif d0 <= D0_MAX:
                    action = "只清零" if float(sku.get("stock") or 0) > 0 else "不动"
                else:
                    continue
                qty = 0
                if action == "开预售":
                    want = max(QTY_MIN, math.ceil(daily * mult))
                    qty = int(min(transit, want)) if transit > 0 else want
                reason = []
                reason.append("可售 %.2f 天（≤%g）" % (d0, D0_MAX))
                if t.get("okShelf"):
                    reason.append("上架 %s（%d 天，≤%g）" % (shelf, days, SHELF_MAX))
                    reason.append("节点「%s」" % (t.get("okNode") or ""))
                else:
                    reason.append("上架 %s（%s 天）" % (shelf or "无", days))
                    reason.append("节点不合格「%s」" % (t.get("node") or "无在途"))
                out.append({
                    "store": st, "code": code, "size": size, "spu": it.get("spuId"),
                    "temuStock": sku.get("stock"), "avail": round(avail, 1),
                    "daily": round(daily, 2), "d0": round(d0, 2),
                    "transit": int(transit), "shelfDate": str(shelf or ""),
                    "shelfDays": days, "node": t.get("node") or "",
                    "nodeOk": node_go, "account": t.get("account") or "",
                    "channel": t.get("channel") or "",
                    "action": action, "qty": qty,
                    "reason": "；".join(reason),
                })
    out.sort(key=lambda x: ({"开预售": 0, "只清零": 1, "不动": 2}.get(x["action"], 9),
                            x["store"], x["d0"], x["code"], x["size"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="v4：可售≤3天 + 上架≤6天 + 节点合格 才开预售")
    ap.add_argument("--mult", type=float, default=QTY_MULT, help="在途量＝日均 × 这个倍数（默认 2）")
    ap.add_argument("--json", default="", help="把结果写到这个 json")
    ap.add_argument("--stores", default="BEL,BHY,KinglamanSport")
    a = ap.parse_args()
    rows = build(stores=tuple(s.strip() for s in a.stores.split(",") if s.strip()), mult=a.mult)
    pre = [r for r in rows if r["action"] == "开预售"]
    z = [r for r in rows if r["action"] == "只清零"]
    print("可售≤3天共 %d 条：开预售 %d / 只清零 %d / 不动 %d"
          % (len(rows), len(pre), len(z), len([r for r in rows if r["action"] == "不动"])))
    for r in pre + z:
        print("  %-5s %-24s %-4s 线上%-5s 在库%-6s天 日均%-6s 在途%-5s 上架%-11s(%s天) %s → %s%s"
              % (r["store"], r["code"], r["size"], r["temuStock"], r["d0"], r["daily"],
                 r["transit"], r["shelfDate"] or "-", r["shelfDays"], r["account"] or "-",
                 r["action"], ("（预售 %d）" % r["qty"]) if r["action"] == "开预售" else ""))
    if a.json:
        Path(a.json).write_text(json.dumps({"generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            "rule": {"d0Max": D0_MAX, "shelfMax": SHELF_MAX,
                                                     "mult": a.mult, "okNode": OK_NODE},
                                            "items": rows}, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print("已写入:", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
