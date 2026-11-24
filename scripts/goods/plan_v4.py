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
3.1 给多少在途量（★ 2026-11-24 用户改口径，旧「日均×2」作废）：
        `min(在途件数, max(5, ceil(在途件数 ÷ 3)))`
    —— 取该尺码**在途量的 1/3**，向上取整，不够 5 件保底给 5。
3.2 **填在哪一仓**（★ 2026-11-24 用户最终口径，按头程归属，取代「固定填恒信仓」）：
        `加州三仓`  = 帕沃尔库存（头程 account=14450 / 乐航）
        `恒信加州3` = 恒信领星库存（头程 account=LM739）
    ⇒ 这票在途属于哪个货源，预售量就填对应仓，另一仓填 0；**两个仓都要清零**才能开。

只算数、不碰浏览器。
用法：
    python scripts/goods/plan_v4.py                 # 打印今天该开的清单
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

D0_MAX = 3.0          # 可售天数上限（用户：≤3 天）→ 2026-11-19 改成 2 天
D0_MAX_PRE = 2.0      # ★ 2026-11-19 用户：「可售 ≤3 天：改成 <=2 天」
SHELF_MAX = 6.0       # 上架距离上限（用户：≤6 天）
#: ★ 2026-11-24 用户改口径：在途预售填数 = min(在途量, max(5, ceil(在途量 ÷ 3)))
#:   旧口径「日均 × 2」（QTY_MULT）已作废，`--mult` 参数保留但不再参与计算。
QTY_DIV = 3
QTY_MIN = 5
QTY_MULT = 2.0        # 兼容旧命令行参数，已不参与计算

#: ★ 2026-11-24 用户：「BHY 和 KLS 在库存基础上加库存可以，但是不能清零和减到<5」
#:   ⇒ 这两店**禁止清零 / 禁止把库存减到 5 以下**，计划里不出「清零」动作，只出「只加库存」。
#:   实际写后台一律只走 BEL（用户：「只有 BEL 去实打实的去修改数值和开预售」）。
NO_ZERO_STORES = {"BHY", "KinglamanSport", "KLS"}

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
            # ★ 2026-11-19：`node/account/channel` 原本会被「先遇到的票」抢占（哪怕更晚到），
            #   面板上显示的节点就和最近上架日对不上（实测 204203-4SetC-L：any=11-28 却显示
            #   12-07 那票的「开船」）。⇒ 统一取**最早那票**的信息；有合格票时优先用合格票。
            if ok:
                best = cur.get("bestOk")
                if best is None or d < best:
                    cur["bestOk"] = d
                    cur["node"], cur["account"], cur["channel"] = cand["node"], acc, cand["channel"]
                    cur["ok"] = True
            else:
                best = cur.get("bestAny")
                if best is None or d < best:
                    cur["bestAny"] = d
                    if not cur.get("ok"):
                        cur["node"], cur["account"], cur["channel"] = cand["node"], acc, cand["channel"]
                        cur["ok"] = False
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


def _presale15():
    """15 天预售名单（config/temu_presale_15d.json）。用户 2026-11-19：

        「已经开了 15 天预售的那几个款-尺码是不允许去动的，只能加」

    这类 SKU 的**平台库存不能清零、预售开关不能关/改**，所以不进「只清零」，也不进「开预售」，
    单独标成 `不动（已开15天预售）`，留给人工只做加法。
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power"))
        import bel_split_by_stock as B  # noqa: PLC0415
        return B
    except Exception:  # noqa: BLE001
        return None


LOCK15 = _presale15()


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
                if d0 > D0_MAX_PRE:
                    continue
                # ★ 2026-11-19 用户红线：已开 15 天预售的款-尺码「不允许去动，只能加」
                locked = bool(LOCK15 and LOCK15.is_presale15(code, st))
                shelf = t.get("okShelf") or t.get("any")
                days = (shelf - today).days if shelf else None
                transit = float(t.get("transit") or 0)
                node_go = bool(t.get("okShelf"))
                if locked:
                    action = "不动（已开15天预售）"
                elif d0 <= D0_MAX_PRE and node_go and days is not None and days <= SHELF_MAX:
                    action = "开预售"
                elif d0 <= D0_MAX_PRE and transit > 0:
                    action = "只清零"
                elif d0 <= D0_MAX_PRE:
                    action = "只清零" if float(sku.get("stock") or 0) > 0 else "不动"
                else:
                    continue
                # ★ 2026-11-24 用户红线（BHY / KLS 两店）：
                #   「BHY 和 KLS 在库存基础上加库存可以，但是不能清零和减到<5」
                #   ⇒ 这两店**不出清零建议**，一律改成「只加库存」（不写后台，只进面板待办）。
                if action == "只清零" and st in NO_ZERO_STORES:
                    action = "只加库存（该店禁清零）"
                qty = 0
                if action == "开预售":
                    # ★ 2026-11-24 用户改口径：填数 = 该尺码在途量 ÷ 3（向上取整，保底 5）
                    #   旧口径「日均 × 2」作废。QTY_DIV=3、QTY_MIN=5 见文件头常量。
                    want = max(QTY_MIN, math.ceil(transit / QTY_DIV))
                    qty = int(min(transit, want)) if transit > 0 else want
                reason = []
                reason.append("可售 %.2f 天（≤%g）" % (d0, D0_MAX_PRE))
                if locked:
                    reason.append("已开 15 天预售 ⇒ 只能加、不能清不能改")
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
    out.sort(key=lambda x: ({"开预售": 0, "只清零": 1, "只加库存（该店禁清零）": 2,
                             "不动": 3, "不动（已开15天预售）": 4}.get(x["action"], 9),
                            x["store"], x["d0"], x["code"], x["size"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="v4：可售≤2天 + 上架≤6天 + 节点合格 才开预售")
    ap.add_argument("--mult", type=float, default=QTY_MULT,
                    help="[已作废] 旧口径日均倍数；2026-11-24 起改用 在途÷3，此参数不再参与计算")
    ap.add_argument("--json", default="", help="把结果写到这个 json")
    ap.add_argument("--stores", default="BEL,BHY,KinglamanSport")
    a = ap.parse_args()
    rows = build(stores=tuple(s.strip() for s in a.stores.split(",") if s.strip()), mult=a.mult)
    pre = [r for r in rows if r["action"] == "开预售"]
    z = [r for r in rows if r["action"] == "只清零"]
    lock = [r for r in rows if r["action"] == "不动（已开15天预售）"]
    print("可售≤3天共 %d 条：开预售 %d / 只清零 %d / 不动 %d（其中已开15天预售锁定 %d）"
          % (len(rows), len(pre), len(z),
             len([r for r in rows if r["action"] == "不动"]), len(lock)))
    for r in pre + z:
        print("  %-5s %-24s %-4s 线上%-5s 在库%-6s天 日均%-6s 在途%-5s 上架%-11s(%s天) %s → %s%s"
              % (r["store"], r["code"], r["size"], r["temuStock"], r["d0"], r["daily"],
                 r["transit"], r["shelfDate"] or "-", r["shelfDays"], r["account"] or "-",
                 r["action"], ("（预售 %d）" % r["qty"]) if r["action"] == "开预售" else ""))
    if a.json:
        Path(a.json).write_text(json.dumps({"generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                            "rule": {"d0Max": D0_MAX_PRE, "shelfMax": SHELF_MAX,
                                                     "mult": a.mult, "okNode": OK_NODE},
                                            "items": rows}, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print("已写入:", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
