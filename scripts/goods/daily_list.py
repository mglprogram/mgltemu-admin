# -*- coding: utf-8 -*-
"""daily_list.py — 每天日更后生成「可加库存 / 可开预售」清单（**只出清单，不做任何写操作**）。

用户 2026-10-13 拍板：写操作停掉，每天更新完在售库存后给一份清单即可。

输出
----
· `_logs/goods-daily-list/清单_YYYY-MM-DD.md`  —— 给人看的（按店分组、按加量降序）
· 同名 `.txt`                                  —— 便于直接粘贴/推送
· 控制台同时打印

清单里每行都是「照着点就能加」的粒度：货号 + 尺码 + TEMU现有 + 加州+kansas现货 + 建议加量，
另外单独列出「可开预售」（仓内无货但 7 天内有到货）与「需补货/待到货确认」供参考。

用法
----
    python scripts/goods/daily_list.py                 # 生成今天的清单
    python scripts/goods/daily_list.py --date 2026-10-13
    python scripts/goods/daily_list.py --push          # 额外推一份到飞书（若配好通知）
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOODS = ROOT / "temu-admin" / "public" / "data" / "goods.json"
CFG = ROOT / "scripts" / "power" / "data" / "temu_restock.json"
OUTDIR = ROOT / "_logs" / "goods-daily-list"


def load() -> tuple[dict, dict]:
    d = json.loads(GOODS.read_text(encoding="utf-8-sig"))
    cfg = {}
    try:
        cfg = json.loads(CFG.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        pass
    return d, cfg


def used_stock(row: dict, keys: list) -> int:
    """该行的「加州+kansas」现货（加量依据）。"""
    byw = [w for w in (row.get("whByWarehouse") or []) if w.get("仓库")]
    return int(sum(w.get("代发库存") or 0 for w in byw
                   if any(k in (w.get("仓库") or "") for k in keys)))


def build(d: dict, cfg: dict) -> dict:
    keys = ((cfg.get("加库存") or {}).get("仓库关键字") or ["加州", "kansas"])
    if isinstance(keys, str):
        keys = [keys]
    rows = (d.get("lowStock") or {}).get("rows") or []
    out = {"keys": keys, "stores": OrderedDict()}
    for r in rows:
        v = (r.get("verdict") or "").strip()
        if v not in ("可加库存", "可开预售", "需补货", "待到货确认"):
            continue
        st = r.get("store") or "?"
        s = out["stores"].setdefault(st, {"可加库存": [], "可开预售": [],
                                          "需补货": [], "待到货确认": []})
        s[v].append({
            "code": r.get("code"), "size": r.get("size"), "stock": r.get("stock"),
            "used": used_stock(r, keys), "add": r.get("suggestAdd") or 0,
            "arrival": r.get("arrivalDate") or "",
            "days": r.get("daysToArrival"),
            "inbound": r.get("inboundConfirmed") or 0,
            "title": (r.get("title") or "")[:44],
        })
    for s in out["stores"].values():
        s["可加库存"].sort(key=lambda x: (-x["add"], x["code"], x["size"]))
    return out


def render(d: dict, info: dict, day: str, keys: list) -> str:
    rc = ((d.get("lowStock") or {}).get("restock") or {})
    sumry = ((d.get("lowStock") or {}).get("summary") or {})
    L = []
    L.append("# TEMU 在售款 · 今日清单（%s）" % day)
    L.append("")
    L.append("数据生成 %s ｜ 判定 %s"
             % (d.get("generatedAt"), d.get("restockGeneratedAt")))
    src = (rc.get("sources") or {})
    L.append("看板快照 %s ｜ 飞书到货表 %s"
             % ((src.get("看板") or {}).get("builtAt"), (src.get("飞书") or {}).get("fetchedAt")))
    L.append("")
    L.append("口径：TEMU 可售 **< %s** 才算低库存（严格小于）；加量依据 = **%s 现货之和**"
             "（≥150 加 30，否则 ×20%%、不足 3 按 3，且不超过该合计）；"
             "加库存动作只落加州仓。"
             % (((d.get("lowStock") or {}).get("threshold")), "+".join(keys)))
    L.append("")
    L.append("**总览**：在售中 BEL %s / BHY %s SKC ｜ 低库存 %s 款·尺码（%s 个款）｜ "
             "0 库存 %s ｜ **可加库存 %s 个尺码 · 建议加 %s 件** ｜ 可开预售 %s"
             % (((d.get("stores") or {}).get("BEL", {}).get("counts") or {}).get("onSaleSkc"),
                ((d.get("stores") or {}).get("BHY", {}).get("counts") or {}).get("onSaleSkc"),
                sumry.get("sku"), sumry.get("style"), sumry.get("zero"),
                rc.get("verdictCounts", {}).get("可加库存"),
                rc.get("suggestAddTotal"),
                rc.get("verdictCounts", {}).get("可开预售")))
    L.append("")
    total_add = 0
    for store, s in info["stores"].items():
        add_items = s["可加库存"]
        if add_items:
            sub = sum(i["add"] for i in add_items)
            total_add += sub
            L.append("## %s 可加库存（%d 个尺码 · 合计加 %d 件）" % (store, len(add_items), sub))
            L.append("")
            L.append("| 货号 | 尺码 | TEMU现有 | %s现货 | 建议加 | 商品 |" % "+".join(keys))
            L.append("|---|---|---|---|---|---|")
            for i in add_items:
                L.append("| %s | %s | %s | %s | **%s** | %s |"
                         % (i["code"], i["size"], i["stock"], i["used"], i["add"], i["title"]))
            L.append("")
        pre = s["可开预售"]
        if pre:
            L.append("## %s 可开预售（%d 个尺码）" % (store, len(pre)))
            L.append("")
            L.append("| 货号 | 尺码 | TEMU现有 | 到货日 | 还有几天 | 到货件数 |")
            L.append("|---|---|---|---|---|---|")
            for i in pre:
                L.append("| %s | %s | %s | %s | %s | %s |"
                         % (i["code"], i["size"], i["stock"], i["arrival"],
                            i["days"] if i["days"] is not None else "—", i["inbound"]))
            L.append("")
        for k, head in (("需补货", "需补货（仓内与在途都没有）"),
                        ("待到货确认", "待到货确认（在途但无到货日）")):
            items = s[k]
            if not items:
                continue
            L.append("## %s %s（%d 个尺码）" % (store, head, len(items)))
            L.append("")
            L.append("| 货号 | 尺码 | TEMU现有 | 到货日 |")
            L.append("|---|---|---|---|")
            for i in items:
                L.append("| %s | %s | %s | %s |"
                         % (i["code"], i["size"], i["stock"], i["arrival"] or "—"))
            L.append("")
    L.append("---")
    L.append("本清单由 `scripts/goods/daily_list.py` 生成，**只读不写**（不会动任何库存）。")
    L.append("面板（可交互、含悬浮逐仓明细）：https://mgltemu.us.ci/#/temu/on-sale?view=low")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成每日「可加库存/可开预售」清单（只读）")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--push", action="store_true",
                    help="额外推一条「可加库存 Top10 + 总数」摘要到飞书")
    ap.add_argument("--receive", default="", help="覆盖收件人（open_id 或 chat_id）")
    ap.add_argument("--chat", action="store_true", help="--receive 是群 chat_id")
    a = ap.parse_args()

    if not GOODS.exists():
        print("!! 找不到面板数据 %s（先跑日更）" % GOODS)
        return 2
    d, cfg = load()
    info = build(d, cfg)
    md = render(d, info, a.date, info["keys"])

    OUTDIR.mkdir(parents=True, exist_ok=True)
    p_md = OUTDIR / ("清单_%s.md" % a.date)
    p_txt = OUTDIR / ("清单_%s.txt" % a.date)
    p_md.write_text(md, encoding="utf-8")
    p_txt.write_text(md.replace("**", "").replace("|", " | "), encoding="utf-8")
    print(md)
    print("清单已写：%s" % p_md)
    print("纯文本：%s" % p_txt)

    if a.push:
        push_summary(d, info, a.date, receive=a.receive, as_chat=a.chat)
    return 0


def push_summary(d: dict, info: dict, day: str, receive: str = "",
                 as_chat: bool = False) -> bool:
    """推一条「可加库存 Top10 + 总数」摘要到飞书（应用机器人私聊）。

    收件人默认取 `scripts/power/data/temu_presale.json → 通知.receive_id`
    （与预售通知同一个 open_id），可用参数覆盖。
    """
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))
        import feishu_bitable as FB  # noqa: E402
    except Exception as e:  # noqa: BLE001
        print("飞书推送失败（库加载）:", str(e)[:120])
        return False

    rid, rtype = receive, "chat_id" if as_chat else "open_id"
    if not rid:
        try:
            pc = json.loads((ROOT / "scripts" / "power" / "data" / "temu_presale.json")
                            .read_text(encoding="utf-8-sig"))
            n = pc.get("通知") or {}
            rid = n.get("receive_id") or ""
            rtype = n.get("receive_id_type") or "open_id"
        except Exception:  # noqa: BLE001
            pass
    if not rid:
        print("飞书推送跳过：没配收件人（--receive 或 temu_presale.json → 通知.receive_id）")
        return False

    rc = ((d.get("lowStock") or {}).get("restock") or {})
    sumry = ((d.get("lowStock") or {}).get("summary") or {})
    flat = []
    for store, s in info["stores"].items():
        for i in s["可加库存"]:
            flat.append((store, i))
    flat.sort(key=lambda x: -x[1]["add"])
    pre = [(st, i) for st, s in info["stores"].items() for i in s["可开预售"]]

    lines = ["**TEMU 在售款 · 今日补货清单（%s）**" % day, ""]
    lines.append("低库存 %s 款·尺码（%s 个款）｜0 库存 %s"
                 % (sumry.get("sku"), sumry.get("style"), sumry.get("zero")))
    lines.append("**可加库存 %s 个尺码 · 建议加 %s 件**"
                 % (rc.get("verdictCounts", {}).get("可加库存"), rc.get("suggestAddTotal")))
    if pre:
        lines.append("可开预售 %d 个尺码（仓内无货但 7 天内有到货）" % len(pre))
    lines.append("")
    lines.append("**可加库存 Top 10**")
    for st, i in flat[:10]:
        lines.append("  %s %s %s ｜ TEMU %s ｜ 加州+kansas %s ｜ **加 %s**"
                     % (st, i["code"], i["size"], i["stock"], i["used"], i["add"]))
    if len(flat) > 10:
        lines.append("  …… 其余 %d 个尺码见清单文件" % (len(flat) - 10))
    if pre:
        lines.append("")
        lines.append("**可开预售**")
        for st, i in pre[:5]:
            lines.append("  %s %s %s ｜ %s 到货 %s 件（%s 天后）"
                         % (st, i["code"], i["size"], i["arrival"],
                            int(i["inbound"] or 0), i["days"]))
    lines.append("")
    lines.append("明细：%s" % "https://mgltemu.us.ci/#/temu/on-sale?view=todo")
    text = "\n".join(lines)

    try:
        res = FB.send_text(rid, text, receive_id_type=rtype)
        ok = bool(res) and (res.get("code") in (0, None))
        print("飞书推送:", "成功 → %s" % rid if ok else "失败 → %s" % str(res)[:160])
        return ok
    except Exception as e:  # noqa: BLE001
        print("飞书推送异常:", str(e)[:160])
        return False


if __name__ == "__main__":
    raise SystemExit(main())
