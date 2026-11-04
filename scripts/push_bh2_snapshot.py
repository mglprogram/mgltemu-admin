# -*- coding: utf-8 -*-
"""push_bh2_snapshot.py — 把「BH-2 开头」几款产品的库存快照推到 TEMU 机器人群。

用户 2026-11-04 的要求
----------------------
> 这三张图中 BH-2 开头的产品，你在 12 月 23 日设个定时任务在那天的 10 点
> 推送到我飞书的 temu 群（以后默认推送这个机器群，那个飞书应用作为 dsh 跟飞书的桥接）

为什么是**快照**而不是实时抓
----------------------------
截图里的 SKU 带 `TM-BH-` 前缀，本地任何数据源里都查不到：

    TM-BH-2072-4SetA-S      本地只有 BS2-2072-4SetA-XL，没有 -S
    TM-BH-207213-3SetA-*    本地是 207213-**4SetA**，没有 3SetA
    TM-BH-T3033-3BHL-*      本地是 **BE**-T3033-3BHL-XS/S（BE 不是 BH）
    TM-BH-202102-5SetA-S    本地有 BH2-/BS-/BS2- 三种前缀，没有 TM-BH-

⇒ 当天没法重抓。用户明确选了「就按三张图上的数字发（快照）」，
   数字含义是**库存数量**。所以数据放在 `scripts/payloads/bh2_stock_snapshot.json`，
   本脚本只负责把它排版成消息发到群里 —— 改数字不用改代码。

通道
----
走 `notify_push.push_group()`（群机器人 webhook），**不是**应用机器人私聊。
用户口径：以后默认推机器人群；飞书应用只作为 DSH 的桥接用。

用法
----
    python scripts\\push_bh2_snapshot.py              # 真发到 TEMU 机器人群
    python scripts\\push_bh2_snapshot.py --dry-run    # 只打印，不发
    python scripts\\push_bh2_snapshot.py --file 别的快照.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

DEFAULT_SNAP = HERE / "payloads" / "bh2_stock_snapshot.json"

#: 面板地址（收到消息点开就能对账）
PANEL_URL = "https://mgltemu.us.ci/#/temu/on-sale"

try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                   # noqa: BLE001
    pass


def _n(v) -> str:
    """数字 → 人话（0 单独标出来，因为它意味着断货）。"""
    try:
        i = int(v)
    except (TypeError, ValueError):
        return str(v)
    return "0（断货）" if i == 0 else str(i)


def build_message(snap: dict, today: str = "") -> str:
    """快照 → 群消息正文。纯文本，不带 markdown（群机器人不解析）。"""
    goods = snap.get("商品") or []
    # 「截图是哪天拍的」和「哪天推送」是两件事：12/23 推的是 11/04 的数字。
    # 混在一起写会让人以为当天刚查过库存 —— 那正是快照最容易骗人的地方。
    shot_at = snap.get("截图日期") or snap.get("推送时间") or ""
    lines = ["【TEMU BH-2 库存提醒】%s" % (today or date.today().isoformat()), ""]
    lines.append("这几款该看库存了（共 %d 个尺码）：" % len(goods))
    lines.append("")

    # 按款号分组，同一款的不同尺码排在一起 —— 逐行刷下来看不出结构
    groups: dict[str, list] = {}
    for g in goods:
        sku = str(g.get("sku") or "")
        # `TM-BH-207213-3SetA-M` → 款 = `TM-BH-207213-3SetA`
        style = sku.rsplit("-", 1)[0] if "-" in sku else sku
        groups.setdefault(style, []).append(g)

    for style, items in groups.items():
        lines.append(style)
        for g in items:
            sku = str(g.get("sku") or "")
            size = sku.rsplit("-", 1)[-1] if "-" in sku else ""
            lines.append("    %-5s 库存 %s" % (size, _n(g.get("库存"))))
        lines.append("")

    zero = [g for g in goods if str(g.get("库存")).strip() in ("0", "0.0")]
    if zero:
        lines.append("注意：%s 已经是 0 库存。" % "、".join(g["sku"] for g in zero))
        lines.append("")

    if shot_at:
        lines.append("（库存快照取自 %s 的截图，不是当天实时数据）" % shot_at)
    lines.append("明细：%s" % PANEL_URL)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="推送 BH-2 库存快照到 TEMU 机器人群")
    ap.add_argument("--file", default=str(DEFAULT_SNAP), help="快照 JSON 路径")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不发")
    a = ap.parse_args()

    p = Path(a.file)
    if not p.is_file():
        print("!! 找不到快照文件 %s" % p, file=sys.stderr)
        return 2
    try:
        snap = json.loads(io.open(p, encoding="utf-8-sig").read())
    except Exception as e:                                    # noqa: BLE001
        print("!! 快照文件读不出来：%s" % e, file=sys.stderr)
        return 2

    goods = snap.get("商品") or []
    if not goods:
        print("!! 快照里没有「商品」，不发空消息", file=sys.stderr)
        return 2

    text = build_message(snap)
    if a.dry_run:
        print(text)
        print("\n--dry-run：未发送")
        return 0

    try:
        import notify_push as NP                              # noqa: PLC0415
    except Exception as e:                                    # noqa: BLE001
        print("!! notify_push 不可用（不会退化成私聊）：%s" % e, file=sys.stderr)
        return 2

    ok, why = NP.push_group("TEMU BH-2 库存提醒", text)
    print("[push] %s → %s" % ("OK" if ok else "FAIL", why))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
